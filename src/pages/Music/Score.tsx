// A piece's score on /music (owner, 23 Oct): the whole piece written out from its MIDI by the live score's own
// generator (show_service/score_sheet.py), no animation, to scroll through or read a page at a time. Opened from a
// piece's ⋯ (Score) or from Now playing; the address carries it (?score=<piece id>), so Back closes it.
//
// The first time a piece's score is opened it is written by the quick pass (a second or two). "Better score" asks for the
// slower neural pass (a minute or two for a long piece): the quick score stays up meanwhile, with how far it has got,
// asked again every 1.5 s (which keeps the server writing it; paused while the tab is hidden); once it exists it is the
// one shown, and Quick / Better switch between them. Pages are SVG images laid out server side for exactly this screen: the score area's
// width by the height left below the controls (less the page turner's row), at the chosen staff size (A− / A+). Scroll
// shows every page down the screen; Pages shows one at a time (‹ ›, the arrow keys, a swipe, or a tap on the page's
// left or right third). Both use the same pages, so switching is instant. PDF opens the whole score laid out for
// paper (A4 portrait unless another paper is chosen) in a new tab, to print or keep.
//
// Your own pieces (uploads and recordings) have a pencil by the title: change the title and composer in place (the PDF
// prints them).
//
// The music stand (owner, 23 Oct: "it should be possible for a pianist to PLAY the score from e.g. an iPad screen"):
// "Play from the score", or a tap on the score (Scroll: any page; Pages: the middle third), opens the score full screen
// on white paper, laid out for exactly the screen below its slim bar, again whenever the screen turns. Landscape and wide
// (an iPad on its side) shows two pages side by side. A tap on the right two thirds turns forward, the left third back;
// a swipe; the arrow and page keys, Space and Enter (Bluetooth page-turner pedals). The screen is kept awake while it's
// open. Scroll in the stand stacks the pages at the screen's width instead. The stand's mode and each piece's place are
// kept for the tab's session, so closing and opening again comes back to the same page.

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { ArrowLeft, Check, ChevronLeft, ChevronRight, FileDown, Maximize2, Pencil, RotateCcw, Sparkles, X } from "lucide-react";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { RoomApiError, storageGet, storageSet } from "@/pages/Room/api";
import { Spinner } from "@/pages/Room/bits";
import { CARD_CLASS, TAP_BUTTON_CLASS, THEME_CLASS } from "@/pages/Room/theme";
import { isUnreached, musicWords, percent, SCORE_PAPERS, type ScoreKind, type ScorePaper, type ScoreSize, type ScoreStatus } from "./api";
import { CHIP, CHIP_OFF, CHIP_ON, PRIMARY, SECONDARY } from "./bits";
import { useMusic } from "./context";

type Mode = "scroll" | "pages";

const MODE_KEY = "music.score.mode";
const STAFF_KEY = "music.score.staff";
const PAPER_KEY = "music.score.paper";
/** The stand's own mode, and each piece's place in it (a fraction of the way through), for this tab's session. */
const STAND_MODE_KEY = "music.stand.mode";
const standAtKey = (id: string) => `music.stand.at.${id}`;
/** A staff's height in css px, smallest to largest. */
const STAFFS = [26, 32, 40, 50, 62];
const POLL_MS = 1500;
/** Nothing answered: ask again this often (the words say the page keeps trying). */
const RETRY_MS = 3000;
const RESIZE_MS = 300;
/** The stand lays out again this long after the screen stops changing (a rotation, fullscreen coming or going). */
const STAND_RESIZE_MS = 250;
/** The page turner's row under a page (kept in both modes, so switching doesn't lay the score out again). */
const PAGER_H = 52;
const MIN_H = 420;

const PAGE_CLASS = "block h-auto w-full rounded-sm border border-stone-200 bg-white shadow-sm";
const ROUND =
  "grid h-11 w-11 shrink-0 place-items-center rounded-full border border-stone-300 bg-white text-stone-700 hover:bg-stone-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-40 disabled:hover:bg-white";
const ICON_BUTTON =
  "grid h-11 w-11 shrink-0 place-items-center rounded-full text-stone-700 hover:bg-stone-200/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
/** The stand's bar: quiet, 44 px targets. */
const BAR_BUTTON =
  "grid h-11 min-w-11 shrink-0 place-items-center rounded-full px-1.5 text-[15px] font-medium text-stone-600 hover:bg-stone-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-35 disabled:hover:bg-transparent";
const FIELD = "h-11 rounded-xl bg-white text-base md:text-base";

const tens = (x: number) => Math.max(0, Math.floor(x / 10) * 10);

/** A short tag of some words (the PDF's address changes when the names it prints do). */
function tag(s: string): string {
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = ((h * 33) ^ s.charCodeAt(i)) >>> 0;
  return h.toString(36);
}

function storedStaff(): number {
  const s = Number(storageGet(STAFF_KEY));
  return STAFFS.includes(s) ? s : window.innerWidth < 640 ? 32 : 40;
}

