// A piece's score on /music (owner, 23 Oct): the whole piece written out from its MIDI by the live score's own
// generator (show_service/score_sheet.py), no animation, to scroll through or read a page at a time. Opened from a
// piece's ⋯ (Score) or from Now playing; the address carries it (?score=<piece id>), so Back closes it.
//
// The first time a piece's score is opened it is written (about a quarter of the piece's length): the page shows how
// far it has got and asks again every 1.5 s, which also keeps the server writing it (paused while the tab is hidden).
// After that it opens at once. Pages are SVG images laid out server side for exactly this screen: the score area's
// width by the height left below the controls (less the page turner's row), at the chosen staff size (A− / A+). Scroll
// shows every page down the screen; Pages shows one at a time (‹ ›, the arrow keys, a swipe, or a tap on the page's
// left or right third). Both use the same pages, so switching is instant.

import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { ArrowLeft, ChevronLeft, ChevronRight, RotateCcw } from "lucide-react";
import { cn } from "@/lib/utils";
import { RoomApiError, storageGet, storageSet } from "@/pages/Room/api";
import { Spinner } from "@/pages/Room/bits";
import { CARD_CLASS, TAP_BUTTON_CLASS } from "@/pages/Room/theme";
import { isUnreached, musicWords, percent, type ScoreStatus } from "./api";
import { CHIP, CHIP_OFF, CHIP_ON, SECONDARY } from "./bits";
import { useMusic } from "./context";

type Mode = "scroll" | "pages";

const MODE_KEY = "music.score.mode";
const STAFF_KEY = "music.score.staff";
/** A staff's height in css px, smallest to largest. */
const STAFFS = [26, 32, 40, 50, 62];
const POLL_MS = 1500;
/** Nothing answered: ask again this often (the words say the page keeps trying). */
const RETRY_MS = 3000;
const RESIZE_MS = 300;
/** The page turner's row under a page (kept in both modes, so switching doesn't lay the score out again). */
const PAGER_H = 52;
const MIN_H = 420;

const PAGE_CLASS = "block h-auto w-full rounded-sm border border-stone-200 bg-white shadow-sm";
const ROUND =
  "grid h-11 w-11 shrink-0 place-items-center rounded-full border border-stone-300 bg-white text-stone-700 hover:bg-stone-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-40 disabled:hover:bg-white";

const tens = (x: number) => Math.max(0, Math.floor(x / 10) * 10);

function storedStaff(): number {
  const s = Number(storageGet(STAFF_KEY));
  return STAFFS.includes(s) ? s : window.innerWidth < 640 ? 32 : 40;
}

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
  const { api } = useMusic();
  const [mode, setMode] = useState<Mode>(() => (storageGet(MODE_KEY) === "pages" ? "pages" : "scroll"));
  const [staff, setStaff] = useState(storedStaff);
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);
  const [status, setStatus] = useState<ScoreStatus | null>(null);
  const [shown, setShown] = useState<Shown | null>(null);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [tick, setTick] = useState(0);
  const [page, setPage] = useState(1);
  const area = useRef<HTMLDivElement>(null);
  const imgs = useRef<(HTMLImageElement | null)[]>([]);
  const scrollToPage = useRef(0);
  const swipe = useRef<{ x: number; y: number } | null>(null);

  // The page size: the area's width, by the height left below the controls.
  useLayoutEffect(() => {
    const el = area.current;
    if (!el) return;
    const measure = () => {
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
    const ro = new ResizeObserver(later);
    ro.observe(el);
    window.addEventListener("resize", later);
    return () => {
      clearTimeout(t);
      ro.disconnect();
      window.removeEventListener("resize", later);
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
        const st = await api.score(pieceId, { ...size, staff }, ac.signal);
        if (ac.signal.aborted) return;
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
  }, [api, pieceId, size, staff, tick]);

  const count = shown?.st.pages ?? 0;
  const cur = Math.min(Math.max(1, page), Math.max(1, count));
  const go = useCallback((n: number) => setPage(Math.min(Math.max(1, n), Math.max(1, count))), [count]);
  const pageUrl = useCallback(
    (n: number) => (shown ? api.scorePageUrl(pieceId, n, { w: shown.w, h: shown.h, staff: shown.staff, rev: shown.st.rev }) : ""),
    [api, pieceId, shown],
  );
  const relaying = !!shown && !!size && (shown.w !== size.w || shown.h !== size.h || shown.staff !== staff);

  // Pages: the keys turn pages (not while typing, or in a sheet or the menu).
  useEffect(() => {
    if (mode !== "pages" || !count) return;
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
  }, [mode, count, cur, go]);

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

  const choose = (m: Mode) => {
    if (m === mode) return;
    if (m === "pages" && count) {
      const mid = window.innerHeight / 2;
      let inView = 1;
      imgs.current.slice(0, count).forEach((el, i) => {
        if (el && el.getBoundingClientRect().top <= mid) inView = i + 1;
      });
      setPage(inView);
    } else scrollToPage.current = cur;
    setMode(m);
    storageSet(MODE_KEY, m);
  };

  const si = STAFFS.indexOf(staff);
  const resize = (d: number) => {
    const s = STAFFS[Math.min(STAFFS.length - 1, Math.max(0, si + d))];
    setStaff(s);
    storageSet(STAFF_KEY, String(s));
  };

  const title = status?.title || shown?.st.title || (problem ? "Score" : null);
  const composer = status?.composer ?? shown?.st.composer ?? null;
  const retry = () => {
    setProblem(null);
    setTick((n) => n + 1);
  };

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
              className={PAGE_CLASS}
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
        <p className="text-sm text-muted-foreground">
          The first time a piece's score is opened it's written from its MIDI, which takes about a quarter of its length. After
          that it opens at once.
        </p>
      </div>
    );
  } else if (status?.state === "ready") {
    body = <p className="py-2 text-base text-muted-foreground">There's nothing in this piece to write out.</p>;
  }

  return (
    <section aria-label="Score" data-testid="music-score" className="space-y-3">
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={onBack}
          aria-label="Back to Music"
          className="-ml-2 grid h-11 w-11 shrink-0 place-items-center rounded-full text-stone-700 hover:bg-stone-200/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <ArrowLeft className="h-5 w-5" aria-hidden />
        </button>
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
          <p className="truncate text-[15px] leading-5 text-muted-foreground">{composer || "\u00a0"}</p>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
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
        {relaying && !problem && <Spinner className="ml-auto h-5 w-5 text-muted-foreground" />}
      </div>

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
    </section>
  );
}
