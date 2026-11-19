// What the generator form keeps so that it survives the browser closing the page (owner, 19 Nov: "uploaded photos on
// mobile devices sometimes trigger the page to reload before the image gets processed").
//
// A phone closes a page it is short of memory for, most often while the camera or the photo picker is on top of the
// browser: Android kills the browser's page process, iOS the Safari one. No event fires; the page is simply loaded
// again when the person comes back, empty, and the photo they chose has nowhere to go. A page can't prevent that. So:
//   - the form (prompt and settings) is written to localStorage whenever the page is hidden and just before a picker
//     opens, and removed when the page is left normally (pagehide fires for a reload or a close, never for a kill);
//   - the photos already chosen are kept in IndexedDB;
//   - a page that loads and finds a form left behind by a page that is no longer alive puts it all back, says so, and
//     tells the server (one log line: routes/generate_api.py page_restored).
// A photo being chosen at the moment the page was closed is lost: the browser never hands it over.
import { openDB } from 'idb';
import { jsonInit, send } from '@/shared/http/client';

const DB_NAME = 'camera-cache';
const STORE = 'photos';
const PICKED = 'picked';
const KEY = 'sm_generator_form';
const FRESH_MS = 15 * 60_000;

export interface UiSnapshot {
  prompt?: string;
  selectedWorkflow?: string;
  selectedRefiner?: string;
  selectedPublish?: string;
  workflowParams?: Record<string, any>;
  refinerParams?: Record<string, any>;
  globalParams?: Record<string, any>;
  referenceUrls?: string[];
}

export type Picker = 'gallery' | 'camera';

export interface ClosedPage {
  t: number;
  form: UiSnapshot;
  /** photos the form held */
  photos: number;
  /** the picker that was open when the page was last seen, and since when */
  picking?: Picker;
  pickingSince?: number;
  /** held by the page that wrote this for as long as it lives */
  lock?: string;
}

// This page holds a Web Lock for as long as it lives, so another tab can tell a form left by a dead page from the
// form of a live one in the background.
const LOCK = `${KEY}_${Math.random().toString(36).slice(2)}`;
try {
  navigator.locks?.request(LOCK, () => new Promise(() => {}));
} catch {
  // no Web Locks: a left-behind form is taken as a dead page's
}

let readForm: (() => UiSnapshot) | null = null;
let photoCount = 0;
let picking: { how: Picker; since: number } | null = null;
let wrote = false;
let taken = false;
let leaving = false;   // between pagehide and the page going (or coming back from the back/forward cache)

function save(): void {
  if (!readForm || leaving) return;
  const form = readForm();
  if (!picking && !photoCount && !(form.prompt || '').trim()) {
    drop();
    return;
  }
  const page: ClosedPage = {
    t: Date.now(), form, photos: photoCount, picking: picking?.how, pickingSince: picking?.since, lock: LOCK,
  };
  try {
    localStorage.setItem(KEY, JSON.stringify(page));
    wrote = true;
  } catch {
    // no storage (private mode, full): nothing is kept
  }
}

function drop(): void {
  if (!wrote) return;
  try {
    localStorage.removeItem(KEY);
  } catch {
    // as above
  }
  wrote = false;
}

/** Keep `read()`'s form while the page is hidden. Returns the undo. */
export function watchForm(read: () => UiSnapshot): () => void {
  readForm = read;
  const onVisibility = () => {
    if (document.visibilityState === 'hidden') {
      save();
    } else if (picking) {
      // back on screen and alive: the picker has closed, whether or not it hands over a photo
      const open = picking;
      setTimeout(() => {
        if (picking === open) pickerClosed();
      }, 5000);
    }
  };
  // a reload or a close: pagehide comes first, then the page is hidden too, which must not write the form back
  const onPageHide = () => {
    leaving = true;
    drop();
  };
  const onPageShow = () => {
    leaving = false;
  };
  document.addEventListener('visibilitychange', onVisibility);
  window.addEventListener('pagehide', onPageHide);
  window.addEventListener('pageshow', onPageShow);
  return () => {
    document.removeEventListener('visibilitychange', onVisibility);
    window.removeEventListener('pagehide', onPageHide);
    window.removeEventListener('pageshow', onPageShow);
    drop();
    if (readForm === read) readForm = null;
  };
}

/** Call just before the camera or the photo picker opens. */
export function pickerOpened(how: Picker): void {
  picking = { how, since: Date.now() };
  save();
}

export function pickerClosed(): void {
  if (!picking) return;
  picking = null;
  save();
}

async function photosDb() {
  return openDB(DB_NAME, 1, {
    upgrade(db) {
      db.createObjectStore(STORE);
    },
  });
}

/** The photos the form holds now (none: forget them). */
export async function keepPhotos(files: File[]): Promise<void> {
  try {
    const db = await photosDb();
    if (files.length) {
      await db.put(STORE, files.map(f => ({ name: f.name, type: f.type, lastModified: f.lastModified, blob: f })), PICKED);
    } else {
      await db.delete(STORE, PICKED);
    }
    photoCount = files.length;
  } catch {
    photoCount = 0;
  }
  if (files.length || wrote) save();
}

async function keptPhotos(): Promise<File[]> {
  try {
    const kept = await (await photosDb()).get(STORE, PICKED);
    return (kept || []).map((p: { name: string; type: string; lastModified: number; blob: Blob }) =>
      new File([p.blob], p.name, { type: p.type, lastModified: p.lastModified }));
  } catch {
    return [];
  }
}

/**
 * Once per page load: the form (and photos) of a page the browser closed, if there is one from the last 15 minutes.
 * Anything older, or nothing, clears the kept photos.
 */
export async function takeClosedPage(): Promise<{ page: ClosedPage; photos: File[] } | null> {
  if (taken) return null;
  taken = true;
  let page: ClosedPage | null = null;
  try {
    page = JSON.parse(localStorage.getItem(KEY) || 'null');
  } catch {
    page = null;
  }
  if (page?.lock && navigator.locks?.query) {
    try {
      const { held } = await navigator.locks.query();
      if (held?.some(l => l.name === page!.lock)) return null; // its page is alive in another tab: not ours to take
    } catch {
      // can't tell: take it
    }
  }
  try {
    localStorage.removeItem(KEY);
  } catch {
    // no storage
  }
  if (!page || !page.form || Date.now() - page.t > FRESH_MS) {
    keepPhotos([]);
    return null;
  }
  return { page, photos: page.photos ? await keptPhotos() : [] };
}

/** One line in the server log, so how often this happens, and at which step, can be read there. */
export function reportRestored(page: ClosedPage, photosRestored: number): void {
  const nav = performance.getEntriesByType?.('navigation')[0] as PerformanceNavigationTiming | undefined;
  const note = {
    picking: page.picking || null,
    picker_open_s: page.pickingSince ? Math.round((page.t - page.pickingSince) / 1000) : null,
    closed_for_s: Math.round((Date.now() - page.t) / 1000),
    photos_held: page.photos,
    photos_restored: photosRestored,
    had_prompt: !!(page.form.prompt || '').trim(),
    discarded: (document as any).wasDiscarded ?? null,
    navigation: nav?.type ?? null,
    device_memory_gb: (navigator as any).deviceMemory ?? null,
  };
  send('/generate/page-restored', jsonInit('POST', note)).catch(() => {});
}
