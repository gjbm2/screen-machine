// What a mode does to the room, in plain words, from GET /api/v1/room/describe (no device text in code). The owner
// (16 Oct) didn't know Piano lowered the blinds, so each tile says what its mode changes before anyone picks it
// ("Blinds lowered · Glitterball spinning · Hue follows the show"), the progress line says what a switch is doing
// ("glitterball starting"), and the menu says which devices aren't connected yet. Names come from the targets' labels
// and x-ui labels, value words from their schemas (oneOf labels, presets, x-ui.labels, x-ui.switch).

import { CANT_SHOW, inWords, type Schema } from "./SchemaControls";
import type { RoomDescribe, RoomStatus, TransitionStep } from "./types";

type Target = RoomDescribe["targets"][string] & { subsystem?: string; member?: string; category?: string };
type Resolved = { verb?: string; value?: unknown };
type SubInfo = { label?: string; available?: boolean; placeholder?: boolean; dry_run?: boolean; members?: string[]; category?: string; kind?: string };
/** describe, with the parts read here that types.ts doesn't name. */
type Describe = RoomDescribe & { targets: Record<string, Target>; subsystems?: Record<string, SubInfo> };
type Live = RoomStatus["subsystems"];

export interface WordsOpts {
  /** Names that beat a target's own label (the screens' names from status), by target. */
  names?: Record<string, string>;
  /** status.subsystems: what each device is doing now, and whether it's reachable. */
  live?: Live;
}

const isSet = (e: Resolved | undefined) => !!e && (e.verb === "set" || e.verb === "store+set" || e.verb === "set+restore");

function friendly(s: string): string {
  const t = s.replace(/[-_:.]+/g, " ").trim();
  return t.length <= 2 ? t.toUpperCase() : t.charAt(0).toUpperCase() + t.slice(1);
}

/** "Glitterball" → "glitterball"; "TVs", "DMX power" stay as they are. */
function lowerFirst(s: string): string {
  return /^[A-Z]{2}/.test(s) ? s : s.charAt(0).toLowerCase() + s.slice(1);
}

