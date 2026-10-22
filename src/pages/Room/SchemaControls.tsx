// One control for any value, drawn from its JSON schema (each target's value_schema in GET /api/v1/room/describe), so
// the page knows no device: screens, Sonos, Hue, blinds and the glitterball all go through these few controls, and
// a value the page can't draw is shown in words, never as JSON. Every word comes from the schema (c8's review #11).
//
// Read from x-ui: label, help, labels {value: words}, enum_labels [words by enum position], unset_label (what "not
// set" is called, on the field or its object), unit, step, presets, ends, hidden, advanced (behind "More"), ordered
// (an array whose order matters), exclusive (an object's fields that rule each other out: setting one clears the rest)
// and widget: slider, select, text, url, color, tags (scene tags), readonly.
// Alternatives (oneOf) are segments named by each branch's x-ui.label; picking one writes its default.

import { createContext, useContext, useRef, useState, type ReactNode } from "react";
import { ArrowDown, ArrowUp, ChevronDown, Minus, Plus, X } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Slider } from "@/components/ui/slider";
import { cn } from "@/lib/utils";
import type { SceneTagIndex } from "./sceneTags";
import { TagPicker } from "./TagPicker";

export interface XUi {
  label?: string;
  help?: string;
  labels?: Record<string, string>;
  /** Words for each enum value, by position (for values that aren't words, e.g. {"favourite": "BBC Radio 3"}). */
  enum_labels?: string[];
  /** What "not set" is called here ("Neutral (the music decides)"); an object's applies to its fields. */
  unset_label?: string;
  widget?: string;
  unit?: string;
  step?: number;
  presets?: Record<string, number>;
  /** Names for a slider's two ends; a [min, max] pair is read too. */
  ends?: { min: string; max: string } | [string, string];
  hidden?: boolean;
  advanced?: boolean;
  ordered?: boolean;
  placeholder?: string;
  /** A two-state device's on and off values (the glitterball: spinning, stopped). */
  switch?: { on: string; off: string };
  /** An object's fields that rule each other out (a light's colour, white or xy): setting one clears the rest. */
  exclusive?: string[][] | string[];

}

export interface Schema {
  /** A JSON-schema type, or several (["string", "null"]: optional). */
  type?: string | string[];
  /** Allowed values: words, or whole JSON values. */
  enum?: unknown[];
  const?: unknown;
  default?: unknown;
  minimum?: number;
  maximum?: number;
  properties?: Record<string, Schema>;
  required?: string[];
  items?: Schema;
  /** Alternatives (a screen: art, or {live: {...}}; a Hue light: follows the show, off, or set). */
  oneOf?: Schema[];
  title?: string;
  description?: string;
  "x-ui"?: XUi;
}

/** What the page shows when a value is set to something no control here can draw. */
export const CANT_SHOW = "Set to something this page can't show yet";

const NO_TAGS: SceneTagIndex = { scenes: [], facets: {} };
const TagIndex = createContext<SceneTagIndex>(NO_TAGS);

const ui = (s: Schema | undefined): XUi => s?.["x-ui"] ?? {};

function friendly(s: string): string {
  const t = s.replace(/[-_:.]+/g, " ").trim();
  return t.charAt(0).toUpperCase() + t.slice(1);
}

function clone<T>(v: T): T {
  return v === undefined ? v : (JSON.parse(JSON.stringify(v)) as T);
}

/** A value as JSON with keys in order, so equal values compare equal however they were written. */
export function jsonKey(v: unknown): string {
  return (
    JSON.stringify(v, (_, x) =>
      x && typeof x === "object" && !Array.isArray(x)
        ? Object.fromEntries(Object.entries(x as Record<string, unknown>).sort(([a], [b]) => a.localeCompare(b)))
        : x,
    ) ?? "undefined"
  );
}

function isType(s: Schema | undefined, t: string): boolean {
  const ty = s?.type;
  return Array.isArray(ty) ? ty.includes(t) : ty === t;
}

const isObject = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);
const isNumberSchema = (s: Schema) => isType(s, "number") || isType(s, "integer");
const isEmpty = (v: unknown) =>
  v === undefined || v === null || v === "" || (Array.isArray(v) && !v.length) || (isObject(v) && !Object.keys(v).length);

