// A parameter form built from a plug-in's JSON Schema (LIVE_V1 §1.1).
//
// Widgets come from x-ui.widget (slider, number, select, checkbox, color).
// Without a hint the type decides: boolean → switch, enum → select,
// number with both bounds → slider, other numbers → number, string → text.
// Nothing here is specific to a plug-in, so a new scene needs no UI code.

import { useEffect, useRef, useState } from "react";
import { RotateCcw } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import { coerceNumber, formatNumber, numberStep, visibleProperties } from "./catalog";
import { THEME_CLASS } from "./theme";
import type { ParamSchema, Params, SchemaProperty } from "./types";

type ResolvedWidget = "slider" | "number" | "select" | "checkbox" | "color" | "text" | "readonly";

const HEX_RE = /^#[0-9a-fA-F]{6}$/;

function resolveWidget(prop: SchemaProperty): ResolvedWidget {
  const hint = prop["x-ui"]?.widget;
  const isNum = prop.type === "number" || prop.type === "integer";
  const bounded = typeof prop.minimum === "number" && typeof prop.maximum === "number";
  if (hint === "slider" && isNum) return bounded ? "slider" : "number";
  if (hint === "number" && isNum) return "number";
  if (hint === "select" && Array.isArray(prop.enum)) return "select";
  if (hint === "checkbox" && prop.type === "boolean") return "checkbox";
  if (hint === "color" && prop.type === "string") return "color";
  if (prop.type === "boolean") return "checkbox";
  if (Array.isArray(prop.enum) && prop.enum.length) return "select";
  if (isNum) return bounded ? "slider" : "number";
  if (prop.type === "string") return "text";
  return "readonly";
}

export interface ParamFormProps {
  idPrefix: string;
  schema?: ParamSchema;
  /** What each field shows. */
  values: Params;
  /** Keys whose value differs from the preset (shown with a reset button). */
  overridden: ReadonlySet<string>;
  onChange: (key: string, value: unknown) => void;
  onReset: (key: string) => void;
  disabled?: boolean;
  /** Shown when the schema has no visible fields; null shows nothing. */
  emptyText?: string | null;
  /**
   * The inline layout on a live screen's card: one line per field where it can be
   * (label beside the control), and a slider without its number box.
   */
  compact?: boolean;
}

export function ParamForm({
  idPrefix,
  schema,
  values,
  overridden,
  onChange,
  onReset,
  disabled,
  emptyText = "This scene has no settings.",
  compact = false,
}: ParamFormProps) {
  const props = visibleProperties(schema);
  if (!props.length) {
    return emptyText ? <p className="text-sm text-muted-foreground">{emptyText}</p> : null;
  }
  return (
    <div className={compact ? "space-y-2" : "space-y-4"}>
      {props.map(([key, prop]) => (
        <Field
          compact={compact}
          key={key}
          id={`${idPrefix}-${key}`}
          name={key}
          prop={prop}
          value={values[key]}
          modified={overridden.has(key)}
          onChange={(v) => onChange(key, v)}
          onReset={() => onReset(key)}
          disabled={disabled}
        />
      ))}
    </div>
  );
}

interface FieldProps {
  id: string;
  name: string;
  prop: SchemaProperty;
  value: unknown;
  modified: boolean;
  onChange: (v: unknown) => void;
  onReset: () => void;
  disabled?: boolean;
  compact?: boolean;
}

