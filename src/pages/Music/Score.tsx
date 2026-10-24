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
// The page (owner, 24 Oct: "you just haven't really worked the interface ui through properly throughout"): a slim
// header (one back named for where it goes, "‹ Room" or "‹ Music"; the title on up to two lines and the composer; Mic
// and AI; ⋯ and the menu), one row (Full screen · Record · Pages | Scroll), the music, and a bar docked at the foot
// (the app's shared Transport with the score's bar steps, Play on, ‹ n / N ›, and the red timer with Stop while the
// piano records). ⋯ is the piece's sheet (Music/bits.tsx PieceSheet: play, playlists, Edit details for your own) with
// "This score": Smaller / Larger notes, Score: Quick | Better, PDF to print (paper, Open PDF). Record is on every score
// with MIDI (ScoreRecord.tsx): the whole piece in one tap, or chosen bars, banded on the score. While a score is open
// the tab's title is "<piece> · Score".
//
// The music stand (owner, 23 Oct: "it should be possible for a pianist to PLAY the score from e.g. an iPad screen"):
// "Full screen", or a tap on the score (Scroll: any page; Pages: the middle third), opens the score full screen
// on white paper, laid out for exactly the screen below its slim bar, again whenever the screen turns: one page, its
// shape the screen's, landscape too. A tap on the right two thirds turns forward, the left third back;
// a swipe; the arrow and page keys, Space and Enter (Bluetooth page-turner pedals). The screen is kept awake while it's
// open. Scroll in the stand stacks the pages at the screen's width instead. The stand's mode and each piece's place are
// kept for the tab's session, so closing and opening again comes back to the same page.

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
  type WheelEvent as ReactWheelEvent,
} from "react";
import { createPortal } from "react-dom";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { ChevronDown, ChevronLeft, ChevronRight, ChevronUp, Circle, FileDown, Maximize2, Menu, MoreHorizontal, Pause, Play, RotateCcw, X } from "lucide-react";
import { BackLink } from "@/components/nav/BackLink";
import { Transport as AppTransport } from "@/components/player/Transport";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import { AppMenu } from "@/components/nav/AppMenu";
import { loadToken, RoomApi, RoomApiError, storageGet, storageSet } from "@/pages/Room/api";
import { useShowFeed } from "@/pages/Room/MusicCard";
import { Spinner } from "@/pages/Room/bits";
import { CARD_CLASS, TAP_BUTTON_CLASS, THEME_CLASS } from "@/pages/Room/theme";
import { isUnreached, musicWords, percent, SCORE_PAPERS, type ScoreKind, type ScorePaper, type ScoreSize, type ScoreStatus } from "./api";
import { CHIP, CHIP_OFF, CHIP_ON, hasScore, PieceSheet, PRIMARY, SCORE_PARAM, SECONDARY, type PieceRef } from "./bits";
import { useMusic } from "./context";
import { BarBand, FollowLight, useFollow, useScoreTransport, type Follow } from "./follow";
import { PlayBar, Segmented, type Watching } from "./PlayBar";
import { RecordChip, RecordSheet, RecordTimer, recordingWords, TakeSheet, useScoreTake, type ScoreTake } from "./ScoreRecord";
import { openPianola } from "@/pages/Room/listenStream";
import { PlayOn } from "@/pages/Room/PlayOn";
import { recPiece } from "@/pages/Room/PianoRecorder";
import { scoreLink } from "@/pages/Room/RecordButton";
import { SpeedNudges } from "@/pages/Room/SpeedNudges";
import { TopToggleButtons, useTopToggles, type TopToggles } from "@/pages/Room/TopToggles";
import type { MusicItem } from "./api";

type Mode = "scroll" | "pages";

const MODE_KEY = "music.score.mode";
const STAFF_KEY = "music.score.staff";
const PAPER_KEY = "music.score.paper";
/** The stand's own mode, and each piece's place in it (a fraction of the way through), for this tab's session. */
const STAND_MODE_KEY = "music.stand.mode";
const standAtKey = (id: string) => `music.stand.at.${id}`;
/** A staff's height in css px, smallest to largest (16 and 20: owner, 23 Oct, "even smaller than the currently
 *  smallest option"; show_service takes 16 at the least). */
const STAFFS = [16, 20, 26, 32, 40, 50, 62];
/** The staff sizes a step either side: laid out ahead on the server, so A+ / A- are ready. */
const nearStaffs = (staff: number) => {
  const i = STAFFS.indexOf(staff);
  return i < 0 ? [] : [STAFFS[i - 1], STAFFS[i + 1]].filter((x): x is number => typeof x === "number");
};
const POLL_MS = 1500;
/** Nothing answered: ask again this often (the words say the page keeps trying). */
const RETRY_MS = 3000;
const RESIZE_MS = 300;
/** The stand lays out again this long after the screen stops changing (a rotation, fullscreen coming or going). */
const STAND_RESIZE_MS = 250;
/** The page turner's row under a page: none (it's in the docked bar; owner, 24 Oct: Pages must fit the screen). */
const PAGER_H = 0;
const MODES: [Mode, string][] = [
  ["pages", "Pages"],
  ["scroll", "Scroll"],
];
const PASSES: [ScoreKind, string][] = [
  ["quick", "Quick"],
  ["better", "Better"],
];
const WATCH: [Watching, string][] = [
  ["score", "Score"],
  ["pianola", "Pianola"],
];
const MORE_HEAD = "mb-1.5 text-sm font-medium text-stone-600";
/** The docked bar's height before it has been measured (so the first layout already leaves room for it). */
const BAR_GUESS_H = 80;
const MIN_H = 420;

const PAGE_CLASS = "block h-auto w-full rounded-sm border border-stone-200 bg-white shadow-sm";
const ROUND =
  "grid h-11 w-11 shrink-0 place-items-center rounded-full border border-stone-300 bg-white text-stone-700 hover:bg-stone-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-40 disabled:hover:bg-white";
const ICON_BUTTON =
  "grid h-11 w-11 shrink-0 place-items-center rounded-full text-stone-700 hover:bg-stone-200/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
/** The stand's bar: quiet, 44 px targets. */
const BAR_BUTTON =
  "grid h-11 min-w-11 shrink-0 place-items-center rounded-full px-1.5 text-[15px] font-medium text-stone-600 hover:bg-stone-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-35 disabled:hover:bg-transparent";
/** The docked bar's page turner. */
const PAGER_BUTTON =
  "grid h-11 w-9 shrink-0 place-items-center rounded-full text-stone-700 hover:bg-stone-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-35";