/** Does `v` fit this schema (enough to tell a oneOf's branches apart)? */
function fits(s: Schema, v: unknown): boolean {
  if ("const" in s) return jsonKey(s.const) === jsonKey(v);
  if (Array.isArray(s.enum)) return s.enum.some((e) => jsonKey(e) === jsonKey(v));
  if (v === null) return isType(s, "null");
  if (Array.isArray(v)) return isType(s, "array");
  if (isObject(v)) return (isType(s, "object") || !!s.properties) && (s.required ?? []).every((k) => k in v);
  if (typeof v === "string") return isType(s, "string");
  if (typeof v === "number") return isNumberSchema(s);
  if (typeof v === "boolean") return isType(s, "boolean");
  return false;
}

/** The value a schema starts at when picked: its default, its constant, or an object of its required and defaulted fields. */
export function defaultOf(s: Schema): unknown {
  if (s.default !== undefined) return clone(s.default);
  if ("const" in s) return clone(s.const);
  if (s.oneOf?.length) return defaultOf(s.oneOf[0]);
  if (Array.isArray(s.enum)) return s.enum.find((e) => e !== null);
  if (isType(s, "object") || s.properties) {
    const req = new Set(s.required ?? []);
    const out: Record<string, unknown> = {};
    for (const [k, p] of Object.entries(s.properties ?? {})) {
      if (req.has(k) || p.default !== undefined) out[k] = defaultOf(p);
    }
    return out;
  }
  if (isType(s, "array")) return [];
  if (isType(s, "boolean")) return false;
  if (isNumberSchema(s)) return s.minimum ?? 0;
  if (isType(s, "string")) return "";
  return undefined;
}

/** The schema's fixed choices with their words: enum, x-ui.labels without an enum, or yes/no. Null is left out. */
function choicesOf(s: Schema): { v: unknown; words: string }[] | null {
  const u = ui(s);
  if (Array.isArray(s.enum)) {
    return s.enum
      .map((v, i) => ({ v, words: u.enum_labels?.[i] ?? (typeof v === "string" ? (u.labels?.[v] ?? friendly(v)) : inWords({}, v)) }))
      .filter((o) => o.v !== null);
  }
  if (!s.type && !s.properties && u.labels && Object.keys(u.labels).length) {
    return Object.entries(u.labels).map(([v, words]) => ({ v, words }));
  }
  if (isType(s, "boolean")) {
    return [
      { v: true, words: u.labels?.true ?? "Yes" },
      { v: false, words: u.labels?.false ?? "No" },
    ];
  }
  return null;
}

function numberWords(s: Schema, n: number): string {
  if (typeof s.minimum === "number" && typeof s.maximum === "number" && s.maximum - s.minimum <= 1) {
    return `${Math.round(n * 100)}%`;
  }
  const unit = ui(s).unit ?? "";
  return `${n}${/^[a-z]/i.test(unit) ? " " : ""}${unit}`;
}

/** A scene tag in words: "piano", or "piano (modes only)" for an exact "modes:piano". */
function tagWords(tag: string): string {
  const i = tag.indexOf(":");
  return i > 0 ? `${tag.slice(i + 1)} (${tag.slice(0, i).replace(/[-_]+/g, " ")} only)` : tag;
}

/** An object's set fields in words: "Brightness 60%, Colour #2f6bff"; a field that is itself an object, by its label. */
/** The value has its own words in the schema (x-ui.labels for it, or an enum label), so no field label goes before it. */
function ownWords(p: Schema | undefined, v: unknown): boolean {
  const u = ui(p ?? {});
  if (typeof v === "boolean" || typeof v === "string") {
    if (u.labels?.[String(v)]) return true;
  }
  const i = Array.isArray(p?.enum) ? p.enum.findIndex((e) => jsonKey(e) === jsonKey(v)) : -1;
  return i >= 0 && !!u.enum_labels?.[i];
}

