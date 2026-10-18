// The pad that turns and tilts a moving light (docs/LIGHTS.md §8.3): drag anywhere on it to move relatively (across
// turns, up and down tilts; Fine or Coarse), or nudge with the arrows. The dot shows where it points within its range
// (and its limits, shaded). "Swap left/right" and "Swap up/down" are kept with the light (setup jog_flip), so the drag
// matches the beam however the light hangs.

import { useEffect, useRef } from "react";
import { ArrowDown, ArrowLeft, ArrowRight, ArrowUp } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { turnWords, type Axis, type Caps, type LightsUi, type PanTilt, type Setup } from "./lib";

const TOGGLE =
  "inline-flex min-h-10 items-center rounded-full border px-3 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

/** pan/tilt kept inside the light's range and, unless `free`, its limits. */
export function clampPos(p: PanTilt, caps: Caps | null, setup: Setup, free: boolean): PanTilt {
  const out = { ...p };
  for (const ax of ["pan", "tilt"] as Axis[]) {
    const r = caps?.[ax];
    let lo = r ? r.min : -Infinity;
    let hi = r ? r.max : Infinity;
    const lim = setup.limits[ax];
    if (!free && lim) {
      lo = Math.max(lo, lim[0]);
      hi = Math.min(hi, lim[1]);
    }
    out[ax] = Math.round(Math.min(hi, Math.max(lo, out[ax])) * 100) / 100;
  }
  return out;
}