function cap(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function jsonKey(v: unknown): string {
  return (
    JSON.stringify(v, (_, x) =>
      x && typeof x === "object" && !Array.isArray(x)
        ? Object.fromEntries(Object.entries(x as Record<string, unknown>).sort(([a], [b]) => a.localeCompare(b)))
        : x,
    ) ?? "undefined"
  );
}

const same = (a: unknown, b: unknown) => jsonKey(a) === jsonKey(b);

function subOf(d: Describe, t: string): string {
  return d.targets[t]?.subsystem ?? t.split(".")[0];
}

function memberOf(d: Describe, t: string): string {
  const s = subOf(d, t);
  return d.targets[t]?.member ?? (t.startsWith(`${s}.`) ? t.slice(s.length + 1) : t);
}

/** Not a thing in the room: describe's category "setting" (the pages' reloads, the light-sensor hold), or an action. */
function notADevice(d: Describe, sub: string, t?: string): boolean {
  const i = d.subsystems?.[sub];
  return ((t && d.targets[t]?.category) || i?.category) === "setting" || i?.kind === "action";
}

function schemaOf(d: Describe, t: string): Schema | undefined {
  return d.targets[t]?.schema as Schema | undefined;
}

/** A device's name: a name from `names`, its label, its x-ui label, else its member id in words. */
export function targetName(describe: RoomDescribe, t: string, names?: Record<string, string>): string {
  const d = describe as Describe;
  if (names?.[t]) return names[t];
  const m = memberOf(d, t);
  const label = d.targets[t]?.label;
  if (label && label !== m && label !== t) return label;
  const ui = schemaOf(d, t)?.["x-ui"]?.label;
  return friendly(ui && ui !== m ? ui : m);
}

/** A kind of device by its subsystem: "Blinds", "Hue", and "TVs" for more than one TV. */
function kindName(sub: string, n: number): string {
  const k = friendly(sub);
  return n > 1 && /^[A-Z0-9]+$/.test(k) ? `${k}s` : k;
}

/** Its devices do nothing real yet: a placeholder, unreachable, or a dry run. */
function offline(d: Describe, sub: string, live?: Live): boolean {
  const i = d.subsystems?.[sub];
  const l = live?.[sub];
  return !!(i?.placeholder || (l?.available ?? i?.available) === false || (l?.dry_run ?? i?.dry_run));
}

/** A value in a few words, from its schema: the oneOf alternative's label ("Live"), a preset's name ("Lowered"),
 *  x-ui.labels ("Follows the show"), else the editor's words. */
export function valueWords(schema: Schema | undefined, v: unknown): string {
  const obj = v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
  const alt = schema?.oneOf?.find((a) => {
    if ("const" in a) return same(a.const, v);
    const req = (a as { required?: string[] }).required ?? Object.keys(a.properties ?? {});
    return !!obj && req.length > 0 && req.every((k) => k in obj);
  });
  if (alt?.["x-ui"]?.label) return alt["x-ui"].label;
  if (typeof v === "number") {
    const preset = Object.entries(schema?.["x-ui"]?.presets ?? {}).find(([, p]) => p === v)?.[0];
    if (preset) return friendly(preset);
  }
  if (typeof v === "boolean") return v ? "On" : "Off";
  if (obj && !schema?.["x-ui"]?.enum_labels) {
    // One named thing ({"favourite": "BBC Radio 3"}): just its name.
    const vals = Object.values(obj);
    if (vals.length === 1 && typeof vals[0] === "string" && !("live" in obj)) return vals[0];
  }
  const w = inWords(schema, v);
  return w === CANT_SHOW ? "Set" : w;
}

/** Words that read on from a name ("lowered", "follows the show", "spinning") rather than naming a state. */
const PREDICATE = /^[A-Za-z]{3,}(?:ed|ing|[^s]s)\b/;

/** "Blinds lowered", "Hue follows the show", "Screens: Live". */
function phrase(name: string, words: string): string {
  return PREDICATE.test(words) ? `${name} ${lowerFirst(words)}` : `${name}: ${words}`;
}

/** "lowered" → "lowering", "follows" → "following", "spins" → "spinning". */
function ing(word: string): string {
  const w = word.toLowerCase();
  if (w.endsWith("ing")) return w;
  if (w.endsWith("ed")) return `${w.slice(0, -2)}ing`;
  let stem = /(ss|x|z|ch|sh|o)es$/.test(w) ? w.slice(0, -2) : w.slice(0, -1);
  if (stem.endsWith("e") && !stem.endsWith("ee")) stem = stem.slice(0, -1);
  else if (/^[^aeiou]+[aeiou][^aeiouwxy]$/.test(stem)) stem += stem.slice(-1);
  return `${stem}ing`;
}

/** The same change while it happens: "glitterball starting" (x-ui.switch), "blinds lowering", "screens to live". */
function doingPhrase(name: string, words: string, schema: Schema | undefined, v: unknown): string {
  const n = lowerFirst(name);
  const sw = schema?.["x-ui"]?.switch;
  if (sw && same(v, sw.on)) return `${n} starting`;
  if (sw && same(v, sw.off)) return `${n} stopping`;
  if (PREDICATE.test(words)) {
    const [first, ...rest] = words.split(" ");
    return [n, ing(first), ...rest].join(" ");
  }
  return `${n} to ${lowerFirst(words)}`;
}

export interface ModeChange {
  /** "Blinds lowered", "Screens: Live / Art". */
  text: string;
  /** While a switch runs: "blinds lowering", "glitterball starting". */
  doing: string;
  /** Its devices aren't connected yet (a placeholder, unreachable, or a dry run). */
  offline: boolean;
  /** A device left at rest (its x-ui.switch "off" value, e.g. the glitterball stopped). */
  resting: boolean;
  /** Each target it covers, and the value the mode sets it to. */
  sets: Record<string, unknown>;
}

/** What the mode sets in the room, one item per kind of device (or per device when they differ in kind): devices that
 *  work first, then devices left at rest, then things not connected yet. A kind's devices with different values are listed
 *  together, commonest first ("Screens: Live / Art"). */
export function modeChangeList(describe: RoomDescribe | null | undefined, mode: string | null | undefined, opts: WordsOpts = {}): ModeChange[] {
  const d = describe as Describe | null | undefined;
  const entries = mode ? (d?.modes[mode]?.entries as Record<string, Resolved> | undefined) : undefined;
  if (!d || !entries) return [];
  const bySub = new Map<string, Map<string, { words: string; targets: string[]; value: unknown }>>();
  for (const [t, e] of Object.entries(entries)) {
    if (!isSet(e)) continue;
    const sub = subOf(d, t);
    const schema = schemaOf(d, t);
    if (notADevice(d, sub, t) || schema?.["x-ui"]?.widget === "readonly") continue;
    const words = valueWords(schema, e.value);
    const g = bySub.get(sub) ?? new Map<string, { words: string; targets: string[]; value: unknown }>();
    const w = g.get(words) ?? { words, targets: [], value: e.value };
    w.targets.push(t);
    g.set(words, w);
    bySub.set(sub, g);
  }
  const out: ModeChange[] = [];
  for (const [sub, g] of bySub) {
    const groups = [...g.values()].sort((a, b) => b.targets.length - a.targets.length);
    const all = groups.flatMap((x) => x.targets);
    const base = { offline: offline(d, sub, opts.live) };
    if (groups.length === 1 || groups.every((x) => x.targets.length === 1)) {
      for (const x of groups) {
        const name = x.targets.length === 1 ? targetName(d, x.targets[0], opts.names) : kindName(sub, x.targets.length);
        const off = (t: string) => {
          const sw = schemaOf(d, t)?.["x-ui"]?.switch;
          return !!sw && same(x.value, sw.off);
        };
        out.push({
          ...base,
          resting: x.targets.every(off),
          text: phrase(name, x.words),
          doing: doingPhrase(name, x.words, schemaOf(d, x.targets[0]), x.value),
          sets: Object.fromEntries(x.targets.map((t) => [t, x.value])),
        });
      }
    } else {
      const name = kindName(sub, all.length);
      out.push({
        ...base,
        resting: false,
        text: `${name}: ${groups.map((x) => x.words).join(" / ")}`,
        doing: `${lowerFirst(name)} changing`,
        sets: Object.fromEntries(groups.flatMap((x) => x.targets.map((t) => [t, x.value] as const))),
      });
    }
  }
  const rank = (c: ModeChange) => (c.offline ? 2 : c.resting ? 1 : 0);
  return out
    .map((c, i) => [c, i] as const)
    .sort((a, b) => rank(a[0]) - rank(b[0]) || a[1] - b[1])
    .map(([c]) => c);
}

/** One line: what the mode changes in the room ("Glitterball spinning · Hue follows the show · Blinds lowered (the
 *  room can't switch it yet)"). Empty when describe hasn't loaded or the mode changes nothing in the room. */
export function modeChanges(describe: RoomDescribe | null | undefined, mode: string, opts: WordsOpts = {}): string {
  return modeChangeList(describe, mode, opts)
    .map((c) => (c.offline ? `${c.text} (the room can't switch it yet)` : c.text))
    .join(" · ");
}

/** "Live (modes:piano)" → "Live": a value's bracketed id is for the editor, not the mode row. */
function plainWords(w: string): string {
  return w.replace(/\s*\([^()]*:[^()]*\)/g, "").trim() || w;
}

/** One kind's change for the mode row's line: several devices as "<value> on <which>" ("Art on all 3 screens",
 *  "Live on North and South, Art on Dev test"), not "Screens: Live / Art"; one device or a phrase as it reads. */
function lineText(d: Describe, c: ModeChange, opts: WordsOpts): string {
  const all = Object.keys(c.sets);
  if (all.length < 2 || !c.text.includes(": ")) return plainWords(c.text);
  const sub = subOf(d, all[0]);
  const kind = lowerFirst(d.subsystems?.[sub]?.label || kindName(sub, all.length));
  const byWords = new Map<string, string[]>();
  for (const [t, v] of Object.entries(c.sets)) {
    const w = plainWords(valueWords(schemaOf(d, t), v));
    byWords.set(w, [...(byWords.get(w) ?? []), t]);
  }
  return [...byWords.entries()]
    .sort((a, b) => b[1].length - a[1].length)
    .map(([w, ts]) => {
      const which =
        ts.length === all.length
          ? all.length === 2
            ? `both ${kind}`
            : `all ${all.length} ${kind}`
          : ts.length <= 3
            ? ts.map((t) => targetName(d, t, opts.names)).join(" and ")
            : `${ts.length} ${kind}`;
      return `${w} on ${which}`;
    })
    .join(", ");
}

/** The current mode in one line under the mode row: what it changes in the room, only the devices that work (what
 *  isn't connected yet is the editor's business). Empty when describe hasn't loaded or it changes nothing. */
export function modeLine(describe: RoomDescribe | null | undefined, mode: string | null | undefined, opts: WordsOpts = {}): string {
  const d = describe as Describe | null | undefined;
  if (!d) return "";
  return modeChangeList(d, mode, opts)
    .filter((c) => !c.offline)
    .map((c) => lineText(d, c, opts))
    .join(" · ");
}

const STEP_DONE = new Set(["ok", "failed", "skipped", "unconfirmed", "cancelled"]);

/** What a switch to `mode` is still doing to the room, for the progress line: "glitterball starting, blinds lowering".
 *  Once the steps are known, the targets with an unfinished step; before that, the ones not yet at the mode's value.
 *  Things not connected yet, and the targets Auto leaves alone (`skip`), are left out. */
export function switchingWords(
  describe: RoomDescribe | null | undefined,
  mode: string | null | undefined,
  opts: WordsOpts & { steps?: TransitionStep[]; skip?: string[] | null },
  max = 3,
): string {
  const d = describe as Describe | null | undefined;
  if (!d) return "";
  const steps = opts.steps ?? [];
  const stepOf = new Map(steps.map((s) => [s.subsystem, s.status]));
  const skip = new Set(opts.skip ?? []);
  const pending = (t: string, v: unknown) => {
    if (skip.has(t)) return false;
    if (steps.length) return stepOf.has(t) && !STEP_DONE.has(String(stepOf.get(t)));
    const now = opts.live?.[subOf(d, t)]?.members?.[memberOf(d, t)]?.believed;
    return now === undefined || now === null || !same(now, v);
  };
  return modeChangeList(d, mode, opts)
    .filter((c) => !c.offline && Object.entries(c.sets).some(([t, v]) => pending(t, v)))
    .slice(0, max)
    .map((c) => c.doing)
    .join(", ");
}

/** "the TVs and the DMX power" */
function theList(names: string[]): string {
  const xs = names.map((n) => `the ${lowerFirst(n)}`);
  return xs.length > 1 ? `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}` : (xs[0] ?? "");
}

/** The menu's sentences about devices the room can't switch: "The room can't switch the TVs and the DMX power yet, so
 *  modes leave them alone." (placeholders), "The room can't reach the blinds right now." (unreachable), and one for
 *  dry runs (set, but nothing moves). */
export function notConnectedWords(describe: RoomDescribe | null | undefined, live?: Live): string[] {
  const d = describe as Describe | null | undefined;
  const subs: [string, SubInfo][] = d?.subsystems
    ? Object.entries(d.subsystems)
    : Object.entries(live ?? {}).map(([s, i]): [string, SubInfo] => [
        s,
        { available: i.available, dry_run: i.dry_run, members: Object.keys(i.members ?? {}) },
      ]);
  const skipped: { name: string; n: number }[] = [];
  const unreachable: { name: string; n: number }[] = [];
  const dry: { name: string; n: number }[] = [];
  for (const [s, i] of subs) {
    if (d ? notADevice(d, s) : false) continue;
    const l = live?.[s];
    const members = i.members ?? Object.keys(l?.members ?? {});
    const name = members.length === 1 && d ? targetName(d, `${s}.${members[0]}`) : kindName(s, members.length);
    if (i.placeholder) skipped.push({ name, n: members.length });
    else if ((l?.available ?? i.available) === false) unreachable.push({ name, n: members.length });
    else if (l?.dry_run ?? i.dry_run) dry.push({ name, n: members.length });
  }
  const out: string[] = [];
  const many = (xs: { n: number }[]) => xs.length > 1 || xs[0].n > 1;
  if (skipped.length) {
    const p = many(skipped);
    out.push(`The room can't switch ${theList(skipped.map((x) => x.name))} yet, so modes leave ${p ? "them" : "it"} alone.`);
  }
  if (unreachable.length) out.push(`The room can't reach ${theList(unreachable.map((x) => x.name))} right now.`);
  if (dry.length) {
    const p = many(dry);
    out.push(`${cap(theList(dry.map((x) => x.name)))} ${p ? "aren't" : "isn't"} connected for real yet: modes set ${p ? "them" : "it"}, but nothing moves.`);
  }
  return out;
}

/** A mode-change step in words, from describe: "Glitterball spinning", "Drawing room volume back to 61", and as a
 *  failure, "Glitterball couldn't change to spinning". Null for a step that isn't a target's value (a signal). */
export function stepSentence(
  describe: RoomDescribe | null | undefined,
  step: TransitionStep,
  names?: Record<string, string>,
): { text: string; failure: string } | null {
  const d = describe as Describe | null | undefined;
  const s = step as TransitionStep & { value?: unknown; kind?: string };
  if (!d || !d.targets[s.subsystem] || !("value" in s) || (s.kind && s.kind !== "set" && s.kind !== "retrieve")) return null;
  const name = targetName(d, s.subsystem, names);
  const words = valueWords(schemaOf(d, s.subsystem), s.value);
  return s.kind === "retrieve"
    ? { text: `${name} back to ${lowerFirst(words)}`, failure: `${name} couldn't be put back` }
    : { text: phrase(name, words), failure: `${name} couldn't change to ${lowerFirst(words)}` };
}