function objectWords(s: Schema, o: Record<string, unknown>): string {
  const props = s.properties ?? {};
  const keys = Object.keys(props);
  // A wrapper with one object field (a screen's {live: {...}}) reads as that field.
  if (keys.length === 1 && isObject(o[keys[0]]) && props[keys[0]].properties) return objectWords(props[keys[0]], o[keys[0]] as Record<string, unknown>);
  const parts: string[] = [];
  for (const [k, v] of Object.entries(o)) {
    const p = props[k];
    if (ui(p).hidden || ui(p).advanced) continue;
    if (isEmpty(v)) {
      if (v === null && ui(p).unset_label) parts.push(ui(p).unset_label as string);   // "No feel"
      continue;
    }
    const label = ui(p).label ?? p?.title ?? friendly(k);
    if (ownWords(p, v)) parts.push(inWords(p, v));                  // "AI listening: off" says it all
    else if (v === true || (isObject(v) && !p?.oneOf)) parts.push(label);
    else parts.push(`${label} ${inWords(p, v)}`);
  }
  return parts.join(", ");
}

/** A value in words, for rows the page can't edit, inherited values and the mode tiles. */
export function inWords(schema: Schema | undefined, v: unknown): string {
  if (v === undefined) return "—";
  const s = schema ?? {};
  const u = ui(s);
  if (Array.isArray(s.enum)) {
    const i = s.enum.findIndex((e) => jsonKey(e) === jsonKey(v));
    if (i >= 0 && u.enum_labels?.[i]) return u.enum_labels[i];
  }
  if (typeof v === "string" && u.labels?.[v]) return u.labels[v];
  if (s.oneOf) {
    const b = s.oneOf.find((x) => fits(x, v));
    if (b) {
      const label = ui(b).label ?? b.title;
      const rest = "const" in b ? "" : isObject(v) ? objectWords(b, v) : inWords(b, v);
      if (label) return rest ? `${label}: ${rest}` : label;
      return rest || inWords({}, v);
    }
  }
  if (v === null) return u.unset_label ?? "—";
  if (typeof v === "boolean") return v ? (u.labels?.true ?? "Yes") : (u.labels?.false ?? "No");
  if (typeof v === "number") return numberWords(s, v);
  if (typeof v === "string") return Array.isArray(s.enum) ? friendly(v) : v;
  if (Array.isArray(v)) {
    if (!v.length) return "None";
    return v.map((x) => (u.widget === "tags" && typeof x === "string" ? tagWords(x) : inWords(s.items, x))).join(", ");
  }
  if (isObject(v)) return objectWords(s, v) || CANT_SHOW;
  return CANT_SHOW;
}

/** Can the page draw a control for this schema (rather than show the value in words)? */
export function editable(s: Schema | undefined): boolean {
  if (!s) return false;
  const u = ui(s);
  if (u.widget === "readonly") return false;
  if (s.oneOf?.length) return true;
  if (u.widget === "tags") return true;
  if (choicesOf(s)?.length) return true;
  if (isType(s, "array")) return !!s.items && (!!choicesOf(s.items)?.length || isType(s.items, "string"));
  if (isType(s, "object") || s.properties) return Object.values(s.properties ?? {}).some((p) => !ui(p).hidden && editable(p));
  return isNumberSchema(s) || isType(s, "string") || u.widget === "text" || u.widget === "url" || u.widget === "color";
}

// ── controls ───────────────────────────────────────────────────────────────

type ControlProps = { schema: Schema; value: unknown; onChange: (v: unknown) => void; disabled?: boolean };

const chip = (on: boolean) =>
  cn(
    "min-h-11 rounded-xl border px-2 text-[15px] transition-colors disabled:opacity-60",
    on ? "border-teal-300 bg-teal-50 font-semibold text-teal-900" : "border-stone-200 bg-white hover:bg-stone-50",
  );

function Words({ schema, value }: { schema: Schema; value: unknown }) {
  return <p className="text-base text-stone-700">{inWords(schema, value)}</p>;
}

