// Playback speed by nudges, shared by the Room page's test piano and takes (and fit for the Music player's): the
// owner, 23 Oct: "FINE GRAINED. Nudges should be SLIGHTLY faster and SLIGHTLY slower e.g. +1%, +3% +5% +10% +50% and
// vice versa"; "implicit log scaling is obviously what's useful". Each nudge multiplies the speed (faster n% =
// x(1 + n/100), slower n% = its inverse, so +3% then -3% is back where it was), shown as % of the piece's own tempo,
// 25-400%; Normal resets. Sent once the taps stop (SETTLE_MS), so a run of taps is one restart that carries on from
// where the piece is. The same nudges as the Music page's Now playing (8f's SpeedControl, a0652b7b).

import { useEffect, useRef, useState } from "react";
import { Gauge } from "lucide-react";

export const NUDGES = [1, 3, 5, 10, 50];
export const SPEED_MIN = 0.25;
export const SPEED_MAX = 4;
const SETTLE_MS = 600;

export const speedWords = (s: number) => {
  const pct = Math.round(s * 1000) / 10;
  return `${Number.isInteger(pct) ? pct : pct.toFixed(1)}%`;
};

export const snapSpeed = (s: number) => {
  const v = Math.min(SPEED_MAX, Math.max(SPEED_MIN, Math.round(s * 1000) / 1000));
  return Math.abs(v - 1) < 0.004 ? 1 : v; // back near the piece's own tempo: exactly 100%
};

export function SpeedNudges({ speed, onSpeed, disabled }: { speed: number; onSpeed: (s: number) => void; disabled?: boolean }) {
  const [draft, setDraft] = useState<number | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);
  const shown = draft ?? speed;
  const change = (s: number, now = false) => {
    const v = snapSpeed(s);
    setDraft(v);
    clearTimeout(timer.current);
    const send = () => {
      setDraft(null);
      if (Math.abs(v - speed) > 1e-6) onSpeed(v);
    };
    if (now) send();
    else timer.current = setTimeout(send, SETTLE_MS);
  };
  const nudge =
    "min-h-10 min-w-12 rounded-full border border-stone-200 bg-white px-2.5 text-[15px] font-medium tabular-nums text-stone-700 hover:bg-stone-50 disabled:opacity-40";
  const row = (sign: 1 | -1) => (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="w-14 text-sm text-muted-foreground">{sign > 0 ? "Faster" : "Slower"}</span>
      {NUDGES.map((n) => (
        <button
          key={n}
          type="button"
          className={nudge}
          aria-label={`${sign > 0 ? "Faster" : "Slower"} by ${n}%`}
          disabled={disabled || (sign > 0 ? shown >= SPEED_MAX - 1e-6 : shown <= SPEED_MIN + 1e-6)}
          onClick={() => change(sign > 0 ? shown * (1 + n / 100) : shown / (1 + n / 100))}
        >
          {sign > 0 ? "+" : "−"}
          {n}%
        </button>
      ))}
    </div>
  );
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <span className="inline-flex items-center gap-1.5 text-base font-medium">
          <Gauge className="h-4 w-4" aria-hidden /> Speed
        </span>
        <span className="min-w-14 text-base font-semibold tabular-nums" aria-live="polite">
          {speedWords(shown)}
        </span>
        {Math.abs(shown - 1) > 1e-6 && (
          <button
            type="button"
            disabled={disabled}
            onClick={() => change(1, true)}
            className="min-h-9 rounded-full border border-stone-200 bg-white px-3 text-[15px] font-medium text-stone-700 hover:bg-stone-50 disabled:opacity-40"
          >
            Normal
          </button>
        )}
      </div>
      {row(-1)}
      {row(1)}
    </div>
  );
}