export function JogPad({
  pos,
  caps,
  setup,
  ui,
  fine,
  free,
  onFine,
  onMove,
  onFlip,
}: {
  pos: PanTilt;
  caps: Caps | null;
  setup: Setup;
  ui: LightsUi;
  fine: boolean;
  /** The limits are being set: they don't hold it back. */
  free: boolean;
  onFine: (fine: boolean) => void;
  onMove: (p: PanTilt) => void;
  onFlip: (axis: Axis, flipped: boolean) => void;
}) {
  const latest = useRef(pos);
  useEffect(() => {
    latest.current = pos;
  }, [pos]);
  const drag = useRef<{ x: number; y: number; id: number } | null>(null);
  const flip = setup.jog_flip;
  const sp = flip.pan ? -1 : 1;
  const st = flip.tilt ? -1 : 1;
  const k = fine ? ui.drag_deg_per_px.fine : ui.drag_deg_per_px.coarse;
  const step = fine ? ui.nudge_deg.fine : ui.nudge_deg.coarse;

  const move = (dPan: number, dTilt: number) => {
    const next = clampPos({ pan: latest.current.pan + dPan, tilt: latest.current.tilt + dTilt }, caps, setup, free);
    latest.current = next;
    onMove(next);
  };

  // Where the dot sits: its share of the range (flipped like the drag, so it follows the finger).
  const share = (ax: Axis, v: number) => {
    const r = caps?.[ax];
    return r ? (v - r.min) / (r.max - r.min) : 0.5;
  };
  const dotX = (ax: number) => (flip.pan ? 1 - ax : ax);
  const dotY = (ay: number) => (flip.tilt ? ay : 1 - ay);
  const x = dotX(share("pan", pos.pan));
  const y = dotY(share("tilt", pos.tilt));
  const lp = setup.limits.pan;
  const lt = setup.limits.tilt;
  const limBox =
    caps?.pan && caps?.tilt && (lp || lt)
      ? (() => {
          const xs = (lp ?? [caps.pan.min, caps.pan.max]).map((v) => dotX(share("pan", v)));
          const ys = (lt ?? [caps.tilt.min, caps.tilt.max]).map((v) => dotY(share("tilt", v)));
          return { l: Math.min(...xs), r: Math.max(...xs), t: Math.min(...ys), b: Math.max(...ys) };
        })()
      : null;

  return (
    <div className="space-y-3" data-testid="jog-pad">
      <div
        role="application"
        aria-label="Drag to turn and tilt the light"
        className="relative mx-auto aspect-square w-full max-w-[18rem] touch-none select-none overflow-hidden rounded-2xl border border-stone-200 bg-stone-50"
        onPointerDown={(e) => {
          (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
          drag.current = { x: e.clientX, y: e.clientY, id: e.pointerId };
        }}
        onPointerMove={(e) => {
          const d = drag.current;
          if (!d || d.id !== e.pointerId) return;
          const dx = e.clientX - d.x;
          const dy = e.clientY - d.y;
          if (dx === 0 && dy === 0) return;
          drag.current = { ...d, x: e.clientX, y: e.clientY };
          move(dx * k * sp, -dy * k * st);
        }}
        onPointerUp={() => (drag.current = null)}
        onPointerCancel={() => (drag.current = null)}
      >
        {limBox && (
          <div
            aria-hidden
            className="absolute rounded-md bg-teal-100/70"
            style={{ left: `${limBox.l * 100}%`, width: `${(limBox.r - limBox.l) * 100}%`, top: `${limBox.t * 100}%`, height: `${(limBox.b - limBox.t) * 100}%` }}
          />
        )}
        <div aria-hidden className="absolute inset-x-0 top-1/2 h-px bg-stone-200" />
        <div aria-hidden className="absolute inset-y-0 left-1/2 w-px bg-stone-200" />
        <div
          aria-hidden
          className="absolute h-6 w-6 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-white bg-teal-600 shadow"
          style={{ left: `${Math.min(1, Math.max(0, x)) * 100}%`, top: `${Math.min(1, Math.max(0, y)) * 100}%` }}
        />
        <span className="pointer-events-none absolute bottom-2 left-0 right-0 text-center text-sm text-muted-foreground">Drag to move it</span>
      </div>

      <p className="text-center text-base font-medium tabular-nums" data-testid="jog-readout" aria-live="polite">
        {turnWords(pos.pan, pos.tilt)}
      </p>

      <div className="mx-auto grid w-48 grid-cols-3 gap-2">
        <span />
        <Button variant="outline" className="h-11 rounded-xl bg-white" aria-label="Tilt up" onClick={() => move(0, step * st)}>
          <ArrowUp aria-hidden />
        </Button>
        <span />
        <Button variant="outline" className="h-11 rounded-xl bg-white" aria-label="Turn left" onClick={() => move(-step * sp, 0)}>
          <ArrowLeft aria-hidden />
        </Button>
        <Button variant="outline" className="h-11 rounded-xl bg-white" aria-label="Tilt down" onClick={() => move(0, -step * st)}>
          <ArrowDown aria-hidden />
        </Button>
        <Button variant="outline" className="h-11 rounded-xl bg-white" aria-label="Turn right" onClick={() => move(step * sp, 0)}>
          <ArrowRight aria-hidden />
        </Button>
      </div>

      <div className="flex flex-wrap items-center justify-center gap-2">
        <div role="radiogroup" aria-label="How far it moves" className="inline-flex rounded-full border border-stone-200 bg-white p-0.5">
          {[true, false].map((f) => (
            <button
              key={String(f)}
              type="button"
              role="radio"
              aria-checked={fine === f}
              onClick={() => onFine(f)}
              className={cn("min-h-9 rounded-full px-3 text-sm font-medium", fine === f ? "bg-teal-600 text-white" : "text-stone-700")}
            >
              {f ? "Fine" : "Coarse"}
            </button>
          ))}
        </div>
        <button type="button" aria-pressed={flip.pan} onClick={() => onFlip("pan", !flip.pan)} className={cn(TOGGLE, flip.pan ? "border-teal-300 bg-teal-50 text-teal-900" : "border-stone-200 bg-white text-stone-700")}>
          Swap left/right
        </button>
        <button type="button" aria-pressed={flip.tilt} onClick={() => onFlip("tilt", !flip.tilt)} className={cn(TOGGLE, flip.tilt ? "border-teal-300 bg-teal-50 text-teal-900" : "border-stone-200 bg-white text-stone-700")}>
          Swap up/down
        </button>
      </div>
    </div>
  );
}