function Field({ id, name, prop, value, modified, onChange, onReset, disabled, compact }: FieldProps) {
  const ui = prop["x-ui"] ?? {};
  const label = ui.label || prop.title || name;
  const help = ui.help || prop.description;
  const widget = resolveWidget(prop);

  const resetBtn = modified ? (
    <button
      type="button"
      onClick={onReset}
      disabled={disabled}
      title="Back to the preset's value"
      aria-label={`Reset ${label}`}
      className="-my-2 grid h-11 w-11 shrink-0 place-items-center rounded-full text-muted-foreground hover:bg-stone-100 hover:text-foreground disabled:opacity-50"
    >
      <RotateCcw className="h-4 w-4" />
    </button>
  ) : null;

  const labelEl = (
    <Label htmlFor={id} className="flex min-w-0 items-center gap-1.5 text-[15px] font-medium leading-snug">
      <span className="truncate">{label}</span>
      {ui.unit && widget !== "slider" && <span className="font-normal text-muted-foreground">({ui.unit})</span>}
      {modified && <span className="h-2 w-2 shrink-0 rounded-full bg-teal-600" title="Changed from the preset" />}
    </Label>
  );

  if (widget === "checkbox") {
    return (
      <div data-param={name} className="space-y-1">
        <div className="flex min-h-11 items-center gap-3">
          <Switch id={id} checked={value === true} disabled={disabled} onCheckedChange={(c) => onChange(c === true)} />
          {labelEl}
          <span className="ml-auto">{resetBtn}</span>
        </div>
        {help && <p className="text-sm leading-snug text-muted-foreground">{help}</p>}
      </div>
    );
  }

  if (compact && widget === "slider") {
    // One row, like the compact selects: the label with its value under it, the slider beside.
    // Compact rows have no reset button (it would squeeze the label); "More" has it, and Reset all.
    const step = numberStep(prop);
    return (
      <div data-param={name} className="flex min-h-11 items-center gap-3">
        <div className="flex min-w-0 flex-1 items-center gap-1">
          <div className="min-w-0">
            {labelEl}
            <span className="block truncate font-mono text-[13px] leading-tight tabular-nums text-muted-foreground">
              {formatNumber(value, step)}
              {ui.unit ? <span className="font-sans"> {ui.unit}</span> : null}
            </span>
          </div>
        </div>
        <div className="flex w-[55%] max-w-[16rem] shrink-0">
          <SliderControl id={id} label={label} prop={prop} value={value} onChange={onChange} disabled={disabled} bare />
        </div>
      </div>
    );
  }
  if (compact && widget !== "readonly") {
    const narrow =
      widget === "select" ? (
        <SelectControl id={id} prop={prop} value={value} onChange={onChange} disabled={disabled} />
      ) : widget === "color" ? (
        <ColorSwatch value={value} onChange={onChange} disabled={disabled} id={id} />
      ) : widget === "number" ? (
        <NumberControl id={id} prop={prop} value={value} onChange={onChange} disabled={disabled} className="w-full" />
      ) : (
        <TextControl id={id} prop={prop} value={value} onChange={onChange} disabled={disabled} />
      );
    return (
      <div data-param={name} className="flex items-center gap-3">
        <div className="flex min-w-0 flex-1 items-center gap-1">{labelEl}</div>
        <div className="w-[55%] max-w-[16rem] shrink-0">{narrow}</div>
      </div>
    );
  }

  let control: JSX.Element;
  switch (widget) {
    case "slider":
      control = <SliderControl id={id} label={label} prop={prop} value={value} onChange={onChange} disabled={disabled} />;
      break;
    case "number":
      control = <NumberControl id={id} prop={prop} value={value} onChange={onChange} disabled={disabled} className="w-full" />;
      break;
    case "select":
      control = <SelectControl id={id} prop={prop} value={value} onChange={onChange} disabled={disabled} />;
      break;
    case "color":
      control = <ColorControl id={id} value={value} onChange={onChange} disabled={disabled} />;
      break;
    case "text":
      control = <TextControl id={id} prop={prop} value={value} onChange={onChange} disabled={disabled} />;
      break;
    default:
      control = (
        <pre className="overflow-x-auto rounded-lg bg-stone-100 p-2 text-xs text-muted-foreground">{JSON.stringify(value)}</pre>
      );
  }

  return (
    <div data-param={name} className="space-y-1.5">
      <div className="flex min-h-7 items-center justify-between gap-2">
        {labelEl}
        {resetBtn}
      </div>
      {control}
      {help && <p className="text-sm leading-snug text-muted-foreground">{help}</p>}
    </div>
  );
}

interface ControlProps {
  id: string;
  /** Accessible name for controls the <Label> can't point at (the slider). */
  label?: string;
  prop: SchemaProperty;
  value: unknown;
  onChange: (v: unknown) => void;
  disabled?: boolean;
  className?: string;
}

const INPUT = "h-11 rounded-xl bg-white text-base";

function SliderControl({ id, label, prop, value, onChange, disabled, bare }: ControlProps & { bare?: boolean }) {
  const step = numberStep(prop);
  const min = prop.minimum ?? 0;
  const max = prop.maximum ?? 1;
  const num = typeof value === "number" ? value : typeof prop.default === "number" ? prop.default : min;
  const slider = (
    <Slider
      id={bare ? id : undefined}
      aria-label={label}
      // A bigger thumb and a taller hit area for fingers.
      className="flex-1 py-3 [&_[role=slider]]:h-6 [&_[role=slider]]:w-6 [&_[role=slider]]:shadow-sm"
      min={min}
      max={max}
      step={step}
      value={[num]}
      disabled={disabled}
      onValueChange={([v]) => onChange(coerceNumber(prop, v))}
    />
  );
  if (bare) return slider;
  return (
    <div className="flex items-center gap-3">
      {slider}
      <div className="flex shrink-0 items-center gap-1.5">
        <NumberControl id={id} prop={prop} value={value} onChange={onChange} disabled={disabled} className="w-[5.5rem]" />
        {prop["x-ui"]?.unit && (
          <span className="hidden max-w-[5rem] truncate text-sm text-muted-foreground sm:inline">{prop["x-ui"].unit}</span>
        )}
      </div>
    </div>
  );
}

/**
 * A number input. Typed text is committed on Enter or blur (clamped), so typing
 * "1000" while live never sends 1, 10, 100 to the renderer. Spinner and arrow-key
 * steps commit at once.
 */