function sessionGet(key: string): string | null {
  try {
    return sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function sessionSet(key: string, value: string): void {
  try {
    sessionStorage.setItem(key, value);
  } catch {
    /* ignore */
  }
}

/** Where a piece was left in the stand this session (0..1), if anywhere. */
function standAt(pieceId: string): number | null {
  const s = sessionGet(standAtKey(pieceId));
  const f = s === null ? NaN : Number(s);
  return Number.isFinite(f) && f >= 0 && f < 1 ? f : null;
}

/** A fraction of the way through the piece as a page (the left page of a spread). */
function pageAt(at: number, count: number, spread: boolean): number {
  const n = Math.min(Math.max(1, 1 + Math.round(at * count)), Math.max(1, count));
  return spread ? n - ((n - 1) % 2) : n;
}

type FsDoc = Document & { webkitFullscreenElement?: Element | null; webkitExitFullscreen?: () => void };
type FsEl = HTMLElement & { webkitRequestFullscreen?: () => void };

/** Real fullscreen for the stand, asked for in the tap itself (browsers want a gesture); nothing where it isn't offered (an iPhone). */
function enterFullscreen() {
  const el = document.documentElement as FsEl;
  try {
    if (el.requestFullscreen) void el.requestFullscreen({ navigationUI: "hide" }).catch(() => undefined);
    else el.webkitRequestFullscreen?.();
  } catch {
    /* not offered */
  }
}

function exitFullscreen() {
  const d = document as FsDoc;
  try {
    if (d.fullscreenElement) void d.exitFullscreen().catch(() => undefined);
    else if (d.webkitFullscreenElement) d.webkitExitFullscreen?.();
  } catch {
    /* ignore */
  }
}

/** Stands open (StrictMode runs an effect twice in development: only a real close leaves fullscreen). */
let standsOpen = 0;

/** The last ready answer and the size it was laid out for (shown until a newer one comes). */
interface Shown {
  st: ScoreStatus;
  w: number;
  h: number;
  staff: number;
}

interface Problem {
  words: string;
  /** Asking again by itself (nothing answered). */
  retrying: boolean;
  /** No such piece, or no file (a test pattern): asking again won't help. */
  noMidi: boolean;
}

export function ScoreView({ pieceId, onBack }: { pieceId: string; onBack: () => void }) {
  const { api, bumpLibrary } = useMusic();
  const [mode, setMode] = useState<Mode>(() => (storageGet(MODE_KEY) === "pages" ? "pages" : "scroll"));
  const [paper, setPaper] = useState<ScorePaper>(() => {
    const p = storageGet(PAPER_KEY);
    return SCORE_PAPERS.some(([id]) => id === p) ? (p as ScorePaper) : "a4";
  });
  const [staff, setStaff] = useState(storedStaff);
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);
  const [status, setStatus] = useState<ScoreStatus | null>(null);
  const [shown, setShown] = useState<Shown | null>(null);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [tick, setTick] = useState(0);
  /** The pass shown: null until chosen (the better one once it exists, else the quick one). */
  const [kind, setKind] = useState<ScoreKind | null>(null);
  const retryNext = useRef(false);
  const [page, setPage] = useState(1);
  const area = useRef<HTMLDivElement>(null);
  const imgs = useRef<(HTMLImageElement | null)[]>([]);
  const scrollToPage = useRef(0);
  const swipe = useRef<{ x: number; y: number } | null>(null);
  /** The music stand: where it opened (a fraction of the way through), while it's open. */
  const [stand, setStand] = useState<number | null>(null);
  const standOpen = stand !== null;
  /** While the stand is open the view doesn't lay out again (fullscreen and the scroll lock change its size). */
  const paused = useRef(false);
  const remeasure = useRef<() => void>(() => undefined);
  /** The stand put a history entry on top, so Back (an edge swipe, a phone's Back) closes the stand, not the score. */
  const standEntry = useRef(false);
  /** The title and composer being changed, in place of the title. */
  const [editing, setEditing] = useState<{ title: string; composer: string } | null>(null);
  const [saving, setSaving] = useState(false);
  const [editErr, setEditErr] = useState<string | null>(null);

  // The page size: the area's width, by the height left below the controls.
  useLayoutEffect(() => {
    const el = area.current;
    if (!el) return;
    const measure = () => {
      if (paused.current) return;
      const w = Math.max(240, tens(el.clientWidth));
      const top = el.getBoundingClientRect().top + window.scrollY;
      // the layout viewport's height: steady while a phone's address bar comes and goes
      const h = tens(Math.max(MIN_H, document.documentElement.clientHeight - top - 12 - PAGER_H));
      setSize((s) => (s && s.w === w && Math.abs(s.h - h) < 40 ? s : { w, h }));
    };
    measure();
    let t: ReturnType<typeof setTimeout> | undefined;
    const later = () => {
      clearTimeout(t);
      t = setTimeout(measure, RESIZE_MS);
    };
    remeasure.current = later;
    const ro = new ResizeObserver(later);
    ro.observe(el);
    window.addEventListener("resize", later);
    return () => {
      clearTimeout(t);
      ro.disconnect();
      window.removeEventListener("resize", later);
      remeasure.current = () => undefined;
    };
  }, []);

  // Ask for the score at this size; again every 1.5 s while it's queued or being written.
  useEffect(() => {
    if (!size) return;
    const ac = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let paused = false;
    const ask = async () => {
      if (document.hidden) {
        paused = true;
        return;
      }
      try {
        const retry = retryNext.current;
        retryNext.current = false;
        const st = await api.score(pieceId, { ...size, staff }, ac.signal, { kind: kind ?? "quick", retry });
        if (ac.signal.aborted) return;
        if (kind === null && st.better === "ready") {
          setKind("better");                     // asked for before: show it
          return;
        }
        setStatus(st);
        setProblem(null);
        if (st.state === "ready") setShown({ st, ...size, staff });
        else if (st.state === "queued" || st.state === "making") timer = setTimeout(ask, POLL_MS);
      } catch (e) {
        if (ac.signal.aborted) return;
        const noMidi = e instanceof RoomApiError && e.status === 404 && e.code === "unknown_piece";
        const retrying = isUnreached(e);
        setProblem({ words: noMidi ? "This piece has no MIDI file to write a score from." : musicWords(e), retrying, noMidi });
        if (retrying) timer = setTimeout(ask, RETRY_MS);
      }
    };
    const onVisible = () => {
      if (document.hidden || !paused) return;
      paused = false;
      void ask();
    };
    document.addEventListener("visibilitychange", onVisible);
    void ask();
    return () => {
      ac.abort();
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [api, pieceId, size, staff, tick, kind]);

  const count = shown?.st.pages ?? 0;
  const cur = Math.min(Math.max(1, page), Math.max(1, count));
  const go = useCallback((n: number) => setPage(Math.min(Math.max(1, n), Math.max(1, count))), [count]);
  const pageUrl = useCallback(
    (n: number) =>
      shown ? api.scorePageUrl(pieceId, n, { w: shown.w, h: shown.h, staff: shown.staff, rev: shown.st.rev, kind: shown.st.kind }) : "",
    [api, pieceId, shown],
  );
  const relaying = !!shown && !!size && (shown.w !== size.w || shown.h !== size.h || shown.staff !== staff);

  // Pages: the keys turn pages (not while typing, or in a sheet or the menu, or while the stand has them).
  useEffect(() => {
    if (mode !== "pages" || !count || standOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey) return;
      const t = e.target instanceof Element ? e.target : null;
      if (t?.closest('input, textarea, select, [contenteditable="true"], [role="dialog"]')) return;
      let to: number;
      switch (e.key) {
        case "ArrowLeft":
        case "PageUp":
          to = cur - 1;
          break;
        case "ArrowRight":
        case "PageDown":
          to = cur + 1;
          break;
        case " ":
          if (t?.closest("button, a")) return; // the button's own
          to = e.shiftKey ? cur - 1 : cur + 1;
          break;
        case "Home":
          to = 1;
          break;
        case "End":
          to = count;
          break;
        default:
          return;
      }
      e.preventDefault();
      go(to);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mode, count, cur, go, standOpen]);

  // Pages: the next and previous pages load ahead.
  useEffect(() => {
    if (mode !== "pages" || !count) return;
    for (const n of [cur + 1, cur - 1]) {
      if (n < 1 || n > count) continue;
      const im = new Image();
      im.decoding = "async";
      im.src = pageUrl(n);
    }
  }, [mode, count, cur, pageUrl]);

  // Switching mode keeps your place: Pages opens at the page in view, Scroll scrolls to the page you were on.
  useLayoutEffect(() => {
    if (mode === "pages") {
      window.scrollTo(0, 0);
      return;
    }
    const n = scrollToPage.current;
    scrollToPage.current = 0;
    if (n > 1) imgs.current[n - 1]?.scrollIntoView({ block: "start" });
  }, [mode]);

  /** The page in view: Pages' page, or the last page in Scroll whose top is above the screen's middle. */
  const inView = () => {
    if (mode === "pages") return cur;
    const mid = window.innerHeight / 2;
    let n = 1;
    imgs.current.slice(0, count).forEach((el, i) => {
      if (el && el.getBoundingClientRect().top <= mid) n = i + 1;
    });
    return n;
  };

  const choose = (m: Mode) => {
    if (m === mode) return;
    if (m === "pages" && count) setPage(inView());
    else scrollToPage.current = cur;
    setMode(m);
    storageSet(MODE_KEY, m);
  };

  const si = STAFFS.indexOf(staff);
  const resize = (d: number) => {
    const s = STAFFS[Math.min(STAFFS.length - 1, Math.max(0, si + d))];
    setStaff(s);
    storageSet(STAFF_KEY, String(s));
  };

  /** Open the stand at page n (a tap on it), else where it was left this session, else at the page in view. */
  const openStand = (n?: number) => {
    if (!shown || !count || standOpen) return;
    const at = n ? (n - 1) / count : (standAt(pieceId) ?? (inView() - 1) / count);
    paused.current = true;
    enterFullscreen();
    try {
      window.history.pushState(window.history.state, "");
      standEntry.current = true;
    } catch {
      standEntry.current = false;
    }
    setStand(at);
  };
  /** Closed at this place (by Back: popped): the view follows it (Pages turns to it, Scroll scrolls to it once
   * fullscreen has gone). */
  const closeStand = (at: number, popped = false) => {
    if (standEntry.current && !popped) window.history.back();
    standEntry.current = false;
    paused.current = false;
    setStand(null);
    const n = pageAt(at, count, false);
    if (mode === "pages") setPage(n);
    else
      setTimeout(() => {
        if (n > 1) imgs.current[n - 1]?.scrollIntoView({ block: "start" });
      }, 150);
    remeasure.current();
  };

  const title = status?.title || shown?.st.title || (problem ? "Score" : null);
  const composer = status?.composer ?? shown?.st.composer ?? null;
  const editable = !!(status?.editable ?? shown?.st.editable);
  const retry = () => {
    setProblem(null);
    retryNext.current = true;
    setTick((n) => n + 1);
  };
  const choosePass = (k: ScoreKind) => {
    if (k === "better" && status?.better === "failed") retryNext.current = true;
    setKind(k);
  };
  const startEdit = () => {
    setEditErr(null);
    setEditing({ title: title && title !== "Score" ? title : "", composer: composer ?? "" });
  };
  const saveEdit = async () => {
    if (!editing || saving) return;
    const t = editing.title.trim();
    const c = editing.composer.trim() || null;
    if (!t) {
      setEditErr("It needs a title.");
      return;
    }
    setSaving(true);
    setEditErr(null);
    try {
      await api.edit(pieceId, { title: t, composer: c });
      // the new names at once; then ask the score again, which carries them
      setStatus((s) => (s ? { ...s, title: t, composer: c } : s));
      setShown((s) => (s ? { ...s, st: { ...s.st, title: t, composer: c } } : s));
      setEditing(null);
      bumpLibrary();
      setTick((n) => n + 1);
    } catch (e) {
      setEditErr(musicWords(e));
    } finally {
      setSaving(false);
    }
  };
  // the better pass being written (or failed) while the quick score is on screen
  const betterBusy = !!shown && status?.kind === "better" && (status.state === "queued" || status.state === "making");
  const betterFailed = !!shown && status?.kind === "better" && status.state === "failed";
  const shownKind = shown?.st.kind ?? "quick";
  const haveBetter = status?.better === "ready" || shownKind === "better";

  let body: ReactNode = null;
  if (shown && count > 0) {
    body =
      mode === "scroll" ? (
        <div className="space-y-2">
          {Array.from({ length: count }, (_, i) => (
            <img
              key={i}
              ref={(el) => {
                imgs.current[i] = el;
              }}
              src={pageUrl(i + 1)}
              alt={`Page ${i + 1} of ${count}`}
              width={shown.w}
              height={shown.h}
              loading="lazy"
              decoding="async"
              title="Play from here, full screen"
              onClick={() => openStand(i + 1)}
              className={cn(PAGE_CLASS, "cursor-zoom-in")}
            />
          ))}
        </div>
      ) : (
        <div>
          <div
            className="cursor-pointer select-none"
            style={{ touchAction: "pan-y pinch-zoom" }}
            onPointerDown={(e) => {
              swipe.current = e.button === 0 ? { x: e.clientX, y: e.clientY } : null;
            }}
            onPointerCancel={() => {
              swipe.current = null;
            }}
            onPointerUp={(e) => {
              const s = swipe.current;
              swipe.current = null;
              if (!s) return;
              const dx = e.clientX - s.x;
              const dy = e.clientY - s.y;
              if (Math.abs(dx) > 50 && Math.abs(dx) > 1.5 * Math.abs(dy)) go(dx < 0 ? cur + 1 : cur - 1);
              else if (Math.abs(dx) < 10 && Math.abs(dy) < 10) {
                const r = e.currentTarget.getBoundingClientRect();
                const f = (e.clientX - r.left) / r.width;
                if (f < 1 / 3) go(cur - 1);
                else if (f > 2 / 3) go(cur + 1);
                else openStand(cur);
              }
            }}
          >
            <img
              src={pageUrl(cur)}
              alt={`Page ${cur} of ${count}`}
              width={shown.w}
              height={shown.h}
              decoding="async"
              draggable={false}
              className={PAGE_CLASS}
            />
          </div>
          <div className="flex items-center justify-center gap-4" style={{ height: PAGER_H }}>
            <button type="button" aria-label="Previous page" disabled={cur <= 1} onClick={() => go(cur - 1)} className={ROUND}>
              <ChevronLeft className="h-5 w-5" aria-hidden />
            </button>
            <span className="min-w-16 text-center text-base tabular-nums text-stone-700" aria-live="polite">
              {cur} / {count}
            </span>
            <button type="button" aria-label="Next page" disabled={cur >= count} onClick={() => go(cur + 1)} className={ROUND}>
              <ChevronRight className="h-5 w-5" aria-hidden />
            </button>
          </div>
        </div>
      );
  } else if (problem) {
    body = (
      <div className={cn(CARD_CLASS, "space-y-3 px-4 py-4")}>
        <p className="flex items-center gap-2 text-base text-amber-800">
          {problem.retrying && <Spinner />}
          {problem.words}
        </p>
        {!problem.retrying && !problem.noMidi && (
          <button type="button" onClick={retry} className={cn(TAP_BUTTON_CLASS, SECONDARY, "inline-flex items-center gap-2")}>
            <RotateCcw className="h-4 w-4" aria-hidden /> Try again
          </button>
        )}
      </div>
    );
  } else if (status?.state === "failed") {
    body = (
      <div className={cn(CARD_CLASS, "space-y-2 px-4 py-4")}>
        <p className="text-base font-medium text-amber-800">Couldn't write this score.</p>
        {status.detail && <p className="break-words text-sm text-muted-foreground">{status.detail}</p>}
        <div className="pt-1">
          <button type="button" onClick={retry} className={cn(TAP_BUTTON_CLASS, SECONDARY, "inline-flex items-center gap-2")}>
            <RotateCcw className="h-4 w-4" aria-hidden /> Try again
          </button>
        </div>
      </div>
    );
  } else if (status?.state === "queued" || status?.state === "making") {
    // queued with nothing ahead is about to start: say it's being written
    const waiting = status.state === "queued" && (status.ahead ?? 0) > 0;
    const pct = percent(status.state === "making" ? status.progress : 0) ?? 0;
    body = (
      <div className={cn(CARD_CLASS, "space-y-3 px-4 py-4")}>
        <p role="status" className="flex items-center gap-2 text-base font-medium text-stone-800">
          <Spinner />
          {waiting
            ? `Waiting for another score to finish${(status.ahead ?? 0) > 1 ? ` (${status.ahead} ahead)` : ""}`
            : `Writing the score… ${pct}%`}
        </p>
        {!waiting && (
          <div
            role="progressbar"
            aria-label="Writing the score"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={pct}
            className="h-1.5 overflow-hidden rounded-full bg-stone-100"
          >
            <div className="h-full rounded-full bg-teal-600 transition-[width] duration-500" style={{ width: `${Math.max(3, pct)}%` }} />
          </div>
        )}
      </div>
    );
  } else if (status?.state === "ready") {
    body = <p className="py-2 text-base text-muted-foreground">There's nothing in this piece to write out.</p>;
  }

  return (
    <section aria-label="Score" data-testid="music-score" className="space-y-3">
      <div className={cn("flex gap-2", editing ? "items-start" : "items-center")}>
        <button type="button" onClick={onBack} aria-label="Back to Music" className={cn(ICON_BUTTON, "-ml-2")}>
          <ArrowLeft className="h-5 w-5" aria-hidden />
        </button>
        {editing ? (
          <form
            className="min-w-0 flex-1 space-y-3"
            aria-label="Title and composer"
            onSubmit={(e) => {
              e.preventDefault();
              void saveEdit();
            }}
            onKeyDown={(e) => {
              if (e.key !== "Escape" || saving) return;
              e.preventDefault();
              setEditing(null);
            }}
          >
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="block space-y-1">
                <span className="text-[15px] font-medium">Title</span>
                <Input
                  autoFocus
                  required
                  value={editing.title}
                  maxLength={120}
                  onChange={(e) => setEditing((d) => d && { ...d, title: e.target.value })}
                  className={FIELD}
                />
              </label>
              <label className="block space-y-1">
                <span className="text-[15px] font-medium">Composer</span>
                <Input
                  value={editing.composer}
                  maxLength={120}
                  placeholder="If you know it"
                  onChange={(e) => setEditing((d) => d && { ...d, composer: e.target.value })}
                  className={FIELD}
                />
              </label>
            </div>
            {editErr && <p className="text-[15px] text-rose-700">{editErr}</p>}
            <div className="flex gap-2">
              <button type="submit" disabled={saving} className={cn(TAP_BUTTON_CLASS, PRIMARY, "inline-flex items-center gap-2 font-semibold")}>
                {saving ? <Spinner /> : <Check className="h-4 w-4" aria-hidden />} Save
              </button>
              <button type="button" disabled={saving} onClick={() => setEditing(null)} className={cn(TAP_BUTTON_CLASS, SECONDARY)}>
                Cancel
              </button>
            </div>
          </form>
        ) : (
          <>
            <div className="min-w-0 flex-1">
              {title ? (
                <h1 className="truncate text-xl font-semibold leading-7" title={title}>
                  {title}
                </h1>
              ) : (
                <p className="flex h-7 items-center gap-2 text-base text-muted-foreground">
                  <Spinner /> Finding the piece…
                </p>
              )}
              <p className="truncate text-[15px] leading-5 text-muted-foreground">{composer || " "}</p>
            </div>
            {editable && (
              <button type="button" onClick={startEdit} aria-label="Edit title and composer" title="Edit title and composer" className={cn(ICON_BUTTON, "-mr-2 text-stone-500")}>
                <Pencil className="h-[18px] w-[18px]" aria-hidden />
              </button>
            )}
          </>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {shown && count > 0 && (
          <button
            type="button"
            onClick={() => openStand()}
            title="Full screen, to play from: tap the right of the page to turn forward, the left to go back"
            className={cn(CHIP, PRIMARY, "border-transparent font-semibold")}
          >
            <Maximize2 className="h-4 w-4" aria-hidden /> Play from the score
          </button>
        )}
        <div role="radiogroup" aria-label="How to read it" className="flex gap-1.5">
          {(
            [
              ["scroll", "Scroll"],
              ["pages", "Pages"],
            ] as [Mode, string][]
          ).map(([m, words]) => (
            <button
              key={m}
              type="button"
              role="radio"
              aria-checked={mode === m}
              onClick={() => choose(m)}
              className={cn(CHIP, mode === m ? CHIP_ON : CHIP_OFF)}
            >
              {words}
            </button>
          ))}
        </div>
        <div className="flex gap-1">
          <button
            type="button"
            aria-label="Smaller notes"
            title="Smaller notes"
            disabled={si <= 0}
            onClick={() => resize(-1)}
            className={cn(CHIP, CHIP_OFF, "px-3 disabled:opacity-40")}
          >
            A−
          </button>
          <button
            type="button"
            aria-label="Larger notes"
            title="Larger notes"
            disabled={si >= STAFFS.length - 1}
            onClick={() => resize(1)}
            className={cn(CHIP, CHIP_OFF, "px-3 disabled:opacity-40")}
          >
            A+
          </button>
        </div>
        {shown && count > 0 && (
          <div className="flex items-center gap-1">
            <a
              href={api.scorePdfUrl(pieceId, paper, shown.st.rev, shown.st.kind, tag(`${title ?? ""}\n${composer ?? ""}`))}
              target="_blank"
              rel="noopener"
              title="The whole score as a PDF, to print or keep"
              className={cn(CHIP, CHIP_OFF)}
            >
              <FileDown className="h-4 w-4" aria-hidden /> PDF
            </a>
            <select
              aria-label="Paper"
              value={paper}
              onChange={(e) => {
                const p = e.target.value as ScorePaper;
                setPaper(p);
                storageSet(PAPER_KEY, p);
              }}
              className={cn(CHIP, CHIP_OFF, "appearance-none bg-white pr-3.5")}
            >
              {SCORE_PAPERS.map(([id, words]) => (
                <option key={id} value={id}>
                  {words}
                </option>
              ))}
            </select>
          </div>
        )}
        {shown && count > 0 && (haveBetter ? (
          <div role="radiogroup" aria-label="Which score" className="flex gap-1.5">
            {(
              [
                ["quick", "Quick"],
                ["better", "Better"],
              ] as [ScoreKind, string][]
            ).map(([k, words]) => (
              <button
                key={k}
                type="button"
                role="radio"
                aria-checked={shownKind === k}
                onClick={() => choosePass(k)}
                className={cn(CHIP, shownKind === k ? CHIP_ON : CHIP_OFF)}
              >
                {words}
              </button>
            ))}
          </div>
        ) : (
          !betterBusy && (
            <button
              type="button"
              onClick={() => choosePass("better")}
              title="A slower, closer reading of the beat, bars and hands (the live score's neural networks): a minute or two for a long piece"
              className={cn(CHIP, CHIP_OFF)}
            >
              <Sparkles className="h-4 w-4" aria-hidden /> Better score
            </button>
          )
        ))}
        {relaying && !problem && <Spinner className="ml-auto h-5 w-5 text-muted-foreground" />}
      </div>

      {betterBusy && status && (
        <div className={cn(CARD_CLASS, "space-y-2 px-4 py-3")} role="status">
          <p className="flex items-center gap-2 text-[15px] font-medium text-stone-800">
            <Spinner />
            {status.state === "queued" && (status.ahead ?? 0) > 0
              ? "Better score: waiting for another to finish"
              : `Making a better score… ${percent(status.progress) ?? 0}%`}
          </p>
          <div className="h-1.5 overflow-hidden rounded-full bg-stone-100" aria-hidden>
            <div
              className="h-full rounded-full bg-teal-600 transition-[width] duration-500"
              style={{ width: `${Math.max(3, percent(status.progress) ?? 0)}%` }}
            />
          </div>
          <p className="text-sm text-muted-foreground">The quick score stays here until it's ready.</p>
        </div>
      )}
      {betterFailed && status && (
        <p className="flex flex-wrap items-center gap-x-3 text-[15px] text-amber-800">
          Couldn't make the better score{status.detail ? ` (${status.detail})` : ""}.
          <button type="button" onClick={retry} className="font-medium underline underline-offset-2">
            Try again
          </button>
          <button type="button" onClick={() => setKind("quick")} className="font-medium underline underline-offset-2">
            Keep the quick one
          </button>
        </p>
      )}

      {/* a new size that didn't lay out: the old pages stay */}
      {shown && count > 0 && problem && (
        <p className="flex flex-wrap items-center gap-x-3 text-[15px] text-amber-800">
          {problem.words}
          {!problem.retrying && (
            <button type="button" onClick={retry} className="font-medium underline underline-offset-2">
              Try again
            </button>
          )}
        </p>
      )}

      <div ref={area}>{body}</div>

      {stand !== null && shown && (
        <MusicStand
          pieceId={pieceId}
          title={title || "Score"}
          kind={shownKind}
          staff={staff}
          canSmaller={si > 0}
          canLarger={si < STAFFS.length - 1}
          onResize={resize}
          start={stand}
          seed={shown}
          onClose={closeStand}
        />
      )}
    </section>
  );
}

interface StandProps {
  pieceId: string;
  title: string;
  /** The pass the view shows (Quick or Better). */
  kind: ScoreKind;
  /** The view's staff size (A− / A+ are shared). */
  staff: number;
  canSmaller: boolean;
  canLarger: boolean;
  onResize: (d: number) => void;
  /** Where to open: a fraction of the way through the piece (0 = the start). */
  start: number;
  /** The view's pages, shown (fitted) until the stand's own layout comes. */
  seed: Shown;
  /** Closed at this place (a fraction of the way through); popped: by Back. */
  onClose: (at: number, popped?: boolean) => void;
}

/** The music stand: the score full screen, to play from (see the top of the file). */
function MusicStand({ pieceId, title, kind, staff, canSmaller, canLarger, onResize, start, seed, onClose }: StandProps) {
  const { api } = useMusic();
  const root = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const [mode, setMode] = useState<Mode>(() => (sessionGet(STAND_MODE_KEY) === "scroll" ? "scroll" : "pages"));
  /** The space below the bar, in css px (multiples of 10). */
  const [room, setRoom] = useState<{ w: number; h: number } | null>(null);
  const [shown, setShown] = useState<Shown>(seed);
  const [problem, setProblem] = useState<{ words: string; retrying: boolean } | null>(null);
  const [tick, setTick] = useState(0);
  /** How far through the piece the reader is (0..1, the top of what's in view): kept across a new layout. */
  const at = useRef(start);
  const [page, setPage] = useState(() => pageAt(start, seed.st.pages ?? 1, false));
  /** Scroll: the page in view and how far down the piece the screen's bottom is (0..1). */
  const [scrolled, setScrolled] = useState({ page: 1, done: 0 });
  const swipe = useRef<{ x: number; y: number } | null>(null);
  const saveTimer = useRef<ReturnType<typeof setTimeout>>();

  const remember = useCallback(() => sessionSet(standAtKey(pieceId), String(at.current)), [pieceId]);

  // Open: focus, no page scroll behind, the screen kept awake (asked again when the tab comes back), and fullscreen
  // left on the way out.
  useEffect(() => {
    const prev = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    root.current?.focus({ preventScroll: true });
    const html = document.documentElement;
    const body = document.body;
    const was = [html.style.overflow, body.style.overflow];
    html.style.overflow = "hidden";
    body.style.overflow = "hidden";
    standsOpen += 1;
    let live = true;
    let lock: WakeLockSentinel | null = null;
    const hold = async () => {
      if (document.hidden || (lock && !lock.released)) return;
      try {
        const l = await navigator.wakeLock?.request("screen");
        if (!l) return;
        if (!live) {
          void l.release().catch(() => undefined);
          return;
        }
        lock = l;
      } catch {
        /* not offered, or not allowed just now (a low battery) */
      }
    };
    const onVisible = () => {
      if (!document.hidden) void hold();
    };
    void hold();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      live = false;
      document.removeEventListener("visibilitychange", onVisible);
      if (lock && !lock.released) void lock.release().catch(() => undefined);
      html.style.overflow = was[0];
      body.style.overflow = was[1];
      standsOpen -= 1;
      setTimeout(() => {
        if (standsOpen === 0) exitFullscreen();
      }, 0);
      prev?.focus({ preventScroll: true });
    };
  }, []);

  // The room below the bar: measured once things settle (fullscreen coming in), and again whenever the screen turns.
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    const measure = () => {
      const w = tens(el.clientWidth);
      const h = tens(el.clientHeight);
      if (w < 200 || h < 200) return;
      setRoom((r) => (r && r.w === w && r.h === h ? r : { w, h }));
    };
    let t: ReturnType<typeof setTimeout> | undefined;
    const later = () => {
      clearTimeout(t);
      t = setTimeout(measure, STAND_RESIZE_MS);
    };
    later();
    const ro = new ResizeObserver(later);
    ro.observe(el);
    window.addEventListener("orientationchange", later);
    return () => {
      clearTimeout(t);
      ro.disconnect();
      window.removeEventListener("orientationchange", later);
    };
  }, []);

  // Two pages side by side on a wide, landscape screen (an iPad on its side), else one fitted to the screen.
  const spread = mode === "pages" && !!room && room.w >= 900 && room.w >= 1.25 * room.h;
  const want = useMemo<ScoreSize | null>(
    () => (room ? { w: spread ? tens((room.w - 1) / 2) : room.w, h: room.h, staff } : null),
    [room, spread, staff],
  );

  // Ask for pages of exactly that size (the old ones stay up meanwhile).
  useEffect(() => {
    if (!want) return;
    const ac = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const ask = async () => {
      try {
        const st = await api.score(pieceId, want, ac.signal, { kind });
        if (ac.signal.aborted) return;
        if (st.state === "ready") {
          setProblem(null);
          setShown({ st, ...want });
        } else if (st.state === "queued" || st.state === "making") timer = setTimeout(ask, POLL_MS);
        else setProblem({ words: st.detail ? `Couldn't lay the score out for this screen (${st.detail}).` : "Couldn't lay the score out for this screen.", retrying: false });
      } catch (e) {
        if (ac.signal.aborted) return;
        const retrying = isUnreached(e);
        setProblem({ words: musicWords(e), retrying });
        if (retrying) timer = setTimeout(ask, RETRY_MS);
      }
    };
    void ask();
    return () => {
      ac.abort();
      clearTimeout(timer);
    };
  }, [api, pieceId, want, kind, tick]);

  const count = shown.st.pages ?? 0;
  const step = spread ? 2 : 1;
  /** The last page a turn can land on (a spread's left page). */
  const last = spread ? Math.max(1, count - ((count - 1) % 2)) : Math.max(1, count);
  const cur = Math.min(Math.max(1, page), last);
  const relaying = !!want && (shown.w !== want.w || shown.h !== want.h || shown.staff !== want.staff || shown.st.kind !== kind);
  const pageUrl = useCallback(
    (n: number) => api.scorePageUrl(pieceId, n, { w: shown.w, h: shown.h, staff: shown.staff, rev: shown.st.rev, kind: shown.st.kind }),
    [api, pieceId, shown],
  );

  const onScroll = useCallback(() => {
    const el = box.current;
    if (!el || !count || !el.scrollHeight) return;
    at.current = Math.min(0.999, el.scrollTop / el.scrollHeight);
    const p = Math.min(count, 1 + Math.floor(((el.scrollTop + el.clientHeight / 3) / el.scrollHeight) * count));
    const done = Math.round(Math.min(1, (el.scrollTop + el.clientHeight) / el.scrollHeight) * 200) / 200;
    setScrolled((s) => (s.page === p && s.done === done ? s : { page: p, done }));
    clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(remember, 300);
  }, [count, remember]);

  // A new layout, Pages / Scroll, or a spread coming or going: the same place in the piece.
  useLayoutEffect(() => {
    if (!count) return;
    if (mode === "pages") setPage(pageAt(at.current, count, spread));
    else {
      const el = box.current;
      if (!el) return;
      el.scrollTop = at.current * el.scrollHeight;
      onScroll();
    }
  }, [mode, count, spread, shown, onScroll]);

  // The way out keeps the place for next time.
  useEffect(
    () => () => {
      clearTimeout(saveTimer.current);
      remember();
    },
    [remember],
  );

  const go = useCallback(
    (n: number) => {
      if (!count) return;
      const p = Math.min(Math.max(1, n), last);
      setPage(p);
      at.current = (p - 1) / count;
      remember();
    },
    [count, last, remember],
  );

  // Pages: the next and previous pages (spreads) load ahead, so a turn is instant.
  useEffect(() => {
    if (mode !== "pages" || !count) return;
    const ahead = spread ? [cur + 2, cur + 3, cur - 2, cur - 1] : [cur + 1, cur + 2, cur - 1];
    for (const n of ahead) {
      if (n < 1 || n > count) continue;
      const im = new Image();
      im.decoding = "async";
      im.src = pageUrl(n);
    }
  }, [mode, count, cur, spread, pageUrl]);

  const close = useCallback(() => onClose(at.current), [onClose]);

  // Back closes the stand (the view put an entry on top for it).
  useEffect(() => {
    const onPop = () => onClose(at.current, true);
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, [onClose]);

  // Keys (a Bluetooth page turner sends these): forward, back, the start, the end; Escape closes.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey) return;
      if (e.key === "Escape") {
        e.preventDefault();
        close();
        return;
      }
      const t = e.target instanceof Element ? e.target : null;
      let d: number;
      switch (e.key) {
        case "ArrowRight":
        case "ArrowDown":
        case "PageDown":
          d = 1;
          break;
        case "ArrowLeft":
        case "ArrowUp":
        case "PageUp":
          d = -1;
          break;
        case " ":
        case "Enter":
          if (t?.closest("button, a, input, select, textarea")) return; // the control's own
          d = e.shiftKey ? -1 : 1;
          break;
        case "Home":
          d = -Infinity;
          break;
        case "End":
          d = Infinity;
          break;
        default:
          return;
      }
      e.preventDefault();
      if (mode === "scroll") {
        const el = box.current;
        if (!el) return;
        if (Number.isFinite(d)) el.scrollBy({ top: d * el.clientHeight * 0.85 });
        else el.scrollTop = d < 0 ? 0 : el.scrollHeight;
      } else if (Number.isFinite(d)) go(cur + d * step);
      else go(d < 0 ? 1 : last);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mode, close, go, cur, step, last]);

  // Tab stays inside the stand.
  const trapTab = (e: ReactKeyboardEvent) => {
    if (e.key !== "Tab") return;
    const els = Array.from(root.current?.querySelectorAll<HTMLElement>("button:not([disabled])") ?? []);
    if (!els.length) return;
    const a = document.activeElement;
    if (e.shiftKey && (a === els[0] || a === root.current)) {
      e.preventDefault();
      els[els.length - 1].focus();
    } else if (!e.shiftKey && a === els[els.length - 1]) {
      e.preventDefault();
      els[0].focus();
    }
  };

  const chooseMode = (m: Mode) => {
    if (m === mode) return;
    setMode(m);
    sessionSet(STAND_MODE_KEY, m);
  };

  const shownLast = Math.min(count, cur + step - 1);
  const where = !count ? "" : mode === "scroll" ? `${scrolled.page} / ${count}` : shownLast > cur ? `${cur}–${shownLast} / ${count}` : `${cur} / ${count}`;
  const done = !count ? 0 : mode === "scroll" ? scrolled.done : shownLast / count;
  // a page's slot: the size asked for (the old pages fit into it while the new ones lay out)
  const slot = want ?? { w: shown.w, h: shown.h };
  // the same two elements turn after turn: a browser keeps the old page up until the new one is there (no blank flash)
  const pageImg = (n: number, key: string) => (
    <img
      key={key}
      src={pageUrl(n)}
      alt={`Page ${n} of ${count}`}
      draggable={false}
      className="block max-h-full max-w-full shrink-0 object-contain"
      style={{ width: slot.w, height: slot.h }}
    />
  );

  let pages: ReactNode;
  if (!count) pages = null;
  else if (mode === "scroll")
    pages = (
      <div className="divide-y divide-stone-200">
        {Array.from({ length: count }, (_, i) => (
          <img
            key={i}
            src={pageUrl(i + 1)}
            alt={`Page ${i + 1} of ${count}`}
            width={shown.w}
            height={shown.h}
            loading="lazy"
            decoding="async"
            draggable={false}
            className="block h-auto w-full"
          />
        ))}
      </div>
    );
  else
    pages = (
      <div className="flex h-full w-full items-center justify-center">
        {pageImg(cur, "a")}
        {spread && (
          <>
            <div aria-hidden className="w-px shrink-0 bg-stone-200" style={{ height: slot.h * 0.92 }} />
            {cur + 1 <= count ? pageImg(cur + 1, "b") : <div aria-hidden className="shrink-0" style={{ width: slot.w, height: slot.h }} />}
          </>
        )}
      </div>
    );

  return createPortal(
    <div
      ref={root}
      role="dialog"
      aria-modal="true"
      aria-label={`${title}: the score, full screen`}
      tabIndex={-1}
      onKeyDown={trapTab}
      data-testid="music-stand"
      className={cn(THEME_CLASS, "fixed inset-0 z-50 flex flex-col bg-white text-base antialiased outline-none")}
      style={{
        paddingTop: "env(safe-area-inset-top)",
        paddingRight: "env(safe-area-inset-right)",
        paddingBottom: "env(safe-area-inset-bottom)",
        paddingLeft: "env(safe-area-inset-left)",
      }}
    >
      <div
        className="flex h-11 shrink-0 items-center gap-0.5 border-b border-stone-100 pl-3 pr-1 text-stone-600"
        // a tap on the bar hands the keys back to the page (a pedal's Space or Enter mustn't press the last button)
        onClick={(e) => {
          if (e.detail > 0) root.current?.focus({ preventScroll: true });
        }}
      >
        <div className="min-w-0 flex-1">
          <p className="hidden truncate text-[15px] font-medium text-stone-700 sm:block" title={title}>
            {title}
          </p>
        </div>
        {relaying && !problem && <Spinner className="mx-1 text-stone-400" />}
        <span className="px-1.5 text-[15px] tabular-nums text-stone-600" aria-live="polite">
          {where}
        </span>
        <button type="button" aria-label="Smaller notes" title="Smaller notes" disabled={!canSmaller} onClick={() => onResize(-1)} className={BAR_BUTTON}>
          A−
        </button>
        <button type="button" aria-label="Larger notes" title="Larger notes" disabled={!canLarger} onClick={() => onResize(1)} className={BAR_BUTTON}>
          A+
        </button>
        <div role="radiogroup" aria-label="How to read it" className="flex">
          {(
            [
              ["pages", "Pages"],
              ["scroll", "Scroll"],
            ] as [Mode, string][]
          ).map(([m, words]) => (
            <button
              key={m}
              type="button"
              role="radio"
              aria-checked={mode === m}
              onClick={() => chooseMode(m)}
              className="h-11 shrink-0 rounded-full px-0.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <span
                className={cn(
                  "inline-flex h-8 items-center rounded-full px-2.5 text-[15px] font-medium",
                  mode === m ? "bg-stone-100 text-stone-900" : "text-stone-500 hover:text-stone-800",
                )}
              >
                {words}
              </span>
            </button>
          ))}
        </div>
        <button type="button" aria-label="Close" title="Close (Esc)" onClick={close} className={cn(BAR_BUTTON, "ml-0.5")}>
          <X className="h-5 w-5" aria-hidden />
        </button>
      </div>

      <div className="relative min-h-0 flex-1">
        <div
          ref={box}
          className={cn(
            "absolute inset-0 select-none overscroll-contain",
            mode === "scroll" ? "overflow-y-auto overflow-x-hidden" : "cursor-pointer overflow-hidden",
          )}
          style={{
            overflowAnchor: "none",
            WebkitTouchCallout: "none",
            touchAction: mode === "scroll" ? "pan-y pinch-zoom" : "pinch-zoom",
          }}
          onScroll={mode === "scroll" ? onScroll : undefined}
          onPointerDown={(e) => {
            // the first finger (or the main mouse button) only: a second finger is a pinch
            swipe.current = mode === "pages" && e.isPrimary && (e.pointerType !== "mouse" || e.button === 0) ? { x: e.clientX, y: e.clientY } : null;
          }}
          onPointerCancel={() => {
            swipe.current = null;
          }}
          onPointerUp={(e) => {
            const s = swipe.current;
            swipe.current = null;
            if (!s || mode !== "pages") return;
            const dx = e.clientX - s.x;
            const dy = e.clientY - s.y;
            if (Math.abs(dx) > 50 && Math.abs(dx) > 1.5 * Math.abs(dy)) go(dx < 0 ? cur + step : cur - step);
            else if (Math.abs(dx) < 12 && Math.abs(dy) < 12) {
              // forward is the common move: the right two thirds
              const r = e.currentTarget.getBoundingClientRect();
              go((e.clientX - r.left) / r.width < 1 / 3 ? cur - step : cur + step);
            }
          }}
        >
          {pages}
        </div>

        {problem && (
          <div className="absolute inset-x-0 bottom-4 mx-auto flex w-fit max-w-[calc(100%-2rem)] items-center gap-3 rounded-full border border-amber-200 bg-white/95 px-4 py-2 text-[15px] text-amber-800 shadow-sm">
            {problem.retrying && <Spinner />}
            <span className="min-w-0">{problem.words}</span>
            {!problem.retrying && (
              <button
                type="button"
                onClick={() => {
                  setProblem(null);
                  setTick((n) => n + 1);
                }}
                className="shrink-0 font-medium underline underline-offset-2"
              >
                Try again
              </button>
            )}
          </div>
        )}

        <div aria-hidden className="pointer-events-none absolute inset-x-0 bottom-0 h-[3px]">
          <div className="h-full bg-teal-600/45" style={{ width: `${Math.round(done * 1000) / 10}%` }} />
        </div>
      </div>
    </div>,
    document.body,
  );
}