// ── Turning pages by hand (owner, 23 Oct: "swipe gestures, sensible clicks to move forward / backward ... with suitable
// mouse cursor changes") ──────────────────────────────────────────────────────────────────────────────────────────────
// A page (or the stand's whole screen) has zones: the left third goes back, the right goes forward (the right two thirds
// when nothing sits in the middle: forward is the common move); the view's middle third opens the stand. A mouse sees
// the zone it's over as its cursor (a dark disc with an arrow) and a soft band with a chevron on that side; a click
// there acts. A swipe (touch, pen, or a mouse drag) left goes forward, right back, by distance or by a quick flick; the
// pointer is captured so a swipe that ends off the page still counts. A trackpad's two-finger swipe sideways turns too
// (and, where the page doesn't scroll, a wheel turn up or down), once per gesture.

type Zone = "prev" | "next" | "open";

const cursorSvg = (path: string) =>
  `url("data:image/svg+xml,${encodeURIComponent(
    `<svg xmlns='http://www.w3.org/2000/svg' width='36' height='36'><circle cx='18' cy='18' r='15' fill='rgba(28,25,23,0.82)' stroke='white' stroke-width='1.5'/><path d='${path}' fill='none' stroke='white' stroke-width='2.8' stroke-linecap='round' stroke-linejoin='round'/></svg>`,
  )}") 18 18`;
const CURSORS: Record<string, string> = {
  prev: `${cursorSvg("M20.5 11 13.5 18l7 7")}, w-resize`,
  next: `${cursorSvg("M15.5 11l7 7-7 7")}, e-resize`,
  up: `${cursorSvg("M11 20.5 18 13.5l7 7")}, n-resize`,
  down: `${cursorSvg("M11 15.5l7 7 7-7")}, s-resize`,
};
const SWIPE_PX = 60;          // a swipe this long turns ...
const FLICK_PX = 25;          // ... or a quick flick (this long, at FLICK_V px/ms)
const FLICK_V = 0.35;
const TAP_PX = 10;
const WHEEL_TURN = 90;        // a wheel / trackpad gesture this long turns once ...
const WHEEL_QUIET_MS = 450;   // ... and its momentum is ignored this long

function usePageTurner(o: {
  onPrev: () => void;
  onNext: () => void;
  canPrev: boolean;
  canNext: boolean;
  /** The middle third opens something (the stand) instead of turning. */
  onOpen?: () => void;
  /** Turns go up and down a scrolled page (arrows point up / down; no swipe: the page scrolls by itself). */
  vertical?: boolean;
  /** Which wheel gestures turn: "x" sideways only (the page scrolls up and down itself), "xy" both, none: none. */
  wheel?: "x" | "xy";
  enabled?: boolean;
}) {
  const [zone, setZone] = useState<Zone | null>(null);
  const down = useRef<{ x: number; y: number; t: number } | null>(null);
  const wheelAcc = useRef({ v: 0, quietUntil: 0, last: 0 });
  const opts = useRef(o);
  opts.current = o;
  const on = o.enabled !== false;

  const zoneOf = (e: { clientX: number; currentTarget: Element }): Zone => {
    const r = e.currentTarget.getBoundingClientRect();
    const f = (e.clientX - r.left) / Math.max(1, r.width);
    if (opts.current.onOpen) return f < 1 / 3 ? "prev" : f > 2 / 3 ? "next" : "open";
    return f < 1 / 3 ? "prev" : "next";
  };
  const act = (z: Zone) => {
    const c = opts.current;
    if (z === "prev" && c.canPrev) c.onPrev();
    else if (z === "next" && c.canNext) c.onNext();
    else if (z === "open") c.onOpen?.();
  };

  const handlers = on
    ? {
        onPointerDown: (e: ReactPointerEvent<HTMLElement>) => {
          if (!e.isPrimary || (e.pointerType === "mouse" && e.button !== 0)) {
            down.current = null;
            return;
          }
          const el = e.currentTarget;
          if (e.clientX - el.getBoundingClientRect().left > el.clientWidth) {
            down.current = null; // on the scroll bar: the browser's
            return;
          }
          down.current = { x: e.clientX, y: e.clientY, t: performance.now() };
          if (!opts.current.vertical) {
            try {
              el.setPointerCapture(e.pointerId);
            } catch {
              /* an element that can't capture: the swipe must end on it */
            }
          }
        },
        onPointerMove: (e: ReactPointerEvent<HTMLElement>) => {
          if (e.pointerType !== "mouse" || down.current) return;
          const z = zoneOf(e);
          setZone((cur) => (cur === z ? cur : z));
        },
        onPointerLeave: () => setZone(null),
        onPointerCancel: () => {
          down.current = null;
        },
        onPointerUp: (e: ReactPointerEvent<HTMLElement>) => {
          const s = down.current;
          down.current = null;
          if (!s) return;
          const dx = e.clientX - s.x;
          const dy = e.clientY - s.y;
          const dt = Math.max(1, performance.now() - s.t);
          const c = opts.current;
          if (!c.vertical && Math.abs(dx) > 1.4 * Math.abs(dy) && (Math.abs(dx) >= SWIPE_PX || (Math.abs(dx) >= FLICK_PX && Math.abs(dx) / dt >= FLICK_V))) {
            if (dx < 0) {
              if (c.canNext) c.onNext();
            } else if (c.canPrev) c.onPrev();
            return;
          }
          if (Math.abs(dx) < TAP_PX && Math.abs(dy) < TAP_PX) act(zoneOf(e));
        },
        onWheel: (e: ReactWheelEvent<HTMLElement>) => {
          const c = opts.current;
          if (!c.wheel || e.ctrlKey) return; // ctrl: a pinch (zoom), the browser's
          const sideways = Math.abs(e.deltaX) > Math.abs(e.deltaY);
          if (!sideways && c.wheel === "x") return;
          const now = performance.now();
          const w = wheelAcc.current;
          if (now - w.last > 200) w.v = 0;
          w.last = now;
          if (now < w.quietUntil) return;
          w.v += sideways ? e.deltaX : e.deltaY;
          if (Math.abs(w.v) < WHEEL_TURN) return;
          if (w.v > 0) {
            if (c.canNext) c.onNext();
          } else if (c.canPrev) c.onPrev();
          w.v = 0;
          w.quietUntil = now + WHEEL_QUIET_MS;
        },
      }
    : {};

  let cursor: string | undefined;
  if (on && zone) {
    if (zone === "open") cursor = "zoom-in";
    else if (zone === "prev") cursor = o.canPrev ? CURSORS[o.vertical ? "up" : "prev"] : "default";
    else cursor = o.canNext ? CURSORS[o.vertical ? "down" : "next"] : "default";
  }
  return { handlers, zone: on ? zone : null, cursor };
}

