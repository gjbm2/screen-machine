// The generator's Film mode (docs/FILM_MODE.md, stage 1): a row of slots holding pictures and, between each pair, what
// happens and how many seconds. Each pair of filled neighbours is one video, drawn from the first picture to the
// second. Pure, so it is tested without a browser:
//   node --experimental-strip-types --test src/components/film/film.test.ts

/** A slot's picture: one the app already has (a link to it), or a file from the person's device. */
export type SlotPicture =
  | { kind: 'url'; url: string }
  | { kind: 'file'; file: File; preview: string };

export interface Slot {
  id: string;
  picture: SlotPicture | null;
  /** width / height of the picture, once it has loaded */
  aspect?: number;
}

/** What happens between a slot and the next one. */
export interface Link {
  text: string;
  seconds: number;
}

/** links[i] runs from slots[i] to slots[i + 1], so there is always one link fewer than slots. */
export interface Film {
  slots: Slot[];
  links: Link[];
}

/** One video to make. */
export interface Segment {
  /** the link it is made from */
  index: number;
  from: SlotPicture;
  to: SlotPicture;
  text: string;
  seconds: number;
}

// the model draws one pass of up to 10 s to a last picture (routes/generate.py SEGMENT_FRAMES)
export const MIN_SECONDS = 2;
export const MAX_SECONDS = 10;
export const DEFAULT_SECONDS = 5;
export const FILM_WORKFLOW = 'h3-film.json';

let made = 0;
const newId = () => `s${Date.now().toString(36)}${(made++).toString(36)}`;

const newSlot = (picture: SlotPicture | null = null): Slot => ({ id: newId(), picture });
const newLink = (): Link => ({ text: '', seconds: DEFAULT_SECONDS });

export function emptyFilm(slots = 2): Film {
  const n = Math.max(2, slots);
  return { slots: Array.from({ length: n }, () => newSlot()), links: Array.from({ length: n - 1 }, newLink) };
}

export const slotLetter = (i: number): string => (i < 26 ? String.fromCharCode(65 + i) : `${i + 1}`);

export const clampSeconds = (s: number): number =>
  Math.min(MAX_SECONDS, Math.max(MIN_SECONDS, Math.round(Number.isFinite(s) ? s : DEFAULT_SECONDS)));

export function addSlot(film: Film, picture: SlotPicture | null = null): Film {
  return { slots: [...film.slots, newSlot(picture)], links: [...film.links, newLink()] };
}

/** Take a slot out. The link into it goes too (the last slot), else the link out of it; never fewer than two slots:
 *  then it is only emptied. */
export function removeSlot(film: Film, i: number): Film {
  if (i < 0 || i >= film.slots.length) return film;
  if (film.slots.length <= 2) return setPicture(film, i, null);
  const gone = Math.min(i, film.links.length - 1);
  return { slots: film.slots.filter((_, k) => k !== i), links: film.links.filter((_, k) => k !== gone) };
}

export function setPicture(film: Film, i: number, picture: SlotPicture | null, aspect?: number): Film {
  return { ...film, slots: film.slots.map((s, k) => (k === i ? { ...s, picture, aspect } : s)) };
}

export function setAspect(film: Film, id: string, aspect: number): Film {
  return { ...film, slots: film.slots.map((s) => (s.id === id ? { ...s, aspect } : s)) };
}

export function setLink(film: Film, i: number, change: Partial<Link>): Film {
  return {
    ...film,
    links: film.links.map((l, k) =>
      k === i ? { ...l, ...change, seconds: clampSeconds(change.seconds ?? l.seconds) } : l),
  };
}

/** Put pictures in from slot `at` on, one per slot, adding slots at the end as they run out. */
export function placePictures(film: Film, at: number, pictures: SlotPicture[]): Film {
  let next = film;
  pictures.forEach((p, k) => {
    while (at + k >= next.slots.length) next = addSlot(next);
    next = setPicture(next, at + k, p);
  });
  return next;
}

/** Where a picture sent to "the film" (not to a slot) goes: the first empty slot, else a new one at the end. */
export function addPicture(film: Film, picture: SlotPicture): Film {
  const empty = film.slots.findIndex((s) => !s.picture);
  return empty >= 0 ? setPicture(film, empty, picture) : addSlot(film, picture);
}

