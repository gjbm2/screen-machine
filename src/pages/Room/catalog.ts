// Helpers over the catalog (LIVE_V1 §1.1): picker choices, schema defaults,
// and turning picker state into the spec forms room_service accepts.
//
// Nothing here knows about any particular plug-in. A new source, transition
// or preset in the catalog shows up in the pickers and gets a form from its
// schema without UI changes.

import type {
  Catalog,
  ParamSchema,
  Params,
  Profile,
  SceneSpec,
  SchemaProperty,
  TransitionSpec,
} from "./types";

export interface SceneChoice {
  /** "preset:<name>" or "source:<name>"; unique across both kinds. */
  key: string;
  kind: "preset" | "source";
  name: string;
  label: string;
  /** The source plug-in this choice renders with. */
  source: string;
  /** The preset's own params ({} for a raw source). */
  presetParams: Params;
  schema?: ParamSchema;
  description?: string;
}

export interface TransitionChoice {
  key: string;
  label: string;
  transition: string;
  params: Params;
  description?: string;
}

/** Scene presets first, then the raw sources a person may pick (internal ones, like `image`, are hidden). */
export function sceneChoices(catalog: Catalog | null): SceneChoice[] {
  if (!catalog) return [];
  const sources = catalog.sources ?? [];
  const byName = new Map(sources.map((s) => [s.name, s]));
  const out: SceneChoice[] = [];
  for (const [name, p] of Object.entries(catalog.presets?.scenes ?? {})) {
    const src = byName.get(p.source);
    out.push({
      key: `preset:${name}`,
      kind: "preset",
      name,
      label: p.label || name,
      source: p.source,
      presetParams: p.params ?? {},
      schema: src?.schema,
      description: src?.description,
    });
  }
  for (const s of sources) {
    if (s.internal) continue;
    out.push({
      key: `source:${s.name}`,
      kind: "source",
      name: s.name,
      label: s.label || s.name,
      source: s.name,
      presetParams: {},
      schema: s.schema,
      description: s.description,
    });
  }
  return out;
}

export function transitionChoices(catalog: Catalog | null): TransitionChoice[] {
  if (!catalog) return [];
  const byName = new Map((catalog.transitions ?? []).map((t) => [t.name, t]));
  return Object.entries(catalog.presets?.transitions ?? {}).map(([name, p]) => ({
    key: name,
    label: p.label || name,
    transition: p.transition,
    params: p.params ?? {},
    description: byName.get(p.transition)?.description,
  }));
}

export function schemaDefaults(schema?: ParamSchema): Params {
  const out: Params = {};
  for (const [k, prop] of Object.entries(schema?.properties ?? {})) {
    if (prop.default !== undefined) out[k] = prop.default;
  }
  return out;
}

/** What "reset to preset" returns to: schema defaults overlaid with the preset's params. */
export function baseValues(choice: SceneChoice | undefined): Params {
  if (!choice) return {};
  return { ...schemaDefaults(choice.schema), ...choice.presetParams };
}

/** Maps a scene spec (any §1.1 form) to a picker key plus its param overrides. */
export function parseSceneSpec(spec: SceneSpec | undefined | null): { key: string; overrides: Params } | null {
  if (!spec) return null;
  if (typeof spec === "string") return { key: `preset:${spec}`, overrides: {} };
  if ("preset" in spec && spec.preset) return { key: `preset:${spec.preset}`, overrides: { ...(spec.params ?? {}) } };
  if ("source" in spec && spec.source) return { key: `source:${spec.source}`, overrides: { ...(spec.params ?? {}) } };
  return null;
}

/**
 * The scene spec to send. A preset with no overrides goes as its bare name;
 * otherwise {preset, params} or {source, params}, carrying only the overrides
 * (stream_service fills in the rest from the preset and the schema defaults).
 */
export function buildSceneSpec(choice: SceneChoice, overrides: Params): SceneSpec {
  const hasOverrides = Object.keys(overrides).length > 0;
  if (choice.kind === "preset") return hasOverrides ? { preset: choice.name, params: overrides } : choice.name;
  return { source: choice.name, params: overrides };
}

/** A stable key for a transition spec: the preset name, or "raw:<plug-in>" for an inline one. */
export function transitionSpecKey(spec: TransitionSpec | undefined | null): string | null {
  if (!spec) return null;
  if (typeof spec === "string") return spec;
  if ("preset" in spec && spec.preset) return spec.preset;
  if ("transition" in spec && spec.transition) return `raw:${spec.transition}`;
  return null;
}

export function describeSceneSpec(spec: SceneSpec | undefined | null, choices: SceneChoice[]): string {
  const parsed = parseSceneSpec(spec);
  if (!parsed) return "?";
  const c = choices.find((x) => x.key === parsed.key);
  const name = c?.label ?? parsed.key.replace(/^(preset|source):/, "");
  const n = Object.keys(parsed.overrides).length;
  return n ? `${name} (+${n} param${n === 1 ? "" : "s"})` : name;
}

export function describeTransitionSpec(spec: TransitionSpec | undefined | null, choices: TransitionChoice[]): string {
  const key = transitionSpecKey(spec);
  if (!key) return "default";
  return choices.find((c) => c.key === key)?.label ?? key;
}

export function profileLabel(name: string, p?: Profile): string {
  if (!p) return name;
  const size = p.w && p.h ? `${p.w}×${p.h}` : "";
  const fps = p.fps ? `@${p.fps}` : "";
  return [p.codec?.toUpperCase(), `${size}${fps}`, p.bitrate].filter(Boolean).join(" · ");
}

/** The fields to show, in schema order, minus any marked hidden. */
export function visibleProperties(schema?: ParamSchema): Array<[string, SchemaProperty]> {
  return Object.entries(schema?.properties ?? {}).filter(([, p]) => !p["x-ui"]?.hidden);
}

export function valuesEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (typeof a === "number" && typeof b === "number") return Math.abs(a - b) < 1e-9;
  if (typeof a === "string" && typeof b === "string") return a.toLowerCase() === b.toLowerCase();
  return JSON.stringify(a) === JSON.stringify(b);
}

/** Drops overrides that equal the base value, so specs stay minimal. */
export function pruneOverrides(overrides: Params, base: Params): Params {
  const out: Params = {};
  for (const [k, v] of Object.entries(overrides)) {
    if (!valuesEqual(v, base[k])) out[k] = v;
  }
  return out;
}

/** Clamps and rounds a number per its schema (the server clamps too; this keeps the UI honest). */
export function coerceNumber(prop: SchemaProperty, v: number): number {
  let x = v;
  if (typeof prop.minimum === "number") x = Math.max(prop.minimum, x);
  if (typeof prop.maximum === "number") x = Math.min(prop.maximum, x);
  if (prop.type === "integer") x = Math.round(x);
  return x;
}

export function numberStep(prop: SchemaProperty): number {
  const s = prop["x-ui"]?.step ?? prop.multipleOf;
  if (typeof s === "number" && s > 0) return s;
  if (prop.type === "integer") return 1;
  if (typeof prop.minimum === "number" && typeof prop.maximum === "number") {
    const span = prop.maximum - prop.minimum;
    return span > 0 ? Number((span / 100).toPrecision(1)) : 0.01;
  }
  return 0.01;
}

/** Formats a number to the precision its step implies. */
export function formatNumber(v: unknown, step: number): string {
  if (typeof v !== "number" || !Number.isFinite(v)) return "—";
  const decimals = Math.min(6, Math.max(0, Math.ceil(-Math.log10(step) - 1e-9)));
  return v.toFixed(decimals);
}