/** The mouse's zone, shown: a soft band with a chevron on the side a click would turn to. */
function ZoneHint({ zone, canPrev, canNext, vertical, withOpen }: { zone: Zone | null; canPrev: boolean; canNext: boolean; vertical?: boolean; withOpen?: boolean }) {
  if (!zone || zone === "open" || (zone === "prev" ? !canPrev : !canNext)) return null;
  const left = zone === "prev";
  const width = left || withOpen ? "33.333%" : "66.667%";
  const Icon = vertical ? (left ? ChevronUp : ChevronDown) : left ? ChevronLeft : ChevronRight;
  return (
    <div
      aria-hidden
      className={cn(
        "pointer-events-none absolute inset-y-0 flex items-center from-stone-900/[0.06] to-transparent",
        left ? "left-0 justify-start bg-gradient-to-r" : "right-0 justify-end bg-gradient-to-l",
      )}
      style={{ width }}
    >
      <span className="mx-3 grid h-11 w-11 place-items-center rounded-full bg-stone-900/70 text-white shadow">
        <Icon className="h-6 w-6" />
      </span>
    </div>
  );
}

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
/** The browser's own full screen only where it is clean: not on an iPad or iPhone, where Safari draws its exit
 *  controls over the top of the page and buries the stand's bar (owner, 23 Oct: "the top nav is still buried
 *  underneath safari ... in webapp mode"), and not in the Home Screen app, which has the whole screen already. */
function fullscreenHelps(): boolean {
  const ios = /iPad|iPhone|iPod/.test(navigator.userAgent) || (/Macintosh/.test(navigator.userAgent) && navigator.maxTouchPoints > 1);
  const standalone = window.matchMedia?.("(display-mode: standalone)").matches || (navigator as Navigator & { standalone?: boolean }).standalone === true;
  return !ios && !standalone;
}