/** The videos to make: one for each pair of filled neighbours. */
export function segments(film: Film): Segment[] {
  const out: Segment[] = [];
  film.links.forEach((link, i) => {
    const from = film.slots[i]?.picture;
    const to = film.slots[i + 1]?.picture;
    if (from && to) out.push({ index: i, from, to, text: link.text.trim(), seconds: clampSeconds(link.seconds) });
  });
  return out;
}

/** The film's shape: its first picture's (every segment is drawn in it). */
export function filmAspect(film: Film): number | undefined {
  return film.slots.find((s) => s.picture && s.aspect)?.aspect;
}

// How long one video takes to make on a warm GPU: a fixed part (the prompt, loading, saving) and a part per second of
// video (23 Nov on a PRO 6000: 45 s for 2.3 s of video, 76 s for 3 s, about 100 s for 5 s). Waiting for a GPU to
// start is left out, as everywhere in the app (docs/JOBS.md).
const MAKE_BASE_S = 30;
const MAKE_PER_SECOND_S = 15;
/** Videos made side by side (the video endpoint's workers). */
export const WORKERS = 3;

/** About how long the film's videos take, made WORKERS at a time; 0 with nothing to make. */
export function estimateSeconds(film: Film): number {
  const each = segments(film).map((s) => MAKE_BASE_S + MAKE_PER_SECOND_S * s.seconds);
  let total = 0;
  for (let i = 0; i < each.length; i += WORKERS) total += Math.max(...each.slice(i, i + WORKERS));
  return total;
}

/** A segment's pictures as the generate request takes them: files and links apart, and the order they came in. */
export function requestPictures(seg: Segment): { files: File[]; urls: string[]; order: Array<'url' | 'file'> } {
  const files: File[] = [];
  const urls: string[] = [];
  const order = [seg.from, seg.to].map((p) => {
    if (p.kind === 'file') files.push(p.file);
    else urls.push(p.url);
    return p.kind;
  });
  return { files, urls, order };
}

/** A film from its pictures (links to them; null for an empty slot) and the links between them (Start again). */
export function filmOf(pictures: Array<string | null>, links: Array<Partial<Link>>): Film {
  const n = Math.max(2, pictures.length, links.length + 1);
  return {
    slots: Array.from({ length: n }, (_, i) => newSlot(pictures[i] ? { kind: 'url', url: pictures[i] as string } : null)),
    links: Array.from({ length: n - 1 }, (_, i) => ({
      text: typeof links[i]?.text === 'string' ? (links[i].text as string) : '',
      seconds: clampSeconds(Number(links[i]?.seconds ?? DEFAULT_SECONDS)),
    })),
  };
}

// --- kept across a reload: the links, and the pictures the app already has (a file from the device can't be kept)

interface Stored {
  slots: Array<{ url: string | null }>;
  links: Link[];
}

export function toStored(film: Film): Stored {
  return {
    slots: film.slots.map((s) => ({ url: s.picture?.kind === 'url' ? s.picture.url : null })),
    links: film.links.map((l) => ({ text: l.text, seconds: l.seconds })),
  };
}

export function fromStored(value: unknown): Film | null {
  const v = value as Stored | null;
  if (!v || !Array.isArray(v.slots) || !Array.isArray(v.links) || v.slots.length < 2 || v.links.length !== v.slots.length - 1) {
    return null;
  }
  let film: Film = {
    slots: v.slots.map((s) => newSlot(typeof s?.url === 'string' && s.url ? { kind: 'url', url: s.url } : null)),
    links: v.links.map((l) => ({ text: typeof l?.text === 'string' ? l.text : '', seconds: clampSeconds(Number(l?.seconds)) })),
  };
  // empty slots at the end are not kept (down to the two a film starts with), unless something was written on the
  // way into them
  while (film.slots.length > 2 && !film.slots[film.slots.length - 1].picture && !film.links[film.links.length - 1].text.trim()) {
    film = removeSlot(film, film.slots.length - 1);
  }
  return film;
}