/** Tap one of a few choices; a dropdown when there are more than four. A value that isn't a choice shows as "Now: …". */
function ChoiceControl({ schema, value, onChange, disabled, options }: ControlProps & { options: { v: unknown; words: string }[] }) {
  const picked = options.findIndex((o) => jsonKey(o.v) === jsonKey(value));
  const offList = picked < 0 && value !== undefined && value !== null;
  if (options.length <= 4) {
    return (
      <div className="space-y-2">
        {offList && <p className="text-base text-stone-700">Now: {inWords(schema, value)}</p>}
        <div className="grid gap-2" style={{ gridTemplateColumns: `repeat(${options.length}, minmax(0, 1fr))` }}>
          {options.map((o) => (
            <button key={jsonKey(o.v)} type="button" disabled={disabled} onClick={() => onChange(clone(o.v))} className={chip(picked >= 0 && options[picked] === o)}>
              {o.words}
            </button>
          ))}
        </div>
      </div>
    );
  }
  return (
    <select
      className="h-11 w-full rounded-xl border border-stone-200 bg-white px-3 text-base"
      value={picked < 0 ? "" : String(picked)}
      disabled={disabled}
      onChange={(e) => e.currentTarget.value !== "" && onChange(clone(options[Number(e.currentTarget.value)].v))}
    >
      {picked < 0 && <option value="">{offList ? `Now: ${inWords(schema, value)}` : (ui(schema).unset_label ?? "—")}</option>}
      {options.map((o, i) => (
        <option key={jsonKey(o.v)} value={String(i)}>
          {o.words}
        </option>
      ))}
    </select>
  );
}

function endsOf(s: Schema): { min: string; max: string } | null {
  const e = ui(s).ends;
  if (!e) return null;
  return Array.isArray(e) ? { min: e[0], max: e[1] } : e;
}

