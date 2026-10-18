// Move it's controls (docs/LIGHTS.md §8.3): one per thing the light can do (lib.controlsFor over its caps), never per
// type, under plain headings: Brightness, Colour, Pattern, Split the beam, Beam, Shutter, More. Every change goes
// straight to the light (the caller sends it through the jog). Pattern and colour slots can be renamed in place.

import { useMemo, useState } from "react";
import { Check, Pencil } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import { Unroll } from "@/pages/Room/bits";
import { controlsFor, hexToRgb, rgbToHex, type Caps, type Control, type SlotChoice } from "./lib";

const CHIP =
  "inline-flex min-h-11 items-center gap-2 rounded-full border px-3.5 text-[15px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
const CHIP_ON = "border-teal-300 bg-teal-50 text-teal-900";
const CHIP_OFF = "border-stone-200 bg-white text-stone-700 hover:bg-stone-50";

/** Colours to try a mixing light with (any other from the picker). */
const SWATCHES = ["#ffffff", "#ffd2a1", "#ff3b30", "#ff9500", "#ffcc00", "#34c759", "#00c7be", "#007aff", "#af52de", "#ff2d55"];

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

function colourHex(v: unknown): string | null {
  if (Array.isArray(v) && v.length >= 3 && v.every((n) => typeof n === "number")) {
    return rgbToHex([v[0] * 255, v[1] * 255, v[2] * 255]);
  }
  return null;
}

function hexColour(hex: string): number[] | null {
  const rgb = hexToRgb(hex);
  return rgb ? rgb.map((n) => Math.round((n / 255) * 1000) / 1000) : null;
}

export function CapControls({
  caps,
  values,
  onChange,
  onRename,
}: {
  caps: Caps | null;
  values: Record<string, unknown>;
  onChange: (attr: string, value: unknown) => void;
  /** Rename a pattern or colour slot (kept with the light). */
  onRename?: (wheel: "colour" | "gobo" | "gobo2", slot: number, name: string) => void;
}) {
  const controls = useMemo(() => controlsFor(caps), [caps]);
  const groups = useMemo(() => {
    const out: [string, Control[]][] = [];
    for (const c of controls) {
      const g = out.find(([name]) => name === c.group);
      if (g) g[1].push(c);
      else out.push([c.group, [c]]);
    }
    return out;
  }, [controls]);
  if (!caps) {
    return <p className="text-[15px] text-muted-foreground">What this light can do isn't known yet (show_service needs updating).</p>;
  }
  if (!controls.length) return <p className="text-[15px] text-muted-foreground">This light has nothing more to set.</p>;
  const main = groups.filter(([g]) => g !== "More");
  const more = groups.find(([g]) => g === "More");
  return (
    <div className="space-y-4" data-testid="cap-controls">
      {main.map(([group, list]) => (
        <section key={group} className="space-y-2">
          <h4 className="text-[15px] font-semibold">{group}</h4>
          {list.map((c) => (
            <ControlRow key={`${c.attr}:${c.kind}`} c={c} value={values[c.attr]} values={values} onChange={onChange} onRename={onRename} />
          ))}
        </section>
      ))}
      {more && (
        <Unroll title="More" hint={`${more[1].length}`}>
          <div className="space-y-3">
            {more[1].map((c) => (
              <ControlRow key={`${c.kind}:${c.kind === "macro" ? c.channel : c.attr}`} c={c} value={values[c.attr]} values={values} onChange={onChange} />
            ))}
          </div>
        </Unroll>
      )}
    </div>
  );
}

