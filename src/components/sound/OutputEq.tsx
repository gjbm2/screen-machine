// An output's graphic EQ (owner, 27 Nov: "a graphic equaliser with some basic settings/controls for audio output"):
// presets as one row of chips, then a slider per band. Stored on the output (smsound/eq.py), so every part of the app
// that plays there sounds the same; the karaoke player (a screen's page or a browser) applies it within a second.

import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import { sound, type OutputEq as Eq } from "@/shared/api/sound";

const hzText = (hz: number) => (hz >= 1000 ? `${+(hz / 1000).toFixed(1)}k` : String(hz));
const SEND_MS = 120;

export function OutputEq({ output, folded = false }: { output: string; /** Shut behind "EQ · <preset>" */ folded?: boolean }) {
  const [open, setOpen] = useState(!folded);
  const [eq, setEq] = useState<Eq | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const timer = useRef<number | null>(null);
  useEffect(() => {
    let dead = false;
    setEq(null);
    sound
      .eq(output)
      .then((r) => !dead && setEq(r.eq))
      .catch((e) => !dead && setErr(String((e as Error).message || e)));
    return () => {
      dead = true;
    };
  }, [output]);
  if (err) return <p className="text-sm text-red-700">{err}</p>;
  if (!eq || !eq.applies) return null;

  const put = (body: { preset: string } | { gains: number[] }) =>
    sound
      .setEq(output, body)
      .then((r) => setEq(r.eq))
      .catch((e) => setErr(String((e as Error).message || e)));
  const slide = (i: number, v: number) => {
    const gains = eq.gains.map((g, j) => (j === i ? v : g));
    const match = eq.presets.find((p) => p.gains.every((g, j) => g === gains[j]));
    setEq({ ...eq, gains, preset: match?.id ?? null });
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => void put({ gains }), SEND_MS);
  };

  const name = eq.presets.find((p) => p.id === eq.preset)?.label ?? "Custom";
  if (!open)
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="text-sm text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
      >
        EQ · {name} ▸
      </button>
    );
  return (
    <div className="space-y-2" data-testid="output-eq">
      {folded && (
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="text-sm text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
        >
          EQ · {name} ▾
        </button>
      )}
      <div className="no-scrollbar -mx-1 flex gap-1.5 overflow-x-auto px-1">
        {eq.presets.map((p) => (
          <button
            key={p.id}
            type="button"
            onClick={() => void put({ preset: p.id })}
            className={cn(
              "shrink-0 whitespace-nowrap rounded-full border px-3 py-1 text-xs font-medium",
              eq.preset === p.id
                ? "border-primary bg-primary text-primary-foreground"
                : "border-stone-200 text-muted-foreground hover:text-foreground",
            )}
          >
            {p.label}
          </button>
        ))}
      </div>
      <div className="flex justify-between gap-1 rounded-xl bg-stone-50 px-2 py-2 ring-1 ring-stone-200/70">
        {eq.bands.map((hz, i) => (
          <label key={hz} className="flex min-w-0 flex-1 flex-col items-center gap-1">
            <span className="text-[11px] tabular-nums text-muted-foreground">
              {eq.gains[i] > 0 ? "+" : ""}
              {eq.gains[i]}
            </span>
            <input
              type="range"
              min={-eq.range_db}
              max={eq.range_db}
              step={1}
              value={eq.gains[i]}
              onChange={(e) => slide(i, Number(e.target.value))}
              onDoubleClick={() => slide(i, 0)}
              aria-label={`${hzText(hz)} Hz`}
              className="h-24 w-5 cursor-pointer accent-primary [direction:rtl] [writing-mode:vertical-lr]"
            />
            <span className="text-[11px] tabular-nums text-muted-foreground">{hzText(hz)}</span>
          </label>
        ))}
      </div>
    </div>
  );
}