function enterFullscreen() {
  if (!fullscreenHelps()) return;
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

export function ScoreView({
  pieceId,
  toggles,
  onDetails,
  onMenu,
}: {
  pieceId: string;
  /** Kept for the Music page's call; the header's BackLink goes back the same way (history, else Music). */
  onBack?: () => void;
  /** The Mic and AI switches: the Music page's own (it has the show feed), null for none; when not given the score
   *  runs its own. */
  toggles?: TopToggles | null;
  /** Where "Listening details" goes (a long press on a switch); by default /room#listening. */
  onDetails?: () => void;
  /** Opens the app's menu (the Music page's AppMenu); when not given the score has its own. */
  onMenu?: () => void;
}) {
  const { api, libraryRev } = useMusic();
  const location = useLocation();
  const navigate = useNavigate();
  const [mode, setMode] = useState<Mode>(() => (storageGet(MODE_KEY) === "pages" ? "pages" : "scroll"));
  const [paper, setPaper] = useState<ScorePaper>(() => {
    const p = storageGet(PAPER_KEY);
    return SCORE_PAPERS.some(([id]) => id === p) ? (p as ScorePaper) : "a4";
  });
  const [staff, setStaff] = useState(storedStaff);
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);
  const [status, setStatus] = useState<ScoreStatus | null>(null);
  // the piece's name at once from the library (owner, 23 Oct: '"Finding the piece" takes weirdly long'): the score's
  // own answer waits for its layout
  // (and again when the library changes here: Edit details in ⋯)
  const [named, setNamed] = useState<{ pieceId: string; item: MusicItem } | null>(null);
  useEffect(() => {
    const ac = new AbortController();
    api
      .item(pieceId, ac.signal)
      .then((it) => it?.title && setNamed({ pieceId, item: it }))
      .catch(() => undefined);
    return () => ac.abort();
  }, [api, pieceId, libraryRev]);
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
  /** The music stand: where it opened (a fraction of the way through), while it's open. */
  const [stand, setStand] = useState<number | null>(null);
  const standOpenRef = useRef(false);
  standOpenRef.current = stand !== null;
  const standOpen = stand !== null;
  /** While the stand is open the view doesn't lay out again (fullscreen and the scroll lock change its size). */
  const paused = useRef(false);
  const remeasure = useRef<() => void>(() => undefined);
  /** The stand put a history entry on top, so Back (an edge swipe, a phone's Back) closes the stand, not the score. */
  const standEntry = useRef(false);
  /** The piece's ⋯ sheet is open. */
  const [sheetOpen, setSheetOpen] = useState(false);
  /** The score's own menu (when the page didn't pass one). */
  const [menuOpen, setMenuOpen] = useState(false);
  const details = onDetails ?? (() => navigate("/room#listening"));
  /** The docked bar's height (the page is laid out to the screen above it). */
  const barH = useRef(BAR_GUESS_H);
  /** Scroll: the page in view (the docked bar's n / N, and where Record's bars start). */
  const [viewPage, setViewPage] = useState(1);
  /** The stand stopped a take: the take's sheet opens when the stand closes. */
  const standTake = useRef(false);

  // a renamed piece (Edit details in ⋯): the score's answer carries the new names (and the PDF prints them)
  const firstRev = useRef(libraryRev);
  useEffect(() => {
    if (libraryRev !== firstRev.current) setTick((n) => n + 1);
  }, [libraryRev]);

  // The page size: the area's width, by the height left below the controls.
  useLayoutEffect(() => {
    const el = area.current;
    if (!el) return;
    const measure = () => {
      if (paused.current) return;
      const w = Math.max(240, tens(el.clientWidth));
      const top = el.getBoundingClientRect().top + window.scrollY;
      // the layout viewport's height: steady while a phone's address bar comes and goes
      const h = tens(Math.max(MIN_H, document.documentElement.clientHeight - top - 12 - PAGER_H - barH.current));
      // a little more room keeps the pages (no new layout for a few px); less never does (the foot would go under the bar)
      setSize((s) => (s && s.w === w && h >= s.h && h - s.h < 40 ? s : { w, h }));
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
    // the header growing (a line of words, the better score's progress) moves the music down: measure again (the
    // section changes size then; the same size measured again changes nothing)
    if (el.parentElement) ro.observe(el.parentElement);
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
        const st = await api.score(pieceId, { ...size, staff }, ac.signal, { kind: kind ?? "quick", retry, near: nearStaffs(staff) });
        if (ac.signal.aborted) return;
        if (kind === null && st.better === "ready") {
          setKind("better");                     // asked for before: show it
          return;
        }
        setStatus(st);
        setProblem(null);
        if (st.state === "ready") {
          setShown({ st, ...size, staff });
          if (st.partial) timer = setTimeout(ask, POLL_MS); // the rest still being laid out: the page count settles
        }
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
  const viewTurner = usePageTurner({
    onPrev: () => go(cur - 1),
    onNext: () => go(cur + 1),
    canPrev: cur > 1,
    canNext: cur < count,
    onOpen: () => openStandRef.current(cur),
    wheel: "x",
    enabled: mode === "pages" && !!shown && count > 0,
  });
  const openStandRef = useRef<(n?: number) => void>(() => undefined);
  const pageUrl = useCallback(
    (n: number) =>
      shown ? api.scorePageUrl(pieceId, n, { w: shown.w, h: shown.h, staff: shown.staff, rev: shown.st.rev, kind: shown.st.kind }) : "",
    [api, pieceId, shown],
  );
  // follow along: the player's place lights the bar and notes playing; the page keeps up with it
  const layout = useMemo(
    () => (shown ? { w: shown.w, h: shown.h, staff: shown.staff, kind: shown.st.kind, rev: `${shown.st.rev}~${shown.st.pages}` } : null),
    [shown],
  );
  const follow = useFollow(pieceId, standOpenRef.current ? null : layout);
  const followPage = follow.head.playing || follow.head.starting ? follow.head.page : null;
  useEffect(() => {
    if (!followPage || standOpenRef.current) return;
    if (mode === "pages") setPage(followPage);
    else imgs.current[followPage - 1]?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [followPage, mode]);
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
  openStandRef.current = openStand;
  // ?stand=1 (the pianola's Watch: Score): straight into full screen once the pages are there
  const [params, setParams] = useSearchParams();
  const wantStand = params.get("stand") === "1";
  useEffect(() => {
    if (!wantStand || !shown || !count || stand !== null) return;
    setParams(
      (p) => {
        const next = new URLSearchParams(p);
        next.delete("stand");
        return next;
      },
      { replace: true, state: location.state }, // where it was opened from stays (the back control's words)
    );
    openStandRef.current();
  }, [wantStand, shown, count, stand, setParams, location.state]);
  const early = named?.pieceId === pieceId ? named.item : null;
  const title = status?.title || shown?.st.title || early?.title || (problem ? "Score" : null);
  const composer = status?.composer ?? shown?.st.composer ?? early?.composer ?? null;
  const editable = !!(status?.editable ?? shown?.st.editable ?? early?.editable);
  const retry = () => {
    setProblem(null);
    retryNext.current = true;
    setTick((n) => n + 1);
  };
  const choosePass = (k: ScoreKind) => {
    if (k === "better" && status?.better === "failed") retryNext.current = true;
    setKind(k);
  };
  // the tab's title while the score is open (owner rule: say what's on screen), put back as it closes
  useEffect(() => {
    if (!title) return;
    const prev = document.title;
    document.title = `${title} · Score`;
    return () => {
      document.title = prev;
    };
  }, [title]);
  // the better pass being written (or failed) while the quick score is on screen
  const betterBusy = !!shown && status?.kind === "better" && (status.state === "queued" || status.state === "making");
  const betterFailed = !!shown && status?.kind === "better" && status.state === "failed";
  const shownKind = shown?.st.kind ?? "quick";

  // Scroll: the page in view, kept as the page scrolls (the docked bar's n / N; where Record's bars start).
  const scrollPage = useCallback(() => {
    const mid = window.innerHeight / 2;
    let n = 1;
    imgs.current.slice(0, count).forEach((el, i) => {
      if (el && el.getBoundingClientRect().top <= mid) n = i + 1;
    });
    return n;
  }, [count]);
  useEffect(() => {
    if (mode !== "scroll" || !count) return;
    let raf = 0;
    const on = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => setViewPage(scrollPage()));
    };
    on();
    window.addEventListener("scroll", on, { passive: true });
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("scroll", on);
    };
  }, [mode, count, scrollPage]);
  const pageHere = mode === "pages" ? cur : Math.min(Math.max(1, viewPage), Math.max(1, count));
  const toPage = (n: number) => {
    const p = Math.min(Math.max(1, n), Math.max(1, count));
    if (mode === "pages") go(p);
    else imgs.current[p - 1]?.scrollIntoView({ block: "start", behavior: "smooth" });
  };

  // Record on the piano (every score with MIDI): the app's one recorder
  const take = useScoreTake({
    baseUrl: api.baseUrl,
    pieceId,
    bars: follow.data?.bars ?? null,
    hereBar: follow.head.mine && follow.head.bar !== null ? follow.head.bar : follow.firstBarOnPage(pageHere),
  });
  const canRecord = hasScore({ id: pieceId, kind: early?.kind }) && !problem?.noMidi;
  // These bars chosen (or From changed in the sheet): the score turns or scrolls to the first of them
  useEffect(() => {
    const first = take.from;
    if (!take.jump || first === null) return;
    const i = follow.data?.pages.findIndex(([a, z]) => a <= first && first <= z) ?? -1;
    if (i < 0) return;
    if (mode === "pages") setPage(i + 1);
    else imgs.current[i]?.scrollIntoView({ block: "start", behavior: "smooth" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [take.jump]);
  const picking = take.what === "section" && !take.r.recording;
  const band = (n: number) =>
    layout && take.what === "section" ? (
      <BarBand pieceId={pieceId} n={n} layout={layout} from={take.from} to={take.to} onPick={picking ? take.pick : undefined} />
    ) : null;

  /** Closed at this place (by Back: popped): the view follows it (Pages turns to it, Scroll scrolls to it once
   * fullscreen has gone). A take stopped in the stand opens in its sheet. */
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
    if (standTake.current && take.r.fresh) take.setTakeOpen(true);
    standTake.current = false;
  };
  /** The stand's "New take · Score": keep it, leave the stand, open the take's score. */
  const takeScore = async (at: number) => {
    const k = await take.r.keep();
    if (!k) return;
    standTake.current = false;
    const l = scoreLink(recPiece(k.id), "Score");
    const open = () => navigate(l.to, { state: l.state });
    const popping = standEntry.current;
    if (popping) window.addEventListener("popstate", () => setTimeout(open, 0), { once: true });
    closeStand(at);
    if (!popping) open();
  };

  const backTo = useMemo(() => {
    const q = new URLSearchParams(location.search);
    q.delete(SCORE_PARAM);
    q.delete("stand");
    const qs = q.toString();
    return `/music${qs ? `?${qs}` : ""}`;
  }, [location.search]);
  const pieceRef: PieceRef = early
    ? { id: early.id, title: early.title, composer: early.composer, genre: early.genre, kind: early.kind, pinned: early.pinned, editable: early.editable }
    : { id: pieceId, title: title || "Score", composer, editable };
  const pdfUrl = shown ? api.scorePdfUrl(pieceId, paper, shown.st.rev, shown.st.kind, tag(`${title ?? ""}\n${composer ?? ""}`)) : null;
  const ready = !!shown && count > 0;

  // ⋯'s own group: this score's notes, pass and paper
  const scoreGroup = (close: () => void) => (
    <div className="mt-3 space-y-4 border-t border-stone-200 px-1 pt-4" data-testid="score-sheet-group">
      <p className="text-base font-semibold text-stone-800">This score</p>
      <div className="flex flex-wrap items-center gap-2">
        <span className="mr-auto text-[15px] text-stone-700">Notes</span>
        <button type="button" disabled={si <= 0} onClick={() => resize(-1)} className={cn(CHIP, CHIP_OFF, "disabled:opacity-40")}>
          A− Smaller
        </button>
        <button type="button" disabled={si >= STAFFS.length - 1} onClick={() => resize(1)} className={cn(CHIP, CHIP_OFF, "disabled:opacity-40")}>
          A+ Larger
        </button>
      </div>
      {ready && (
        <div className="space-y-1.5">
          <p className="text-[15px] text-stone-700">Score</p>
          <Segmented label="Which score" value={kind ?? shownKind} options={PASSES} onChange={choosePass} />
          <p className="text-sm text-muted-foreground">Better: a closer reading of beat, bars and hands; a minute or two.</p>
        </div>
      )}
      {ready && pdfUrl && (
        <div className="space-y-2">
          <p className="text-[15px] text-stone-700">PDF to print</p>
          <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label="Paper">
            {SCORE_PAPERS.map(([id, words]) => (
              <button
                key={id}
                type="button"
                role="radio"
                aria-checked={paper === id}
                onClick={() => {
                  setPaper(id);
                  storageSet(PAPER_KEY, id);
                }}
                className={cn(CHIP, paper === id ? CHIP_ON : CHIP_OFF)}
              >
                {words}
              </button>
            ))}
          </div>
          <a
            href={pdfUrl}
            target="_blank"
            rel="noopener"
            onClick={close}
            className={cn(TAP_BUTTON_CLASS, SECONDARY, "inline-flex items-center gap-2 font-medium")}
          >
            <FileDown className="h-4 w-4" aria-hidden /> Open PDF
          </a>
        </div>
      )}
    </div>
  );

  let body: ReactNode = null;
  if (shown && count > 0) {
    body =
      mode === "scroll" ? (
        <div className="space-y-2">
          {Array.from({ length: count }, (_, i) => (
            <div key={i} className="relative">
              <img
                ref={(el) => {
                  imgs.current[i] = el;
                }}
                src={pageUrl(i + 1)}
                alt={`Page ${i + 1} of ${count}`}
                width={shown.w}
                height={shown.h}
                loading="lazy"
                decoding="async"
                title="Full screen from this page"
                onClick={() => openStand(i + 1)}
                className={cn(PAGE_CLASS, "cursor-zoom-in")}
              />
              {layout && <FollowLight pieceId={pieceId} n={i + 1} layout={layout} head={follow.head} />}
              {band(i + 1)}
            </div>
          ))}
        </div>
      ) : (
        <div
          className="relative select-none"
          style={{ touchAction: "pan-y pinch-zoom", cursor: viewTurner.cursor }}
          title="Click the right of the page for the next, the left for the previous; the middle opens it full screen"
          {...viewTurner.handlers}
        >
          {/* the page before and the two after stay mounted and painted (see-through), so a turn is instant */}
          {[cur - 1, cur, cur + 1, cur + 2]
            .filter((n) => n >= 1 && n <= count)
            .map((n) => (
              <img
                key={`p${n}`}
                src={pageUrl(n)}
                alt={n === cur ? `Page ${cur} of ${count}` : ""}
                aria-hidden={n !== cur}
                width={shown.w}
                height={shown.h}
                decoding="async"
                draggable={false}
                className={cn(PAGE_CLASS, n !== cur && "pointer-events-none absolute inset-x-0 top-0 opacity-0")}
              />
            ))}
          {layout && <FollowLight pieceId={pieceId} n={cur} layout={layout} head={follow.head} />}
          <ZoneHint zone={viewTurner.zone} canPrev={cur > 1} canNext={cur < count} withOpen />
          {band(cur)}
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
      <header className="space-y-2" data-testid="score-header">
        <div className="flex min-w-0 items-center gap-1">
          <BackLink to={backTo} testId="score-back" />
          <span className="flex-1" />
          {toggles === undefined ? (
            <OwnToggles baseUrl={api.baseUrl} render={(t) => <TopToggleButtons t={t} onDetails={details} />} />
          ) : (
            toggles && <TopToggleButtons t={toggles} onDetails={details} />
          )}
          <button
            type="button"
            onClick={() => setSheetOpen(true)}
            aria-label="More: this piece and this score"
            title="This piece and this score"
            className={ICON_BUTTON}
            data-testid="score-more"
          >
            <MoreHorizontal className="h-6 w-6" aria-hidden />
          </button>
          <button type="button" onClick={onMenu ?? (() => setMenuOpen(true))} aria-label="Menu" className={cn(ICON_BUTTON, "-mr-2")} data-testid="menu-button">
            <Menu className="h-6 w-6" aria-hidden />
          </button>
        </div>
        <div className="min-w-0">
          {title ? (
            <h1 className="break-words text-[22px] font-semibold leading-tight tracking-tight">{title}</h1>
          ) : (
            <p className="flex h-7 items-center gap-2 text-base text-muted-foreground">
              <Spinner /> Finding the piece…
            </p>
          )}
          {composer && <p className="break-words text-[15px] leading-5 text-muted-foreground">{composer}</p>}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {ready && (
            <button
              type="button"
              onClick={() => openStand()}
              title="Full screen, to play from: tap the right of the page to turn forward, the left to go back"
              className={cn(CHIP, PRIMARY, "border-transparent font-semibold")}
            >
              <Maximize2 className="h-4 w-4" aria-hidden /> Full screen
            </button>
          )}
          {ready && canRecord && <RecordChip take={take} />}
          <Segmented label="How to read it" value={mode} options={MODES} onChange={choose} />
          {relaying && !problem && <Spinner className="ml-auto h-5 w-5 text-muted-foreground" />}
        </div>
        {picking && (
          <p className="text-sm text-muted-foreground" data-testid="score-bars-hint">
            {take.span ? `Record will take ${take.span}.` : "Choose the bars."} Tap a bar on the score to start there, then the bar to end on.
          </p>
        )}
      </header>

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

      {/* only the music: the page is laid out to the height from here down to the docked bar */}
      <div ref={area}>{body}</div>

      {ready && (
        <ScoreBar
          follow={follow}
          pageInView={pageHere}
          count={count}
          onPage={toPage}
          take={take}
          onHeight={(h) => {
            if (Math.abs(h - barH.current) < 2) return;
            barH.current = h;
            remeasure.current();
          }}
        />
      )}

      <PieceSheet piece={sheetOpen ? pieceRef : null} onClose={() => setSheetOpen(false)} extra={(_p, close) => scoreGroup(close)} />
      <RecordSheet take={take} editable={editable} />
      <TakeSheet take={take} />
      {!onMenu && <AppMenu open={menuOpen} onOpenChange={setMenuOpen} current="music" />}

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
          take={canRecord ? take : null}
          onTakeStopped={() => {
            standTake.current = true;
          }}
          onTakeScore={(at) => void takeScore(at)}
        />
      )}
    </section>
  );
}

const noop = () => undefined;

/** Mic and AI when the page didn't pass its own: the score's own show feed (the room's two switches). */
function OwnToggles({ baseUrl, render }: { baseUrl: string; render: (t: TopToggles) => ReactNode }) {
  const api = useMemo(() => new RoomApi(baseUrl, loadToken(baseUrl).token), [baseUrl]);
  const feed = useShowFeed(api, true, noop);
  const t = useTopToggles(api, feed, noop);
  return <>{render(t)}</>;
}

/** The score's bar, docked at the foot of the screen (in the app dock's place, which a score doesn't show): the shared
 *  Transport with the score's bar steps (⋯ More: Stop, Speed, Sound, Play on on a phone, Watch), Play on, ‹ n / N ›,
 *  and while the piano records a first line with the red timer and Stop. Its height is --dock-h (the page's foot clears
 *  it, the Music page's messages sit above it). */
function ScoreBar({
  follow,
  pageInView,
  count,
  onPage,
  take,
  onHeight,
}: {
  follow: Follow;
  pageInView: number;
  count: number;
  onPage: (n: number) => void;
  take: ScoreTake;
  onHeight: (h: number) => void;
}) {
  const { api, player } = useMusic();
  const t = useScoreTransport(follow, pageInView);
  const ref = useRef<HTMLDivElement>(null);
  const heightRef = useRef(onHeight);
  heightRef.current = onHeight;
  useLayoutEffect(() => {
    const root = document.documentElement;
    const el = ref.current;
    if (!el) return;
    const set = () => {
      const h = Math.ceil(el.getBoundingClientRect().height);
      root.style.setProperty("--dock-h", `${h}px`);
      heightRef.current(h);
    };
    set();
    const ro = new ResizeObserver(set);
    ro.observe(el);
    return () => {
      ro.disconnect();
      root.style.removeProperty("--dock-h");
      heightRef.current(0);
    };
  }, []);
  return (
    <div
      ref={ref}
      className={cn(THEME_CLASS, "pointer-events-none fixed inset-x-0 bottom-0 z-40 px-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] pt-2 text-base antialiased")}
      data-testid="score-bar"
    >
      <div
        className={cn(
          CARD_CLASS,
          "pointer-events-auto mx-auto w-full max-w-3xl space-y-1.5 px-1.5 py-1.5 sm:px-3",
          "shadow-[0_2px_4px_rgba(68,48,28,0.08),0_12px_32px_-8px_rgba(68,48,28,0.28)]",
        )}
      >
        {take.r.recording && (
          <div className="flex items-center gap-2 border-b border-stone-200/80 px-1.5 pb-1.5" data-testid="score-bar-recording">
            <span aria-hidden className="h-2.5 w-2.5 shrink-0 rounded-full bg-rose-600 motion-safe:animate-pulse" />
            <p className="min-w-0 flex-1 truncate text-[15px] font-semibold text-rose-900">{recordingWords(take)}</p>
            <RecordTimer take={take} />
          </div>
        )}
        <div className="flex min-w-0 items-center gap-1">
          <AppTransport
            player={player}
            compact
            steps={t.steps}
            play={t.play}
            pieceSteps={false}
            more={
              <>
                <div className="sm:hidden">
                  <p className={MORE_HEAD}>Play on</p>
                  <PlayOn baseUrl={api.baseUrl} label={null} />
                </div>
                <div>
                  <p className={MORE_HEAD}>Watch</p>
                  <Segmented label="Watch" value={"score" as Watching} options={WATCH} onChange={(w) => w === "pianola" && openPianola()} />
                </div>
              </>
            }
          />
          <PlayOn baseUrl={api.baseUrl} compact className="max-sm:hidden" />
          <div className="ml-auto flex shrink-0 items-center" role="group" aria-label="Pages">
            <button type="button" aria-label="Previous page" disabled={pageInView <= 1} onClick={() => onPage(pageInView - 1)} className={PAGER_BUTTON}>
              <ChevronLeft className="h-5 w-5" aria-hidden />
            </button>
            <span className="min-w-[3.25rem] text-center text-[15px] tabular-nums text-stone-700" aria-live="polite" data-testid="score-bar-page">
              {pageInView} / {count}
            </span>
            <button type="button" aria-label="Next page" disabled={pageInView >= count} onClick={() => onPage(pageInView + 1)} className={PAGER_BUTTON}>
              <ChevronRight className="h-5 w-5" aria-hidden />
            </button>
          </div>
        </div>
      </div>
    </div>
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
  /** Record on the piano (null: this piece has no MIDI). */
  take: ScoreTake | null;
  /** A take was stopped here (its sheet opens when the stand closes). */
  onTakeStopped: () => void;
  /** The "New take · Score" line's Score: leave the stand at this place, open the take's score. */
  onTakeScore: (at: number) => void;
}

/** The music stand: the score full screen, to play from (see the top of the file). */
function MusicStand({ pieceId, title, kind, staff, canSmaller, canLarger, onResize, start, seed, onClose, take, onTakeStopped, onTakeScore }: StandProps) {
  const { api, player } = useMusic();
  const [moreOpen, setMoreOpen] = useState(false);
  /** "New take · Score", for a while after a take stops here. */
  const [newTake, setNewTake] = useState(false);
  useEffect(() => {
    if (!newTake) return;
    const t = setTimeout(() => setNewTake(false), 12_000);
    return () => clearTimeout(t);
  }, [newTake]);
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
  /** The page images that have loaded (their URLs): the one shown not yet loaded shows a spinner. */
  const [loaded, setLoaded] = useState<Set<string>>(() => new Set());
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

  // One page, laid out to the screen's own shape, landscape too (owner, 23 Oct: "in landscape full screen on an ipad,
  // I don't want to see 2 columns in score mode"); the two-page spread stays off.
  const spread = false as boolean;
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
        const st = await api.score(pieceId, want, ac.signal, { kind, near: nearStaffs(want.staff) });
        if (ac.signal.aborted) return;
        if (st.state === "ready") {
          setProblem(null);
          setShown({ st, ...want });
          if (st.partial) timer = setTimeout(ask, POLL_MS);
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
  // follow along on the stand's own layout: the light, and the pages keep up with the music
  const layout = useMemo(() => ({ w: shown.w, h: shown.h, staff: shown.staff, kind: shown.st.kind, rev: `${shown.st.rev}~${shown.st.pages}` }), [shown]);
  const follow: Follow = useFollow(pieceId, layout);

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

  // while it plays, the pages keep up: the spread (or page) with the bar playing is the one shown
  const followPage = follow.head.playing || follow.head.starting ? follow.head.page : null;
  useEffect(() => {
    if (!followPage) return;
    if (mode === "pages") {
      const first = followPage - ((followPage - 1) % step);
      if (first !== cur) go(first);
    } else {
      box.current?.querySelector(`[data-stand-page="${followPage}"]`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [followPage, mode, step]);

  // Every page of this layout comes into the browser's cache, nearest first, one at a time (the server draws them
  // ahead too): a turn, or a jump, never waits on the network. The window of mounted pages below makes it instant.
  const fetchedRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    if (mode !== "pages" || !count) return;
    let stop = false;
    const order: number[] = [];
    for (let d = 0; d <= count; d++) {
      for (const n of d ? [cur + d, cur - d] : [cur]) if (n >= 1 && n <= count && !order.includes(n)) order.push(n);
    }
    void (async () => {
      for (const n of order) {
        if (stop) return;
        const url = pageUrl(n);
        if (fetchedRef.current.has(url)) continue;
        try {
          const r = await fetch(url, { cache: "force-cache" });
          if (r.ok) {
            await r.blob();
            fetchedRef.current.add(url);
          }
        } catch {
          return; // offline or the size changed under us: the visible pages still load as images
        }
      }
    })();
    return () => {
      stop = true;
    };
  }, [mode, count, cur, pageUrl]);

  const close = useCallback(() => onClose(at.current), [onClose]);
  const playParts = useScoreTransport(follow, cur);
  const speed = typeof player.view?.speed === "number" ? player.view.speed : null;
  const stopTake = async () => {
    if (!take) return;
    const t = await take.stop(false);
    if (t) {
      onTakeStopped();
      setNewTake(true);
    }
  };
  /** Record (what the score has chosen), or the red dot with the time and Stop while it records. */
  const recordControl = (cls?: string) =>
    !take || !take.available ? null : take.r.recording ? (
      <RecordTimer take={take} onStop={() => void stopTake()} className={cn("h-9 px-2.5", cls)} />
    ) : (
      <button
        type="button"
        aria-label={take.what === "section" && take.span ? `Record ${take.span}` : "Record"}
        title={take.what === "section" && take.span ? `Record ${take.span}` : "Record on the piano"}
        disabled={take.r.busy === "record"}
        onClick={() => void take.start()}
        className={cn(BAR_BUTTON, cls)}
        data-testid="stand-record"
      >
        {take.r.busy === "record" ? <Spinner /> : <Circle className="h-4 w-4 fill-rose-600 text-rose-600" aria-hidden />}
      </button>
    );

  // Clicks, swipes and trackpad gestures turn the pages (Pages), or move a screen up or down (Scroll).
  const scrollScreen = (d: number) => {
    const el = box.current;
    if (el) el.scrollBy({ top: d * el.clientHeight * 0.85, behavior: "smooth" });
  };
  const pagesTurner = usePageTurner({
    onPrev: () => go(cur - step),
    onNext: () => go(cur + step),
    canPrev: cur > 1,
    canNext: cur < last,
    wheel: "xy",
    enabled: mode === "pages",
  });
  const scrollCanPrev = scrolled.done > 0 && (box.current?.scrollTop ?? 0) > 1;
  const scrollCanNext = scrolled.done < 0.999;
  const scrollTurner = usePageTurner({
    onPrev: () => scrollScreen(-1),
    onNext: () => scrollScreen(1),
    canPrev: scrollCanPrev,
    canNext: scrollCanNext,
    vertical: true,
    enabled: mode === "scroll",
  });
  const turner =
    mode === "pages"
      ? { ...pagesTurner, canPrev: cur > 1, canNext: cur < last }
      : { ...scrollTurner, canPrev: scrollCanPrev, canNext: scrollCanNext };

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
    <div key={key} className="relative shrink-0" style={{ width: slot.w, height: slot.h }}>
    <img
      src={pageUrl(n)}
      alt={`Page ${n} of ${count}`}
      draggable={false}
      onLoad={(e) => {
        const src = e.currentTarget.getAttribute("src") ?? "";
        setLoaded((m) => (m.has(src) ? m : new Set(m).add(src)));
      }}
      className="block max-h-full max-w-full shrink-0 object-contain"
      style={{ width: slot.w, height: slot.h }}
    />
    <FollowLight pieceId={pieceId} n={n} layout={layout} head={follow.head} />
    </div>
  );

  let pages: ReactNode;
  if (!count) pages = null;
  else if (mode === "scroll")
    pages = (
      <div className="divide-y divide-stone-200">
        {Array.from({ length: count }, (_, i) => (
          <div key={i} className="relative" data-stand-page={i + 1}>
            <img
              src={pageUrl(i + 1)}
              alt={`Page ${i + 1} of ${count}`}
              width={shown.w}
              height={shown.h}
              loading="lazy"
              decoding="async"
              draggable={false}
              className="block h-auto w-full"
            />
            <FollowLight pieceId={pieceId} n={i + 1} layout={layout} head={follow.head} />
          </div>
        ))}
      </div>
    );
  else {
    // the spread shown, the one before and the two after, all mounted and painted (the hidden ones see-through), so a
    // turn swaps layers that are already decoded: nothing loads at the tap
    const firsts: number[] = [];
    for (let f = cur - step; f <= cur + 2 * step; f += step) if (f >= 1 && f <= count) firsts.push(f);
    pages = (
      <div className="relative h-full w-full">
        {firsts.map((f) => {
          const here = f === cur;
          return (
            <div
              key={`spread-${f}`}
              aria-hidden={!here}
              className="absolute inset-0 flex items-center justify-center"
              style={{ opacity: here ? 1 : 0, zIndex: here ? 1 : 0 }}
            >
              {pageImg(f, `p${f}`)}
              {spread && (
                <>
                  <div aria-hidden className="w-px shrink-0 bg-stone-200" style={{ height: slot.h * 0.92 }} />
                  {f + 1 <= count ? pageImg(f + 1, `p${f + 1}`) : <div aria-hidden className="shrink-0" style={{ width: slot.w, height: slot.h }} />}
                </>
              )}
            </div>
          );
        })}
        {!loaded.has(pageUrl(cur)) && (
          <div className="pointer-events-none absolute right-3 top-3 z-10 grid h-9 w-9 place-items-center rounded-full bg-white/90 shadow">
            <Spinner className="text-stone-500" />
          </div>
        )}
      </div>
    );
  }

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
        paddingTop: "var(--app-top)", // clear of the iPad Home Screen app's status bar and edge blur (index.css)
        paddingRight: "env(safe-area-inset-right)",
        paddingBottom: "env(safe-area-inset-bottom)",
        paddingLeft: "env(safe-area-inset-left)",
      }}
    >
      <div
        className="flex h-12 shrink-0 items-center gap-0.5 border-b border-stone-100 pl-1 pr-1 text-stone-600 sm:pl-3"
        // a tap on the bar hands the keys back to the page (a pedal's Space or Enter mustn't press the last button)
        onClick={(e) => {
          if (e.detail > 0) root.current?.focus({ preventScroll: true });
        }}
      >
        {/* a phone: Close · ⏯ · n / N · Record · ⋯ (the rest in ⋯) */}
        <div className="flex min-w-0 flex-1 items-center gap-0.5 sm:hidden" data-testid="stand-bar-phone">
          <button type="button" aria-label="Close" title="Close (Esc)" onClick={close} className={BAR_BUTTON}>
            <X className="h-5 w-5" aria-hidden />
          </button>
          <button
            type="button"
            aria-label={playParts.play.label}
            title={playParts.play.label}
            onClick={playParts.play.onClick}
            className="relative grid h-10 w-10 shrink-0 place-items-center rounded-full bg-teal-700 text-white hover:bg-teal-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {playParts.play.starting && <span aria-hidden className="absolute inset-0 rounded-full border-2 border-white/70 border-t-transparent motion-safe:animate-spin" />}
            {playParts.play.going ? <Pause className="h-5 w-5" aria-hidden /> : <Play className="h-5 w-5 translate-x-px" aria-hidden />}
          </button>
          <span className="px-1.5 text-[15px] tabular-nums text-stone-600" aria-live="polite">
            {where}
          </span>
          <span className="flex-1" />
          {relaying && !problem && <Spinner className="mx-1 text-stone-400" />}
          {recordControl()}
          <Popover open={moreOpen} onOpenChange={setMoreOpen}>
            <PopoverTrigger asChild>
              <button type="button" aria-label="More: notes, pages, play on, speed, pianola" className={BAR_BUTTON} data-testid="stand-more">
                <MoreHorizontal className="h-5 w-5" aria-hidden />
              </button>
            </PopoverTrigger>
            <PopoverContent
              align="end"
              collisionPadding={12}
              className={cn(
                THEME_CLASS,
                "z-[70] max-h-[var(--radix-popover-content-available-height)] w-[min(22rem,calc(100vw-2rem))] space-y-4 overflow-y-auto rounded-2xl p-3",
              )}
              onClick={(e) => e.stopPropagation()}
            >
              <div className="flex items-center gap-2">
                <span className="mr-auto text-[15px] text-stone-700">Notes</span>
                <button type="button" disabled={!canSmaller} onClick={() => onResize(-1)} className={cn(CHIP, CHIP_OFF, "disabled:opacity-40")}>
                  A−
                </button>
                <button type="button" disabled={!canLarger} onClick={() => onResize(1)} className={cn(CHIP, CHIP_OFF, "disabled:opacity-40")}>
                  A+
                </button>
              </div>
              <Segmented label="How to read it" value={mode} options={MODES} onChange={chooseMode} />
              <div>
                <p className={MORE_HEAD}>Play on</p>
                <PlayOn baseUrl={api.baseUrl} label={null} />
              </div>
              {speed !== null && (
                <div>
                  <SpeedNudges speed={speed} onSpeed={(v) => void player.act({ action: "set", speed: v }, { speed: v })} />
                </div>
              )}
              <button
                type="button"
                onClick={() => {
                  setMoreOpen(false);
                  close(); // out of full screen first: the pianola is drawn over the page
                  openPianola();
                }}
                className={cn(TAP_BUTTON_CLASS, SECONDARY, "w-full font-medium")}
              >
                Watch the pianola
              </button>
            </PopoverContent>
          </Popover>
        </div>

        {/* a tablet and up: the whole bar */}
        <div className="hidden min-w-0 flex-1 items-center gap-0.5 sm:flex" data-testid="stand-bar">
          <div className="min-w-0 flex-1">
            <p className="truncate text-[15px] font-medium text-stone-700" title={title}>
              {title}
            </p>
          </div>
          {relaying && !problem && <Spinner className="mx-1 text-stone-400" />}
          <PlayBar
            baseUrl={api.baseUrl}
            follow={follow}
            pageInView={cur}
            compact
            watching="score"
            onWatch={(w) => {
              if (w !== "pianola") return;
              close(); // out of full screen first: the pianola is drawn over the page
              openPianola();
            }}
            className="flex-nowrap"
          />
          <span className="px-1.5 text-[15px] tabular-nums text-stone-600" aria-live="polite">
            {where}
          </span>
          {recordControl()}
          <button type="button" aria-label="Smaller notes" title="Smaller notes" disabled={!canSmaller} onClick={() => onResize(-1)} className={BAR_BUTTON}>
            A−
          </button>
          <button type="button" aria-label="Larger notes" title="Larger notes" disabled={!canLarger} onClick={() => onResize(1)} className={BAR_BUTTON}>
            A+
          </button>
          <div role="radiogroup" aria-label="How to read it" className="flex">
            {MODES.map(([m, words]) => (
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
      </div>

      <div className="relative min-h-0 flex-1">
        <div
          ref={box}
          className={cn(
            "absolute inset-0 select-none overscroll-contain",
            mode === "scroll" ? "overflow-y-auto overflow-x-hidden" : "overflow-hidden",
          )}
          style={{
            overflowAnchor: "none",
            WebkitTouchCallout: "none",
            touchAction: mode === "scroll" ? "pan-y pinch-zoom" : "pinch-zoom",
            cursor: turner.cursor,
          }}
          onScroll={mode === "scroll" ? onScroll : undefined}
          {...turner.handlers}
        >
          {pages}
        </div>
        <ZoneHint zone={turner.zone} canPrev={turner.canPrev} canNext={turner.canNext} vertical={mode === "scroll"} />

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

        {newTake && take?.r.fresh && (
          <div
            role="status"
            className="absolute inset-x-0 bottom-4 z-10 mx-auto flex w-fit max-w-[calc(100%-2rem)] items-center gap-3 rounded-full bg-stone-900/90 py-1.5 pl-4 pr-1.5 text-[15px] font-medium text-white shadow-lg"
            data-testid="stand-new-take"
          >
            <span className="min-w-0 truncate">New take</span>
            <button
              type="button"
              onClick={() => onTakeScore(at.current)}
              className="h-9 shrink-0 rounded-full bg-white px-3.5 text-[15px] font-semibold text-stone-900 hover:bg-stone-100"
            >
              Score
            </button>
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