function NumberControl({ id, prop, value, onChange, disabled, className }: ControlProps) {
  const step = numberStep(prop);
  const fmt = (v: unknown) => (typeof v === "number" ? formatNumber(v, step) : "");
  const [text, setText] = useState(fmt(value));
  const focused = useRef(false);

  useEffect(() => {
    if (!focused.current) setText(fmt(value));
    // fmt depends only on step, which changes with prop
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, step]);

  const commit = (raw: string) => {
    // Unchanged text (a focus then blur, or Enter twice) commits nothing: no live PATCH, and a
    // value more precise than the display (0.123 shown as "0.12") isn't rounded by a visit.
    if (raw === fmt(value)) return;
    const n = Number.parseFloat(raw);
    if (Number.isFinite(n)) {
      const c = coerceNumber(prop, n);
      if (c !== value) onChange(c); // e.g. "0.30" after a spinner step to 0.3: nothing new to send
      setText(fmt(c));
    } else {
      setText(fmt(value));
    }
  };

  return (
    <Input
      id={id}
      type="number"
      inputMode="decimal"
      className={cn(INPUT, "px-2.5 font-mono tabular-nums", className)}
      value={text}
      min={prop.minimum}
      max={prop.maximum}
      step={step}
      disabled={disabled}
      onFocus={() => (focused.current = true)}
      onChange={(e) => {
        setText(e.target.value);
        // Typing has an inputType ("insertText", "deleteContentBackward", …); a spinner or arrow-key step has none.
        const stepped = !(e.nativeEvent as InputEvent).inputType;
        if (stepped) {
          const n = Number.parseFloat(e.target.value);
          if (Number.isFinite(n)) onChange(coerceNumber(prop, n));
        }
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter") commit(e.currentTarget.value);
      }}
      onBlur={(e) => {
        focused.current = false;
        commit(e.currentTarget.value);
      }}
    />
  );
}

/** Free text, committed on Enter or blur like a typed number, so a live screen never gets each keystroke. */
function TextControl({ id, value, onChange, disabled }: ControlProps) {
  const current = typeof value === "string" ? value : value === undefined ? "" : String(value);
  const [text, setText] = useState(current);
  const focused = useRef(false);
  useEffect(() => {
    if (!focused.current) setText(current);
  }, [current]);
  const commit = (raw: string) => {
    if (raw !== current) onChange(raw);
  };
  return (
    <Input
      id={id}
      className={INPUT}
      value={text}
      disabled={disabled}
      onFocus={() => (focused.current = true)}
      onChange={(e) => setText(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter") commit(e.currentTarget.value);
      }}
      onBlur={(e) => {
        focused.current = false;
        commit(e.currentTarget.value);
      }}
    />
  );
}

function SelectControl({ id, prop, value, onChange, disabled }: ControlProps) {
  const options = prop.enum ?? [];
  return (
    <Select
      // "" (not undefined) keeps the Select controlled while showing the placeholder.
      value={value === undefined ? "" : String(value)}
      disabled={disabled}
      // Map back to the enum's own value so numeric enums stay numbers.
      onValueChange={(s) => onChange(options.find((o) => String(o) === s) ?? s)}
    >
      <SelectTrigger id={id} className={cn(INPUT, "text-[15px]")}>
        <SelectValue placeholder="Choose…" />
      </SelectTrigger>
      <SelectContent className={cn(THEME_CLASS, "rounded-xl")}>
        {options.map((o) => (
          <SelectItem key={String(o)} value={String(o)} className="min-h-11 rounded-lg text-[15px]">
            {String(o)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

/** Just the colour picker, for the compact layout. */
function ColorSwatch({ id, value, onChange, disabled }: Omit<ControlProps, "prop">) {
  const hex = typeof value === "string" && HEX_RE.test(value) ? value : "#000000";
  return (
    <input
      id={id}
      type="color"
      className="ml-auto block h-11 w-full cursor-pointer rounded-xl border border-input bg-white p-1 disabled:cursor-not-allowed"
      value={hex.toLowerCase()}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value.toUpperCase())}
    />
  );
}

function ColorControl({ id, value, onChange, disabled }: Omit<ControlProps, "prop">) {
  const hex = typeof value === "string" && HEX_RE.test(value) ? value : "#000000";
  const [text, setText] = useState(hex);
  useEffect(() => setText(hex), [hex]);
  return (
    <div className="flex items-center gap-2">
      <input
        type="color"
        aria-label="Pick a colour"
        className="h-11 w-14 shrink-0 cursor-pointer rounded-xl border border-input bg-white p-1 disabled:cursor-not-allowed"
        value={hex.toLowerCase()}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value.toUpperCase())}
      />
      <Input
        id={id}
        className={cn(INPUT, "font-mono uppercase")}
        value={text}
        maxLength={7}
        disabled={disabled}
        onChange={(e) => {
          setText(e.target.value);
          if (HEX_RE.test(e.target.value)) onChange(e.target.value.toUpperCase());
        }}
        onBlur={() => setText(hex)}
      />
    </div>
  );
}
