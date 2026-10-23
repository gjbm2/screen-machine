// Playback speed: one small dial, "− 100% +" (owner, 23 Oct: "FAR too much real estate given to speed changing.
// should be a 100% dial with + and - on either side of it"), shared by the Room page's test piano and takes and the
// Music player's Now playing. A tap nudges 1% (log scaling, owner: "implicit log scaling is obviously what's
// useful": faster multiplies by 1.01, slower divides, so up then down is back where it was); holding a button keeps
// going, and after a second in 5% steps, for a bigger change. Tapping the number goes back to 100%. Shown as % of
// the piece's own tempo, 25-400%. Sent once the taps stop (SETTLE_MS): a run of taps is one restart that carries on
// from where the piece is.

import { useEffect, useRef, useState } from "react";

export const NUDGES = [1, 5];                 // a tap's step, then a held button's after HOLD_FAST_MS (in %)
export const SPEED_MIN = 0.25;
export const SPEED_MAX = 4;
const SETTLE_MS = 600;
const HOLD_DELAY_MS = 380;                     // a press held this long starts repeating ...
const HOLD_EVERY_MS = 90;                      // ... this often ...
const HOLD_FAST_MS = 1100;                     // ... in bigger steps after this long

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
  const shownRef = useRef(speed);
  const settle = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const hold = useRef<{ t?: ReturnType<typeof setTimeout>; since: number } | null>(null);
  const shown = draft ?? speed;
  shownRef.current = shown;

  const stopHold = () => {
    if (hold.current?.t) clearTimeout(hold.current.t);
    hold.current = null;
  };
  useEffect(
    () => () => {
      clearTimeout(settle.current);
      stopHold();
    },
    [],
  );

  const set = (s: number, now = false) => {
    const v = snapSpeed(s);
    shownRef.current = v;
    setDraft(v);
    clearTimeout(settle.current);
    const send = () => {
      setDraft(null);
      if (Math.abs(v - speed) > 1e-6) onSpeed(v);
    };
    if (now) send();
    else settle.current = setTimeout(send, SETTLE_MS);
  };
  const step = (sign: 1 | -1, pct: number) => {
    const f = 1 + pct / 100;
    set(sign > 0 ? shownRef.current * f : shownRef.current / f);
  };
  const press = (sign: 1 | -1) => {
    stopHold();
    step(sign, NUDGES[0]);
    const h = { since: Date.now() } as { t?: ReturnType<typeof setTimeout>; since: number };
    hold.current = h;
    const again = () => {
      if (hold.current !== h) return;
      step(sign, Date.now() - h.since > HOLD_FAST_MS ? NUDGES[1] : NUDGES[0]);
      h.t = setTimeout(again, HOLD_EVERY_MS);
    };
    h.t = setTimeout(again, HOLD_DELAY_MS);
  };

  const atMin = shown <= SPEED_MIN + 1e-6;
  const atMax = shown >= SPEED_MAX - 1e-6;
  const btn =
    "inline-flex h-9 w-9 select-none items-center justify-center rounded-full border border-stone-200 bg-white text-lg font-semibold text-stone-700 hover:bg-stone-50 disabled:opacity-40 touch-manipulation";
  const button = (sign: 1 | -1) => (
    <button
      type="button"
      className={btn}
      aria-label={sign > 0 ? "A little faster" : "A little slower"}
      title={sign > 0 ? "Faster (hold for more)" : "Slower (hold for more)"}
      disabled={disabled || (sign > 0 ? atMax : atMin)}
      onPointerDown={(e) => {
        e.preventDefault();
        press(sign);
      }}
      onPointerUp={stopHold}
      onPointerLeave={stopHold}
      onPointerCancel={stopHold}
      onContextMenu={(e) => e.preventDefault()}
      onClick={(e) => e.detail === 0 && step(sign, NUDGES[0])} // the keyboard's Enter/Space (a pointer press is above)
    >
      {sign > 0 ? "+" : "−"}
    </button>
  );
  const normal = Math.abs(shown - 1) < 1e-6;
  return (
    <div className="inline-flex items-center gap-1.5" role="group" aria-label="Speed">
      <span className="text-[15px] font-medium text-stone-700">Speed</span>
      {button(-1)}
      <button
        type="button"
        disabled={disabled || normal}
        onClick={() => set(1, true)}
        title={normal ? "The piece's own speed" : "Back to 100%"}
        aria-live="polite"
        className="min-w-16 rounded-full px-1.5 text-center text-base font-semibold tabular-nums text-stone-800 disabled:cursor-default enabled:hover:bg-stone-100"
      >
        {speedWords(shown)}
      </button>
      {button(1)}
    </div>
  );
}