/** A number in a range. Unset (left to the receiver's default, its "not set" chip selected): the thumb sits in the middle and the number reads "—". */
function SliderControl({ schema, value, onChange, disabled }: ControlProps) {
  const u = ui(schema);
  const ends = endsOf(schema);
  const lo = schema.minimum ?? 0;
  const hi = schema.maximum ?? 100;
  const unset = typeof value !== "number";
  const unit = hi - lo <= 1;                       // a 0-1 scale: shown as a percentage, moved in 5% steps
  const n = unset ? (lo + hi) / 2 : (value as number);
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-3">
        {ends && <span className="shrink-0 text-sm text-muted-foreground">{ends.min}</span>}
        <Slider
          className="flex-1 py-3 [&_[role=slider]]:h-6 [&_[role=slider]]:w-6"
          min={lo}
          max={hi}
          step={u.step ?? (unit ? 0.05 : 1)}
          value={[n]}
          disabled={disabled}
          onValueChange={([v]) => onChange(unit ? Math.round(v * 100) / 100 : v)}
        />
        {ends && <span className="shrink-0 text-sm text-muted-foreground">{ends.max}</span>}
        <span className="w-14 shrink-0 text-right font-mono text-[15px] tabular-nums">{unset ? "—" : numberWords(schema, n)}</span>
      </div>
      {u.presets && (
        <div className="flex flex-wrap gap-2">
          {Object.entries(u.presets).map(([k, v]) => (
            <button
              key={k}
              type="button"
              disabled={disabled}
              onClick={() => onChange(v)}
              className="min-h-9 rounded-lg border border-stone-200 bg-white px-3 text-sm hover:bg-stone-50"
            >
              {friendly(k)}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** A number without a range: minus, the number (typed in if wanted), plus, and its unit. */
function NumberStepper({ schema, value, onChange, disabled }: ControlProps) {
  const u = ui(schema);
  const int = isType(schema, "integer");
  const step = u.step ?? 1;
  const n = typeof value === "number" ? value : undefined;
  const fit = (x: number) => {
    const r = int ? Math.round(x) : Math.round(x * 1000) / 1000;
    return Math.min(schema.maximum ?? Infinity, Math.max(schema.minimum ?? -Infinity, r));
  };
  const btn = "grid h-11 w-11 shrink-0 place-items-center rounded-xl border border-stone-200 bg-white hover:bg-stone-50 disabled:opacity-60";
  return (
    <div className="flex items-center gap-2">
      <button type="button" aria-label="Decrease" disabled={disabled} onClick={() => onChange(fit((n ?? defaultNumber(schema)) - step))} className={btn}>
        <Minus className="h-4 w-4" aria-hidden />
      </button>
      <Input
        type="number"
        inputMode="decimal"
        className="h-11 w-28 text-center text-base tabular-nums"
        placeholder={u.unset_label ?? "—"}
        value={n ?? ""}
        step={step}
        disabled={disabled}
        onChange={(e) => {
          const t = e.currentTarget.value;
          if (t === "") onChange(undefined);
          else if (Number.isFinite(Number(t))) onChange(fit(Number(t)));
        }}
      />
      <button type="button" aria-label="Increase" disabled={disabled} onClick={() => onChange(fit((n ?? defaultNumber(schema)) + step))} className={btn}>
        <Plus className="h-4 w-4" aria-hidden />
      </button>
      {u.unit && <span className="text-base text-stone-700">{u.unit}</span>}
    </div>
  );
}

function defaultNumber(s: Schema): number {
  return typeof s.default === "number" ? s.default : (s.minimum ?? 0);
}

/** Words typed in (x-ui.widget text or url, or any plain string). Emptied = not set. */
function TextControl({ schema, value, onChange, disabled }: ControlProps) {
  const u = ui(schema);
  const url = u.widget === "url";
  return (
    <Input
      type={url ? "url" : "text"}
      inputMode={url ? "url" : undefined}
      className="h-11 text-base"
      placeholder={u.placeholder ?? u.unset_label ?? ""}
      value={typeof value === "string" ? value : ""}
      disabled={disabled}
      onChange={(e) => onChange(e.currentTarget.value === "" ? undefined : e.currentTarget.value)}
    />
  );
}

/** A colour (x-ui.widget color), written as #rrggbb. A value kept in another form leaves the swatch empty. */
function ColorControl({ schema, value, onChange, disabled }: ControlProps) {
  const hex = typeof value === "string" && /^#[0-9a-f]{6}$/i.test(value) ? value : null;
  return (
    <div className="flex items-center gap-3">
      <input
        type="color"
        aria-label={ui(schema).label ?? "Colour"}
        className={cn("h-11 w-16 cursor-pointer rounded-xl border border-stone-200 bg-white p-1 disabled:opacity-60", !hex && "opacity-40")}
        value={hex ?? "#ffffff"}
        disabled={disabled}
        onChange={(e) => onChange(e.currentTarget.value)}
      />
      <span className="font-mono text-[15px] text-stone-700">{hex ?? ui(schema).unset_label ?? "—"}</span>
    </div>
  );
}

/** Several of a list of choices: tap to include or leave out. */
function PickSeveral({ schema, value, onChange, disabled }: ControlProps) {
  const list = Array.isArray(value) ? value : [];
  const options = (schema.items && choicesOf(schema.items)) ?? [];
  const has = (v: unknown) => list.some((x) => jsonKey(x) === jsonKey(v));
  return (
    <div className="flex flex-wrap gap-2">
      {options.map((o) => {
        const on = has(o.v);
        return (
          <button
            key={jsonKey(o.v)}
            type="button"
            aria-pressed={on}
            disabled={disabled}
            onClick={() => onChange(on ? list.filter((x) => jsonKey(x) !== jsonKey(o.v)) : [...list, clone(o.v)])}
            className={cn(
              "min-h-9 rounded-full border px-3 text-[15px] transition-colors disabled:opacity-60",
              on ? "border-teal-300 bg-teal-50 font-semibold text-teal-900" : "border-stone-200 bg-white hover:bg-stone-50",
            )}
          >
            {o.words}
          </button>
        );
      })}
    </div>
  );
}

/** A list in order (x-ui.ordered), or a list of typed-in words: each item with up, down and remove; add at the end. */
function ListControl({ schema, value, onChange, disabled }: ControlProps) {
  const list = Array.isArray(value) ? value : [];
  const ordered = !!ui(schema).ordered;
  const options = schema.items ? choicesOf(schema.items) : null;
  const [draft, setDraft] = useState("");
  const move = (i: number, d: number) => {
    const next = [...list];
    [next[i], next[i + d]] = [next[i + d], next[i]];
    onChange(next);
  };
  const add = (v: unknown) => onChange([...list, v]);
  const left = options?.filter((o) => !list.some((x) => jsonKey(x) === jsonKey(o.v))) ?? [];
  const icon = "grid h-9 w-9 place-items-center rounded-lg hover:bg-stone-100 disabled:opacity-40";
  return (
    <div className="space-y-2">
      {list.length > 0 && (
        <ol className="space-y-1">
          {list.map((x, i) => (
            <li key={`${i}:${jsonKey(x)}`} className="flex min-h-11 items-center gap-1 rounded-xl border border-stone-200 bg-white pl-3 pr-1">
              <span className="flex-1 text-[15px]">
                {ordered && <span className="mr-2 text-sm text-muted-foreground">{i + 1}.</span>}
                {inWords(schema.items, x)}
              </span>
              {ordered && (
                <>
                  <button type="button" aria-label="Move up" disabled={disabled || i === 0} onClick={() => move(i, -1)} className={icon}>
                    <ArrowUp className="h-4 w-4" aria-hidden />
                  </button>
                  <button type="button" aria-label="Move down" disabled={disabled || i === list.length - 1} onClick={() => move(i, 1)} className={icon}>
                    <ArrowDown className="h-4 w-4" aria-hidden />
                  </button>
                </>
              )}
              <button type="button" aria-label="Remove" disabled={disabled} onClick={() => onChange(list.filter((_, j) => j !== i))} className={icon}>
                <X className="h-4 w-4" aria-hidden />
              </button>
            </li>
          ))}
        </ol>
      )}
      {options ? (
        left.length > 0 && (
          <select
            className="h-11 w-full rounded-xl border border-stone-200 bg-white px-3 text-base"
            value=""
            disabled={disabled}
            onChange={(e) => e.currentTarget.value !== "" && add(clone(left[Number(e.currentTarget.value)].v))}
          >
            <option value="">Add…</option>
            {left.map((o, i) => (
              <option key={jsonKey(o.v)} value={String(i)}>
                {o.words}
              </option>
            ))}
          </select>
        )
      ) : (
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (draft.trim()) add(draft.trim());
            setDraft("");
          }}
        >
          <Input className="h-11 flex-1 text-base" value={draft} disabled={disabled} onChange={(e) => setDraft(e.currentTarget.value)} />
          <button type="submit" disabled={disabled || !draft.trim()} className="min-h-11 rounded-xl border border-stone-200 bg-white px-4 text-[15px] hover:bg-stone-50 disabled:opacity-60">
            Add
          </button>
        </form>
      )}
    </div>
  );
}

/** Scene tags (x-ui.widget tags): the scene library's words, searchable, with how many scenes each choice leaves. */
function TagsControl(props: ControlProps) {
  const index = useContext(TagIndex);
  if (!index.scenes.length) return <ListControl {...props} />;
  return (
    <TagPicker
      index={index}
      value={Array.isArray(props.value) ? (props.value as string[]) : []}
      onChange={(v) => props.onChange(v.length ? v : undefined)}
      title={ui(props.schema).label}
      help={ui(props.schema).help}
    />
  );
}

/**
 * Alternatives (oneOf): a segment per branch, named by its x-ui.label, then the chosen branch's own fields. Picking a
 * branch writes its default, or what it held before in this sitting, so trying Art and back keeps Live's settings.
 */
function OneOfControl({ schema, value, onChange, disabled }: ControlProps) {
  const branches = schema.oneOf ?? [];
  const at = value === undefined ? -1 : branches.findIndex((b) => fits(b, value));
  const held = useRef<Record<number, unknown>>({});
  const name = (b: Schema) => ui(b).label ?? b.title ?? ("const" in b ? inWords(schema, b.const) : friendly(String(b.type ?? "")));
  const pick = (i: number) => {
    if (i === at) return;
    if (at >= 0) held.current[at] = value;
    onChange(held.current[i] ?? defaultOf(branches[i]));
  };
  const b = at >= 0 ? branches[at] : null;
  const many = branches.length > 4;
  return (
    <div className="space-y-3">
      {many ? (
        <select className="h-11 w-full rounded-xl border border-stone-200 bg-white px-3 text-base" value={at < 0 ? "" : String(at)} disabled={disabled} onChange={(e) => e.currentTarget.value !== "" && pick(Number(e.currentTarget.value))}>
          {at < 0 && <option value="">{ui(schema).unset_label ?? "—"}</option>}
          {branches.map((x, i) => (
            <option key={i} value={String(i)}>
              {name(x)}
            </option>
          ))}
        </select>
      ) : (
        <div className="grid gap-2" style={{ gridTemplateColumns: `repeat(${branches.length}, minmax(0, 1fr))` }}>
          {branches.map((x, i) => (
            <button key={i} type="button" aria-pressed={i === at} disabled={disabled} onClick={() => pick(i)} className={chip(i === at)}>
              {name(x)}
            </button>
          ))}
        </div>
      )}
      {at < 0 && value !== undefined && value !== null && <p className="text-base text-stone-700">Now: {inWords(schema, value)}</p>}
      {b && ui(b).help && <p className="text-sm text-muted-foreground">{ui(b).help}</p>}
      {b && !("const" in b) && editable(b) && <Control schema={b} value={value} onChange={onChange} disabled={disabled} />}
    </div>
  );
}

/** An object's set fields in a line, for a folded control: "Energy 35% · Elegant, Dreamy · Transitions: Gentle". */
function foldWords(s: Schema, v: unknown): string {
  if (!isObject(v)) return "";
  const out: string[] = [];
  for (const [k, p] of Object.entries(s.properties ?? {})) {
    const x = v[k];
    if (isEmpty(x) || ui(p).hidden) continue;
    const label = ui(p).label ?? p.title ?? friendly(k);
    if ((isType(p, "object") || p.properties) && !p.oneOf) {
      const w = foldWords(p, x);
      if (w) out.push(`${label}: ${w}`);
    } else if (Array.isArray(x)) out.push(inWords(p, x));
    else if (typeof x === "number") out.push(`${label} ${inWords(p, x)}`);
    else out.push(`${label}: ${inWords(p, x)}`);
  }
  return out.join(" · ");
}

/** A control folded to its summary line; tap to open. */
function Folded({ title, summary, children }: { title: string; summary: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-xl border border-stone-200">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="flex min-h-11 w-full items-center gap-2 px-3 py-2 text-left"
      >
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-semibold text-stone-800">{title}</span>
          <span className="block text-sm text-muted-foreground">
            {summary} · <span className="text-teal-800 underline underline-offset-2">{open ? "Done" : "Change"}</span>
          </span>
        </span>
        <ChevronDown aria-hidden className={cn("h-4 w-4 shrink-0 text-muted-foreground transition-transform", open && "rotate-180")} />
      </button>
      {open && <div className="border-t border-stone-100 p-3">{children}</div>}
    </div>
  );
}

/** One field of an object: its label and help, the "not set" choice if it's optional, and its control (or words). */
function Field({
  name,
  schema,
  value,
  required,
  unsetLabel,
  onChange,
  disabled,
}: ControlProps & { name: string; required: boolean; unsetLabel?: string }) {
  const u = ui(schema);
  const unset = value === undefined || value === null;
  const can = editable(schema);
  if (!can && unset) return null;
  const label = u.label ?? schema.title ?? friendly(name);
  // The tag picker folds itself, under the field's own label and help.
  if (can && u.widget === "tags") return <Control schema={schema} value={value} onChange={onChange} disabled={disabled} />;
  const box = can && !schema.oneOf && (isType(schema, "object") || !!schema.properties);
  const list = isType(schema, "array");
  const unsetWords = u.unset_label ?? unsetLabel ?? "Any";
  if (box) {
    return (
      <Folded title={label} summary={foldWords(schema, value) || unsetWords}>
        {u.help && <p className="mb-2 text-sm text-muted-foreground">{u.help}</p>}
        <Control schema={schema} value={value} onChange={onChange} disabled={disabled} unsetLabel={u.unset_label ?? unsetLabel} />
      </Folded>
    );
  }
  return (
    <div>
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="text-sm font-medium text-stone-700">
          {label}
          {u.help && <span className="block font-normal text-muted-foreground">{u.help}</span>}
        </span>
        {!required && !list && (
          <button
            type="button"
            aria-pressed={unset}
            disabled={disabled}
            onClick={() => !unset && onChange(undefined)}
            className={cn(
              "min-h-8 shrink-0 rounded-full border px-3 text-sm transition-colors",
              unset ? "border-teal-300 bg-teal-50 font-semibold text-teal-900" : "border-stone-200 bg-white text-stone-600 hover:bg-stone-50",
            )}
          >
            {unsetWords}
          </button>
        )}
      </div>
      {can ? <Control schema={schema} value={value} onChange={onChange} disabled={disabled} unsetLabel={u.unset_label ?? unsetLabel} /> : <Words schema={schema} value={value} />}
    </div>
  );
}

/**
 * An object's fields: each drawn by its own schema; hidden ones left out; advanced ones behind "More"; fields the
 * schema doesn't know (left from older config) shown in words with a way to clear them. A wrapper with a single object
 * field (a screen's {live: {...}}) draws that field's fields directly.
 */
function ObjectControl({ schema, value, onChange, disabled, unsetLabel }: ControlProps & { unsetLabel?: string }) {
  const [more, setMore] = useState(false);
  const obj = isObject(value) ? value : {};
  const props = schema.properties ?? {};
  const keys = Object.keys(props);
  const required = new Set(schema.required ?? []);
  const inherit = ui(schema).unset_label ?? unsetLabel;
  if (keys.length === 1 && (isType(props[keys[0]], "object") || props[keys[0]].properties) && !props[keys[0]].oneOf) {
    const k = keys[0];
    return (
      <ObjectControl
        schema={props[k]}
        value={obj[k]}
        onChange={(nv) => onChange({ ...obj, [k]: isObject(nv) ? nv : {} })}
        disabled={disabled}
        unsetLabel={inherit}
      />
    );
  }
  const ex = ui(schema).exclusive ?? [];
  const groups = (ex.length && typeof ex[0] === "string" ? [ex] : ex) as string[][];
  const set = (k: string, v: unknown) => {
    const next = { ...obj };
    if (isEmpty(v) && !required.has(k)) delete next[k];
    else {
      next[k] = v;
      for (const g of groups) if (g.includes(k)) for (const o of g) if (o !== k) delete next[o];
    }
    onChange(next);
  };
  const shown = keys.filter((k) => !ui(props[k]).hidden);
  const plain = shown.filter((k) => !ui(props[k]).advanced);
  const advanced = shown.filter((k) => ui(props[k]).advanced);
  const unknown = Object.keys(obj).filter((k) => !(k in props) && !isEmpty(obj[k]));
  const field = (k: string, s: Schema) => (
    <Field key={k} name={k} schema={s} value={obj[k]} required={required.has(k)} unsetLabel={inherit} onChange={(v) => set(k, v)} disabled={disabled} />
  );
  return (
    <div className="space-y-3">
      {plain.map((k) => field(k, props[k]))}
      {unknown.map((k) => field(k, {}))}
      {advanced.length > 0 &&
        (more || advanced.some((k) => !isEmpty(obj[k])) ? (
          advanced.map((k) => field(k, props[k]))
        ) : (
          <button type="button" onClick={() => setMore(true)} className="inline-flex min-h-9 items-center gap-1 text-sm text-stone-600 underline underline-offset-2 hover:text-stone-900">
            More <ChevronDown className="h-3.5 w-3.5" aria-hidden />
          </button>
        ))}
    </div>
  );
}

function Control(props: ControlProps & { unsetLabel?: string }) {
  const { schema, value } = props;
  const u = ui(schema);
  if (u.widget === "readonly") return <Words schema={schema} value={value} />;
  if (schema.oneOf?.length) return <OneOfControl {...props} />;
  if (u.widget === "tags") return <TagsControl {...props} />;
  if (u.widget === "color") return <ColorControl {...props} />;
  const options = choicesOf(schema);
  if (options?.length) return <ChoiceControl {...props} options={options} />;
  if (isType(schema, "array")) {
    if (!editable(schema)) return <Words schema={schema} value={value} />;
    return schema.items && choicesOf(schema.items) && !u.ordered ? <PickSeveral {...props} /> : <ListControl {...props} />;
  }
  if (isType(schema, "object") || schema.properties) {
    return editable(schema) ? <ObjectControl {...props} /> : <Words schema={schema} value={value} />;
  }
  if (isNumberSchema(schema)) {
    return typeof schema.minimum === "number" && typeof schema.maximum === "number" ? <SliderControl {...props} /> : <NumberStepper {...props} />;
  }
  if (isType(schema, "string") || u.widget === "text" || u.widget === "url") return <TextControl {...props} />;
  return <Words schema={schema} value={value} />;
}

/** The control for one value, drawn from its schema. `tagIndex` (the scene library's tags) feeds any scene-tag field. */
export function ValueControl({
  schema,
  value,
  onChange,
  disabled,
  tagIndex,
}: ControlProps & { tagIndex?: SceneTagIndex }) {
  const body = <Control schema={schema} value={value} onChange={onChange} disabled={disabled} />;
  return tagIndex ? <TagIndex.Provider value={tagIndex}>{body}</TagIndex.Provider> : body;
}
