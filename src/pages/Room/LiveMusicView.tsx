// The live music screen for "This device" (owner, 23 Oct): while PC22's piano plays on this phone or tablet, a
// full-screen view of the notes as they're heard (a lit keyboard with each note rising from its key), what's playing,
// "Tap to start sound" when the browser holds the sound back, Stop sound here, and Close (the sound keeps playing; the
// page's bar opens it again).
//
// The notes come from the show bus (noteFeed.ts, fed by ListenHere from WS /show/v1/show: music.note, music.pedal and
// the music.notes snapshots), the same notes PianoRow shows: test pieces, recordings and the real Steinway. They're drawn
// `delayMs` after they arrive, the stream's delay behind the room (ListenHere: show_service's share plus the browser's
// buffer), so the keys light with the sound rather than ahead of it. The bus's t_ms is PC22's clock: not used.
// Drawn on one canvas, only while the screen is open and the page visible; nothing is kept once it has scrolled off.

import { useEffect, useRef, useState, type ReactNode } from "react";
import { Square, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { listenOff, setListenView, useListenHere } from "./listenStream";
import { isBlack, RISE_S, type NoteFeed } from "./noteFeed";
import { TAP_BUTTON_CLASS, THEME_CLASS } from "./theme";

// The Room page's warm skin, as canvas colours.
const C = {
  roll: "#faf8f4",
  octave: "rgba(120, 100, 80, 0.09)",
  note: "15, 118, 110", // teal-700
  noteBlack: "17, 94, 89", // teal-800
  white: "#ffffff",
  whiteEdge: "#e7e0d6",
  whiteLit: "#5eead4", // teal-300
  black: "#292524", // stone-800
  blackLit: "#0d9488", // teal-600
  top: "#d6cdc0",
};

/** A rounded rectangle path (a plain one where the browser has no roundRect: Safari before 16). */
function rr(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number | number[]) {
  ctx.beginPath();
  if (typeof ctx.roundRect === "function") ctx.roundRect(x, y, w, h, r);
  else ctx.rect(x, y, w, h);
}

function draw(ctx: CanvasRenderingContext2D, w: number, h: number, feed: NoteFeed, now: number) {
  const kb = Math.min(130, Math.max(64, h * 0.17));
  const rollH = h - kb;
  const speed = rollH / RISE_S;
  const [lo, hi] = feed.range;
  const whites: number[] = [];
  for (let p = lo; p <= hi; p++) if (!isBlack(p)) whites.push(p);
  const ww = w / whites.length;
  const bw = ww * 0.62;
  const whiteIx = new Map(whites.map((p, i) => [p, i]));
  const keyX = (p: number) => (isBlack(p) ? (whiteIx.get(p - 1) ?? 0) * ww + ww - bw / 2 : (whiteIx.get(p) ?? 0) * ww);
  const keyW = (p: number) => (isBlack(p) ? bw : ww);

  ctx.fillStyle = C.roll;
  ctx.fillRect(0, 0, w, rollH);
  ctx.strokeStyle = C.octave;
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (const p of whites)
    if (p % 12 === 0) {
      const x = Math.round(keyX(p)) + 0.5;
      ctx.moveTo(x, 0);
      ctx.lineTo(x, rollH);
    }
  ctx.stroke();

  // the notes, rising from their keys
  for (const b of feed.bars) {
    if (b.pitch < lo || b.pitch > hi) continue;
    const top = rollH - ((now - b.start) / 1000) * speed;
    const bottom = b.end === null ? rollH : rollH - ((now - b.end) / 1000) * speed;
    if (bottom < 0) continue;
    const black = isBlack(b.pitch);
    const inset = black ? 1 : Math.min(3, ww * 0.15);
    const x = keyX(b.pitch) + inset;
    const bwid = Math.max(2, keyW(b.pitch) - inset * 2);
    const y = Math.max(-4, top);
    const hgt = Math.max(3, bottom - y);
    const a = (b.end === null ? 0.55 : 0.4) + 0.45 * Math.min(1, b.vel / 110);
    ctx.fillStyle = `rgba(${black ? C.noteBlack : C.note}, ${a.toFixed(2)})`;
    rr(ctx, x, y, bwid, hgt, Math.min(4, bwid / 2));
    ctx.fill();
  }

  // the keyboard: whites, then blacks over them; held keys lit
  const y0 = rollH;
  for (const p of whites) {
    const x = keyX(p);
    ctx.fillStyle = feed.held.has(p) ? C.whiteLit : C.white;
    ctx.fillRect(x, y0, ww, kb);
    ctx.fillStyle = C.whiteEdge;
    ctx.fillRect(Math.round(x + ww) - 1, y0, 1, kb);
  }
  for (let p = lo; p <= hi; p++) {
    if (!isBlack(p)) continue;
    ctx.fillStyle = feed.held.has(p) ? C.blackLit : C.black;
    rr(ctx, keyX(p), y0, bw, kb * 0.62, [0, 0, 3, 3]);
    ctx.fill();
  }
  ctx.fillStyle = C.top;
  ctx.fillRect(0, y0, w, 2);
}

type RollState = { sustain: boolean; idle: boolean };
/** No note for this long (and none held): the screen says the notes will show. */
const IDLE_MS = 8_000;

/** The notes, drawn every frame while mounted (the browser stops frames while the page is hidden). */
function Roll({ feed, onState }: { feed: NoteFeed; onState: (s: RollState) => void }) {
  const box = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const report = useRef<(s: RollState) => void>(onState);
  report.current = onState;

  useEffect(() => {
    const el = box.current;
    const cv = canvas.current;
    const ctx = cv?.getContext("2d");
    if (!el || !cv || !ctx) return;
    let w = 0;
    let h = 0;
    const size = () => {
      const r = el.getBoundingClientRect();
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      w = Math.max(1, r.width);
      h = Math.max(1, r.height);
      cv.width = Math.round(w * dpr);
      cv.height = Math.round(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    size();
    const ro = new ResizeObserver(size);
    ro.observe(el);
    let raf = 0;
    let last: RollState | null = null;
    const frame = () => {
      const now = performance.now();
      feed.advance(now);
      draw(ctx, w, h, feed, now);
      const st = { sustain: feed.damper > 0.5, idle: feed.held.size === 0 && now - feed.lastOn > IDLE_MS };
      if (!last || st.sustain !== last.sustain || st.idle !== last.idle) {
        last = st;
        report.current(st);
      }
      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, [feed]);

  return (
    <div ref={box} className="absolute inset-0">
      <canvas ref={canvas} className="block h-full w-full" aria-hidden />
    </div>
  );
}

export function LiveMusicView({
  feed,
  title,
  sub,
  soundLabel,
  line,
}: {
  feed: NoteFeed;
  title: string;
  sub?: string | null;
  soundLabel?: string | null;
  /** The sound's line (ListenHere's ListenLine): Tap to start sound, Getting the sound ready…, how far behind. */
  line: ReactNode;
}) {
  const here = useListenHere();
  const [roll, setRoll] = useState<RollState>({ sustain: false, idle: false });
  const close = () => setListenView(false);

  // the page underneath doesn't scroll while this is open; Escape closes it
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setListenView(false);
    };
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prev;
      window.removeEventListener("keydown", onKey);
    };
  }, []);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Music on this device"
      className={cn(THEME_CLASS, "fixed inset-0 z-50 flex flex-col bg-background text-foreground")}
      data-testid="live-music-view"
    >
      <div className="mx-auto flex w-full max-w-3xl items-start gap-3 px-4 pb-3 pt-[max(0.75rem,env(safe-area-inset-top))] sm:px-6">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-teal-800">Playing on this device</p>
          <p className="text-xl font-semibold leading-snug [overflow-wrap:anywhere]">{title}</p>
          {sub && <p className="truncate text-base text-muted-foreground">{sub}</p>}
          {soundLabel && <p className="truncate text-sm text-muted-foreground">Sound: {soundLabel}</p>}
        </div>
        <button
          type="button"
          aria-label="Close"
          title="Back to the page (the sound keeps playing)"
          onClick={close}
          className="grid h-11 w-11 shrink-0 place-items-center rounded-full border border-stone-300 bg-white text-stone-700 hover:bg-stone-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <X className="h-5 w-5" aria-hidden />
        </button>
      </div>

      <div className="relative min-h-0 flex-1 border-y border-stone-200/80">
        <Roll feed={feed} onState={setRoll} />
        {roll.sustain && (
          <span className="absolute right-3 top-3 rounded-full border border-amber-200 bg-amber-50 px-2.5 py-0.5 text-[13px] font-medium text-amber-900">
            Sustain pedal
          </span>
        )}
        {roll.idle && here.status === "playing" && (
          <p className="pointer-events-none absolute inset-x-0 top-1/3 px-6 text-center text-base text-muted-foreground">
            The notes show here as the music plays.
          </p>
        )}
      </div>

      <div className="mx-auto w-full max-w-3xl space-y-3 px-4 pb-[max(1rem,env(safe-area-inset-bottom))] pt-3 sm:px-6">
        {line}
        <button
          type="button"
          onClick={listenOff}
          className={cn(TAP_BUTTON_CLASS, "inline-flex h-12 w-full items-center justify-center gap-2 border border-stone-300 bg-white font-semibold hover:bg-stone-50")}
        >
          <Square className="h-4 w-4" aria-hidden /> Stop sound here
        </button>
      </div>
    </div>
  );
}