function ControlRow({
  c,
  value,
  values,
  onChange,
  onRename,
}: {
  c: Control;
  value: unknown;
  values: Record<string, unknown>;
  onChange: (attr: string, value: unknown) => void;
  onRename?: (wheel: "colour" | "gobo" | "gobo2", slot: number, name: string) => void;
}) {
  switch (c.kind) {
    case "slider": {
      const v = typeof value === "number" ? value : c.attr === "intensity" ? 1 : (c.min + c.max) / 2;
      const shown = c.unit === "K" ? `${Math.round(v)} K` : c.unit === "°" ? `${Math.round(v)}°` : `${Math.round(((v - c.min) / (c.max - c.min || 1)) * 100)}%`;
      return (
        <div className="space-y-1">
          <div className="flex items-baseline justify-between text-[15px]">
            <span>{c.label}</span>
            <span className="tabular-nums text-muted-foreground">{shown}</span>
          </div>
          <Slider
            aria-label={c.label}
            min={c.min}
            max={c.max}
            step={c.step}
            value={[Math.min(c.max, Math.max(c.min, v))]}
            onValueChange={([x]) => onChange(c.attr, x)}
            className="py-3"
          />
          <div className="flex justify-between text-sm text-muted-foreground">
            <span>{c.ends[0]}</span>
            <span>{c.ends[1]}</span>
          </div>
        </div>
      );
    }
    case "spin": {
      const v = typeof value === "number" ? value : 0;
      return (
        <div className="space-y-1">
          <div className="flex items-baseline justify-between text-[15px]">
            <span>{c.label}</span>
            <button type="button" onClick={() => onChange(c.attr, 0)} className="min-h-9 rounded-full px-3 text-sm font-medium text-teal-800 hover:bg-stone-100">
              {v === 0 ? "Still" : "Stop"}
            </button>
          </div>
          <Slider aria-label={c.label} min={-1} max={1} step={0.02} value={[v]} onValueChange={([x]) => onChange(c.attr, Math.abs(x) < 0.03 ? 0 : x)} className="py-3" />
          <div className="flex justify-between text-sm text-muted-foreground">
            <span aria-label="Anticlockwise">⟲</span>
            <span>stop</span>
            <span aria-label="Clockwise">⟳</span>
          </div>
        </div>
      );
    }
    case "switch":
      return (
        <label className="flex min-h-11 cursor-pointer items-center justify-between gap-3 text-[15px]">
          <span>{c.label}</span>
          <Switch aria-label={c.label} checked={same(value, c.on)} onCheckedChange={(on) => onChange(c.attr, on ? c.on : c.off)} />
        </label>
      );
    case "colour": {
      const hex = colourHex(value);
      return (
        <div className="flex flex-wrap items-center gap-2">
          {SWATCHES.map((s) => (
            <button
              key={s}
              type="button"
              aria-label={s === "#ffffff" ? "White" : `Colour ${s}`}
              aria-pressed={hex === s}
              onClick={() => onChange("colour", hexColour(s))}
              className={cn("h-11 w-11 rounded-full border-2 transition-transform", hex === s ? "scale-110 border-teal-700" : "border-stone-200")}
              style={{ background: s }}
            />
          ))}
          <label className={cn(CHIP, CHIP_OFF, "cursor-pointer")}>
            Pick…
            <input type="color" className="h-6 w-8 cursor-pointer border-0 bg-transparent p-0" value={hex ?? "#ffffff"} onChange={(e) => onChange("colour", hexColour(e.target.value))} />
          </label>
        </div>
      );
    }
    case "slots":
      return <SlotChips c={c} value={value} onChange={onChange} onRename={onRename} />;
    case "macro": {
      const cur = (value && typeof value === "object" ? (value as Record<string, string>) : {})[c.channel] ?? "";
      return (
        <label className="flex flex-col gap-1 text-[15px]">
          <span>{c.label}</span>
          <select
            className="h-11 rounded-xl border border-stone-200 bg-white px-3 text-base"
            value={cur}
            onChange={(e) => {
              const prev = values.macro && typeof values.macro === "object" ? (values.macro as Record<string, string>) : {};
              onChange("macro", { ...prev, [c.channel]: e.target.value });
            }}
          >
            <option value="">As it is</option>
            {c.choices.map((w) => (
              <option key={w} value={w}>
                {w}
              </option>
            ))}
          </select>
        </label>
      );
    }
  }
}

function SlotChips({
  c,
  value,
  onChange,
  onRename,
}: {
  c: Extract<Control, { kind: "slots" }>;
  value: unknown;
  onChange: (attr: string, value: unknown) => void;
  onRename?: (wheel: "colour" | "gobo" | "gobo2", slot: number, name: string) => void;
}) {
  const [renaming, setRenaming] = useState(false);
  const [names, setNames] = useState<Record<number, string>>({});
  const current = typeof value === "number" ? value : null;
  const label = (s: SlotChoice) => names[s.slot] ?? s.name;
  return (
    <div className="space-y-2">
      <div role="radiogroup" aria-label={c.label} className="flex flex-wrap gap-2">
        {c.slots.map((s) =>
          renaming && !s.open ? (
            <Input
              key={s.slot}
              aria-label={`Name of slot ${s.slot}`}
              value={label(s)}
              onChange={(e) => setNames((n) => ({ ...n, [s.slot]: e.target.value }))}
              className="h-11 w-36 rounded-full bg-white text-[15px]"
            />
          ) : (
            <button
              key={s.slot}
              type="button"
              role="radio"
              aria-checked={current === s.slot}
              onClick={() => onChange(c.attr, s.slot)}
              className={cn(CHIP, current === s.slot ? CHIP_ON : CHIP_OFF)}
            >
              {s.hex && <span aria-hidden className="h-4 w-4 rounded-full border border-stone-300" style={{ background: s.hex }} />}
              {label(s)}
            </button>
          ),
        )}
      </div>
      {onRename && (
        <Button
          variant="ghost"
          className="h-10 rounded-xl px-3 text-[15px] text-teal-800"
          onClick={() => {
            if (renaming) {
              for (const s of c.slots) {
                const n = names[s.slot];
                if (n !== undefined && n.trim() && n.trim() !== s.name) onRename(c.wheel, s.slot, n.trim());
              }
            }
            setRenaming((r) => !r);
          }}
        >
          {renaming ? <Check aria-hidden /> : <Pencil aria-hidden />} {renaming ? "Done renaming" : "Rename"}
        </Button>
      )}
    </div>
  );
}
