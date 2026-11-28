// The pieces every was / now / next view shares (the screens' LiveNow.tsx, the lights' LightsNow.tsx): a card per
// moment, and holding still while it is being used (owner, 27 Nov: "you advance the scene before you get to the score
// ... and end up rating the wrong thing"; 28 Nov: one component, used wherever it is needed).

import { useEffect, useRef, useState, type ReactNode } from "react";
import { RotateCw } from "lucide-react";
import { cn } from "@/lib/utils";

export type When = "was" | "now" | "next";
export const WHEN_WORDS: Record<When, string> = { was: "Was", now: "Now", next: "Next" };
/** After the last touch inside, what shows stays put this long (a finger has no hover). */
export const HOLD_MS = 6000;

/** One moment's card: Now stands out, Was and Next are quiet. ``extra``: beside the word (Next's "in 12 s"). */
export function MomentCard({ when, extra, children, testId }: { when: When; extra?: ReactNode; children: ReactNode; testId?: string }) {
  const isNow = when === "now";
  return (
    <section
      data-testid={testId}
      className={cn(
        "space-y-1.5 rounded-xl border p-2",
        isNow ? "border-teal-500 bg-white shadow-sm ring-1 ring-teal-500/30" : "border-stone-200 bg-stone-50/70",
      )}
    >
      <div className="flex min-h-6 items-center gap-2 px-0.5">
        <span className={cn("text-xs font-semibold uppercase tracking-wider", isNow ? "text-teal-700" : "text-stone-500")}>{WHEN_WORDS[when]}</span>
        {extra ? <span className="text-xs tabular-nums text-stone-500">{extra}</span> : null}
      </div>
      {children}
    </section>
  );
}

/** Nothing moves under the pointer: while the pointer is over the view, and for HOLD_MS after a touch, ``view`` is what
 *  it was when you came to it; ``stale`` when the live one has moved on since (``sig`` tells them apart), and
 *  ``release`` brings it up to date. Spread ``handlers`` on the view's box. */
export function useHold<T>(live: T, sig: (v: T) => string) {
  const [frozen, setFrozen] = useState<T | null>(null);
  const inside = useRef(false);
  const touched = useRef(0);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => document.visibilityState === "visible" && setTick((x) => x + 1), 1000);
    return () => clearInterval(t);
  }, []);
  useEffect(() => {
    if (frozen && !inside.current && Date.now() - touched.current > HOLD_MS) setFrozen(null);
  }, [tick, frozen]);
  const hold = () => setFrozen((f) => f ?? live);
  const release = () => {
    touched.current = 0;
    setFrozen(null);
  };
  return {
    view: frozen ?? live,
    held: frozen !== null,
    stale: frozen !== null && sig(frozen) !== sig(live),
    release,
    handlers: {
      onPointerEnter: () => {
        inside.current = true;
        hold();
      },
      onPointerLeave: () => {
        inside.current = false;
      },
      onPointerDown: () => {
        touched.current = Date.now();
        hold();
      },
    },
  };
}

/** ↻: the view has moved on while held; brings it up to date. */
export function CatchUp({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      title="Up to date"
      aria-label="Up to date"
      className="absolute right-2 top-1.5 z-10 grid h-7 w-7 place-items-center rounded-full border border-teal-300 bg-teal-50 text-teal-800 shadow-sm"
    >
      <RotateCw className="h-3.5 w-3.5" aria-hidden />
    </button>
  );
}
