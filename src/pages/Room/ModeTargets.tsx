// Every device the room has, and what one mode does with each: the mode editor's "In this mode" list. The owner asked
// on 15 Oct to see each fixture and choose whether it is part of a mode, so this is one switch per device. Rows follow
// the site's rooms (status.rooms: the first open, the rest folded; anything in no room last), and within a room its
// kinds of device in a fixed order (screens, TVs, blinds, lamps, music, the room's effects such as the glitterball with
// its settings nested), then the settings that aren't devices, folded under "Behind the scenes".
//
// Modes are flat (owner, 16 Oct): a mode lists everything it sets. On = this mode sets it. Off = not changed by this
// mode: no entry, or null over a broader key that would name it (model.py merge rule 4). "Put back afterwards" is
// {"set": v, "restore": true}: when the room leaves the mode the value from before comes back, unless the next mode
// sets it. Legacy put-back rows ({"store": slot, "set": v} here, {"retrieve": slot} in another mode) are drawn with
// put-back on and are never undone from here while another mode relies on them, so a store/retrieve pairing can't break.
//
// Edits are per target: a target's own key overrides a broader one. Afterwards redundant keys go, per-target keys fold
// back into a key the saved mode used (else the room's group, else the kind's own key) when they end up equal, and a
// result that means the same as the saved mode becomes the saved mode exactly, so toggling back reads "No changes".

import { useRef, useState, type ReactNode } from "react";
import { ChevronDown } from "lucide-react";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import type { RawMode, RoomDescribe, RoomStatus, SiteRoom } from "./types";

/** aa's row options schema: each target has its own, whose store_if choices carry their words (enum_labels). */
type EntrySchema = { properties?: Record<string, { "x-ui"?: { label?: string; enum_labels?: string[] } }> };
type Target = RoomDescribe["targets"][string] & { subsystem?: string; member?: string; groups?: string[]; entry_schema?: EntrySchema };
type Describe = RoomDescribe & {
  targets: Record<string, Target>;
  subsystems?: Record<string, { placeholder?: boolean; dry_run?: boolean; label?: string; category?: string; order?: number }>;
  /** Subsystems in the room's own order (rooms.json lists in file order, then the zone; settings last). */
  subsystem_order?: string[];
  groups_from_rooms?: string[];
  /** aa's row options schema, when served (the room-wide one; a target's own has its store_if words). */
  entry_schema?: EntrySchema;
};
/** One target's entry in a mode, resolved (describe.modes[m].entries[t]). */
type Resolved = { verb?: string; slot?: string | null };
type SchemaLike = {
  default?: unknown;
  enum?: unknown[];
  type?: string | string[];
  minimum?: number;
  maximum?: number;
  readOnly?: boolean;
  "x-ui"?: { label?: string; labels?: Record<string, string>; widget?: string };
};
type State = Record<string, unknown>;
type Entry = Record<string, unknown>;
type Op = (state: State, t: string, touched: Set<string>) => void;

const ENTRY_KEYS = new Set(["set", "store", "retrieve", "restore", "store_if", "skip_if", "policy", "opts", "else", "max_age_s"]);
/** Row options that only mean something while put-back is on (model.py: store_if and max_age_s need restore). */
const PUT_BACK_ONLY = ["store_if", "max_age_s"];
/** Settings rather than devices (describe's category "setting" counts too): folded under "Behind the scenes". */
const BEHIND: Record<string, string> = { page: "Page refreshes", sensor_mask: "Art brightness" };
/** "Only if it was changed within" (max_age_s). */
const AGES: [number | undefined, string][] = [
  [3600, "1 h"],
  [10800, "3 h"],
  [43200, "12 h"],
  [undefined, "Any time"],
];
const OFF = "Not changed by this mode";
const PUT_BACK = "↩ Put back afterwards";
const RELIED_ON = "Stays on for now: another mode relies on it.";
const CANT = "This page can't set it yet.";

function friendly(s: string): string {
  const t = s.replace(/[-_:.]+/g, " ").trim();
  return t.length <= 2 ? t.toUpperCase() : t.charAt(0).toUpperCase() + t.slice(1);
}

/** "Blinds" -> "blinds" for a sentence; "TVs" stays. */
function lower(s: string): string {
  return /^[A-Z][a-z]/.test(s) ? s.charAt(0).toLowerCase() + s.slice(1) : s;
}

function upper(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function clone<T>(v: T): T {
  return v === undefined ? v : (JSON.parse(JSON.stringify(v)) as T);
}

/** A value as JSON with keys in order, so equal values compare equal however they were written. */
function jsonKey(v: unknown): string {
  return (
    JSON.stringify(v, (_, x) =>
      x && typeof x === "object" && !Array.isArray(x)
        ? Object.fromEntries(Object.entries(x as Record<string, unknown>).sort(([a], [b]) => a.localeCompare(b)))
        : x,
    ) ?? "undefined"
  );
}

function ageWords(s: number): string {
  if (s % 3600 === 0) return `${s / 3600} h`;
  return s % 60 === 0 ? `${s / 60} min` : `${s} s`;
}

/** An entry object ({"set": …, "restore": true}, …): its keys. A bare value (art, {"live": …}) → null. */
function entryKeys(v: unknown): string[] | null {
  if (!v || typeof v !== "object" || Array.isArray(v)) return null;
  const keys = Object.keys(v);
  return keys.length && keys.every((k) => ENTRY_KEYS.has(k)) ? keys : null;
}

/** An entry as an object ({"set": v} for a bare value); null for none. */
function entryOf(raw: unknown): Entry | null {
  if (raw === undefined || raw === null) return null;
  return entryKeys(raw) ? (raw as Entry) : { set: raw };
}

/** Do two raw entries mean the same (a bare value and {"set": v} do; none and null do)? */
function sameEntry(a: unknown, b: unknown): boolean {
  return jsonKey(entryOf(a)) === jsonKey(entryOf(b));
}

/** An entry with only "set" goes back to its bare value. */
function collapse(e: Entry): unknown {
  const keys = Object.keys(e);
  return keys.length === 1 && keys[0] === "set" ? e.set : e;
}

function globRe(sel: string): RegExp {
  return new RegExp(`^${sel.split("*").map((s) => s.replace(/[.+?^${}()|[\]\\]/g, "\\$&")).join(".*")}$`);
}

/** How specifically a modes.json key names a target (model.py): 3 the target, 2 a group, 1 a prefix or wildcard, 0 not. */
function specificity(sel: string, t: string, groups: Record<string, string[]>): number {
  if (sel === t) return 3;
  if (groups[sel]) return groups[sel].includes(t) ? 2 : 0;
  if (sel.includes("*")) return globRe(sel).test(t) ? 1 : 0;
  return t.startsWith(`${sel}.`) ? 1 : 0;
}

/** The key in a mode's state that decides a target (the most specific), if any; `skip` is left out. */
function ownKey(state: State, t: string, groups: Record<string, string[]>, skip?: string): string | undefined {
  let best: string | undefined;
  let score = 0;
  for (const k of Object.keys(state)) {
    if (k === skip || k.startsWith("_")) continue;
    const s = specificity(k, t, groups);
    if (s > score) [best, score] = [k, s];
  }
  return best;
}

/** What a state says about a target: its deciding key's entry (null: not this mode), undefined when no key names it. */
function rawOf(state: State, t: string, groups: Record<string, string[]>): unknown {
  const k = ownKey(state, t, groups);
  return k === undefined ? undefined : state[k];
}

/** The targets a modes.json key names (for mapping a saved mode's problems to its rows). */
export function targetsOf(describe: RoomDescribe, sel: string): string[] {
  const groups = describe.groups ?? {};
  return Object.keys(describe.targets).filter((t) => specificity(sel, t, groups) > 0);
}

/** A kind of device in words ("Blinds", "Lamps"), from describe when it has words, else the page's own. */
export function kindWords(describe: RoomDescribe, sub: string): string {
  return (describe as Describe).subsystems?.[sub]?.label || BEHIND[sub] || friendly(sub);
}

/** The targets a mode's state sets (switched on in this list), by the same merge rule. */
export function setTargets(describe: RoomDescribe, state: Record<string, unknown> | undefined): string[] {
  const groups = describe.groups ?? {};
  return Object.keys(describe.targets).filter((t) => viewOf(rawOf(state ?? {}, t, groups)).on);
}

interface View {
  on: boolean;
  /** What it is set to (on only). */
  value?: unknown;
  /** A legacy row with no value: {"retrieve": slot} (puts back what another mode stored) or a store only. */
  fixed?: "retrieve" | "store";
  /** A legacy {"store": slot, "set": v}: another mode's retrieve may put it back. */
  store?: string;
  /** Put back afterwards (restore, or a legacy store). */
  restores: boolean;
  /** Kept there: hand changes are undone (policy enforce). */
  keeps: boolean;
  policy?: string;
  maxAge?: number;
  storeIf: boolean;
}

function viewOf(raw: unknown): View {
  const none: View = { on: false, restores: false, keeps: false, storeIf: false };
  if (raw === undefined || raw === null) return none;
  if (!entryKeys(raw)) return { ...none, on: true, value: raw };
  const o = raw as Entry;
  const opts = {
    keeps: o.policy === "enforce",
    policy: typeof o.policy === "string" ? o.policy : undefined,
    storeIf: Array.isArray(o.store_if) && o.store_if.length > 0,
  };
  const store = typeof o.store === "string" ? o.store : undefined;
  if ("retrieve" in o) return { ...none, ...opts, on: true, fixed: "retrieve", restores: true };
  if (!("set" in o)) return store ? { ...none, ...opts, on: true, fixed: "store", store, restores: true } : none;
  return {
    ...opts,
    on: true,
    value: o.set,
    store,
    restores: o.restore === true || !!store,
    maxAge: typeof o.max_age_s === "number" ? o.max_age_s : undefined,
  };
}

interface Ctx {
  groups: Record<string, string[]>;
  all: string[];
  /** Keys to fold per-target keys back into when they end up equal, most preferred first. */
  folds: (t: string) => string[];
}

/** Give target t its own raw entry; undefined = not changed by this mode (null when a broader key would name it). */
function put(state: State, t: string, raw: unknown, ctx: Ctx, touched: Set<string>): void {
  touched.add(t);
  delete state[t];
  if (raw !== undefined) state[t] = raw;
  else if (ownKey(state, t, ctx.groups) !== undefined) state[t] = null;
}

/** A key that changes nothing: every target it decides would get the same from the next key without it. */
function redundant(state: State, k: string, ctx: Ctx): boolean {
  return ctx.all
    .filter((t) => ownKey(state, t, ctx.groups) === k)
    .every((t) => {
      const next = ownKey(state, t, ctx.groups, k);
      return sameEntry(state[k], next === undefined ? undefined : state[next]);
    });
}

/** Per-target keys back into one key, when every target it covers has its own equal entry and the key would win. */
function fold(state: State, key: string, ctx: Ctx): void {
  const ts = ctx.all.filter((t) => specificity(key, t, ctx.groups) > 0);
  if (ts.length < 2 || ts.includes(key) || !ts.every((t) => t in state)) return;
  if (!ts.every((t) => sameEntry(state[t], state[ts[0]]))) return;
  const rivals = Object.keys(state).filter((k) => k !== key && !k.startsWith("_") && !ts.includes(k));
  if (rivals.some((k) => ts.some((t) => specificity(k, t, ctx.groups) >= specificity(key, t, ctx.groups)))) return;
  state[key] = state[ts[0]];
  for (const t of ts) delete state[t];
}

/** Every target's entry as the mode means it, for comparing two states. */
function meaning(state: State, ctx: Ctx): string {
  return jsonKey(ctx.all.map((t) => entryOf(rawOf(state, t, ctx.groups))));
}

/** After edits to `touched`: drop redundant keys, fold equal per-target keys, and become the saved state if equal. */
function tidy(state: State, touched: Set<string>, ctx: Ctx, saved: State): State {
  for (const t of touched) if (t in state && redundant(state, t, ctx)) delete state[t];
  const near = [...new Set([...touched].flatMap((t) => ctx.folds(t)))];
  for (const k of near) fold(state, k, ctx);
  const covering = Object.keys(state).filter((k) => [...touched].some((t) => specificity(k, t, ctx.groups) > 0));
  for (const k of new Set([...near, ...covering])) if (k in state && redundant(state, k, ctx)) delete state[k];
  return meaning(state, ctx) === meaning(saved, ctx) ? clone(saved) : state;
}

/** One line of the list: one target, or several drawn as one ("Both screens"). */
interface RowSpec {
  key: string;
  ts: string[];
  label: string;
  nested?: boolean;
  /** Drawn as one: the part's key, for "Set each screen separately". */
  joined?: string;
}

/** A kind of device in a room (or, behind the scenes, one kind of setting). */
interface Part {
  key: string;
  sub: string;
  label: string;
  rows: RowSpec[];
  ts: string[];
}

export function ModeTargets({
  name,
  draft,
  baseMode,
  update,
  describe: describeIn,
  status,
  names,
  issues,
  renderValue,
  words,
  wholeMode,
}: {
  /** The mode being edited. */
  name: string;
  draft: RawMode;
  /** The mode as saved: a toggle back to it writes the saved entry exactly. */
  baseMode: RawMode | undefined;
  update: (fn: (d: RawMode) => void) => void;
  describe: RoomDescribe;
  /** The room's status: its rooms, and what each device is doing now (a row switched on starts there). */
  status?: RoomStatus | null;
  /** Display names that beat a target's raw label (the screens' names from status), by target. */
  names?: Record<string, string>;
  /** Problems the room found in the draft, in words, by target (shown in amber under the row). */
  issues?: Record<string, string[]>;
  /** The control for one target's value, or null when the page can't edit it (then it's shown in words). */
  renderValue: (t: string, value: unknown, onChange: (v: unknown) => void) => ReactNode | null;
  /** A target's value in words. */
  words: (t: string, value: unknown) => string;
  /** Drawn last in "For the whole mode" (the editor's "Starts by itself"). */
  wholeMode?: ReactNode;
}) {
  const describe = describeIn as Describe;
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [split, setSplit] = useState<Record<string, boolean>>({});
  const [more, setMore] = useState<Record<string, boolean>>({});
  const [notice, setNotice] = useState<Record<string, string>>({});
  // Put-back options parked while put-back is off (they mean nothing without it), so switching it on brings them back.
  const parked = useRef<Record<string, Entry>>({});
  const groups = describe.groups ?? {};
  const all = Object.keys(describe.targets);
  const savedState = baseMode?.state ?? {};
  const schemaOf = (t: string) => describe.targets[t]?.schema as SchemaLike | undefined;
  const subOf = (t: string) => describe.targets[t]?.subsystem ?? t.split(".")[0];
  const memberOf = (t: string) => describe.targets[t]?.member ?? t.slice(subOf(t).length + 1);
  const is = (sc: SchemaLike | undefined, ty: string) => (Array.isArray(sc?.type) ? sc.type.includes(ty) : sc?.type === ty);
  const readOnly = (t: string) => schemaOf(t)?.["x-ui"]?.widget === "readonly" || schemaOf(t)?.readOnly === true;
  const behind = (sub: string) => sub in BEHIND || describe.subsystems?.[sub]?.category === "setting";

  const ctx: Ctx = {
    groups,
    all,
    folds: (t) => [
      ...Object.keys(savedState).filter((k) => !k.startsWith("_") && k !== t && specificity(k, t, groups) > 0),
      ...Object.keys(groups).filter((g) => g.includes(":") && groups[g].includes(t)),
      subOf(t),
    ],
  };

  /** What the device is doing now (status), when it is a value this target takes. */
  const nowValue = (t: string): unknown => {
    const b = status?.subsystems?.[subOf(t)]?.members?.[memberOf(t)]?.believed;
    if (b === undefined || b === null) return undefined;
    const sc = schemaOf(t);
    if (Array.isArray(sc?.enum)) return sc.enum.find((x) => jsonKey(x) === jsonKey(b));
    if (is(sc, "number") || is(sc, "integer")) {
      const ok = typeof b === "number" && (sc?.minimum === undefined || b >= sc.minimum) && (sc?.maximum === undefined || b <= sc.maximum);
      return ok ? b : undefined;
    }
    if (is(sc, "boolean")) return typeof b === "boolean" ? b : undefined;
    if (is(sc, "string")) return typeof b === "string" ? b : undefined;
    return b;
  };

  /** Where a row switched on starts: what the device is doing now, else the schema's default. Never another mode's. */
  const startValue = (t: string): unknown => {
    const sc = schemaOf(t);
    const now = nowValue(t);
    if (sc?.["x-ui"]?.widget === "screen") return now === undefined || now === "art" ? "art" : { live: { auto: true } };
    if (now !== undefined) return clone(now);
    if (sc?.default !== undefined) return clone(sc.default);
    const named = sc?.enum?.find((x) => x !== null) ?? Object.keys(sc?.["x-ui"]?.labels ?? {})[0];
    if (named !== undefined) return clone(named);
    if (is(sc, "boolean")) return false;
    if (is(sc, "object")) return {};
    if (is(sc, "array")) return [];
    return typeof sc?.minimum === "number" ? sc.minimum : undefined;
  };

  const saved = (t: string) => rawOf(savedState, t, groups);
  /** A new entry that means the same as the saved one becomes the saved one exactly (its options and form). */
  const settle = (t: string, raw: unknown) => {
    const b = saved(t);
    return b !== undefined && b !== null && sameEntry(raw, b) ? clone(b) : raw;
  };
  /** Does another mode retrieve this legacy store (so dropping it would break the pairing)? */
  const relied = (t: string, slot: string | undefined) =>
    !!slot &&
    Object.entries(describe.modes).some(([m, md]) => {
      const e = md.entries?.[t] as Resolved | undefined;
      return m !== name && e?.verb === "retrieve" && e.slot === slot;
    });

  const ops = {
    on: ((s, t, touched) => {
      const b = saved(t);
      put(s, t, viewOf(b).on ? clone(b) : startValue(t), ctx, touched);
    }) as Op,
    off: ((s, t, touched) => put(s, t, undefined, ctx, touched)) as Op,
    value:
      (x: unknown): Op =>
      (s, t, touched) => {
        const raw = rawOf(s, t, groups);
        const e = entryKeys(raw) && "set" in (raw as Entry) ? { ...clone(raw as Entry), set: clone(x) } : clone(x);
        put(s, t, settle(t, e), ctx, touched);
      },
    restore:
      (on: boolean): Op =>
      (s, t, touched) => {
        const raw = rawOf(s, t, groups);
        const v = viewOf(raw);
        if (!v.on || v.fixed || v.store) return;           // a legacy store keeps its store: another mode may need it
        const e = clone(entryOf(raw) as Entry);
        const keep: Entry = {};
        for (const k of PUT_BACK_ONLY) if (k in e) keep[k] = e[k];
        const rest = Object.fromEntries(Object.entries(e).filter(([k]) => k !== "restore" && !PUT_BACK_ONLY.includes(k)));
        if (on) {
          const b = entryOf(saved(t));
          const back = parked.current[t] ?? (b?.restore ? Object.fromEntries(PUT_BACK_ONLY.filter((k) => k in b).map((k) => [k, b[k]])) : {});
          put(s, t, settle(t, { ...rest, restore: true, ...clone(back), ...keep }), ctx, touched);
        } else {
          if (Object.keys(keep).length) parked.current[t] = keep;
          put(s, t, settle(t, collapse(rest)), ctx, touched);
        }
      },
    keep:
      (on: boolean): Op =>
      (s, t, touched) => {
        const raw = rawOf(s, t, groups);
        if (!viewOf(raw).on || viewOf(raw).fixed) return;
        const e = clone(entryOf(raw) as Entry);
        const before = viewOf(saved(t)).policy;
        if (on) e.policy = "enforce";
        else if (before && before !== "enforce") e.policy = before;   // e.g. music's own "ignore"
        else delete e.policy;
        put(s, t, settle(t, collapse(e)), ctx, touched);
      },
    age:
      (secs: number | undefined): Op =>
      (s, t, touched) => {
        const raw = rawOf(s, t, groups);
        const v = viewOf(raw);
        if (!v.restores || v.fixed || v.store) return;
        const e = clone(entryOf(raw) as Entry);
        if (secs === undefined) delete e.max_age_s;
        else e.max_age_s = secs;
        delete parked.current[t];
        put(s, t, settle(t, e), ctx, touched);
      },
  };

  const edit = (ts: string[], fn: Op) => {
    if (!ts.length) return;
    update((d) => {
      const s = (d.state = d.state ?? {});
      const touched = new Set<string>();
      for (const t of ts) fn(s, t, touched);
      d.state = tidy(s, touched, ctx, savedState);
      if (!Object.keys(d.state).length) delete d.state;
    });
  };

  const state = draft.state ?? {};
  const view = (t: string): View => viewOf(rawOf(state, t, groups));
  const say = (key: string, text: string) => setNotice((n) => ({ ...n, [key]: text }));

  const nameOf = (t: string): string => {
    if (names?.[t]) return names[t];
    const d = describe.targets[t];
    const member = memberOf(t);
    if (d?.label && d.label !== member && d.label !== t) return d.label;
    const screen = subOf(t) !== "screens" ? names?.[`screens.${member.split(".")[0]}`] : undefined;
    if (screen) return screen;                              // a screen's TV or page goes by the screen's name
    const ui = schemaOf(t)?.["x-ui"]?.label;
    return friendly(ui && ui !== member ? ui : member);
  };

  // ── Rooms and their parts ──
  const shown = all.filter((t) => !readOnly(t));
  const rooms: SiteRoom[] =
    status?.rooms ??
    [...new Set(Object.keys(groups).filter((g) => g.includes(":")).map((g) => g.split(":")[0]))].map((id) => ({
      id,
      name: friendly(id),
      screens: [],
      lights: [],
      blinds: [],
      sensors: [],
    }));
  const roomOf = (t: string): string => {
    for (const g of Object.keys(groups)) {
      const id = g.split(":")[0];
      if (g.includes(":") && groups[g].includes(t) && rooms.some((r) => r.id === id)) return id;
    }
    const seg = memberOf(t).split(".")[0];
    for (const r of rooms) {
      const ids = [...r.screens, ...r.lights, ...r.blinds, ...r.sensors, ...(r.effects ?? []).flatMap((e) => [e.power, ...e.settings])];
      if (ids.includes(t) || ids.includes(seg) || seg === r.id) return r.id;
    }
    return "";
  };
  const effectOf = (t: string) => rooms.flatMap((r) => r.effects ?? []).find((e) => e.power === t || e.settings.includes(t));
  // The room's own order (describe.subsystem_order follows rooms.json; settings last): no names in code.
  const order = (describe as Describe).subsystem_order ?? [];
  const rank = (sub: string) => {
    const real = sub.startsWith("effect:") ? subOf(sub.slice(7)) : sub;
    const i = order.indexOf(real);
    return behind(real) ? 1000 + (i >= 0 ? i : 0) : i >= 0 ? i : 500;
  };

  const partsOf = (ts: string[], prefix: string): Part[] => {
    const bySub = new Map<string, string[]>();
    for (const t of ts) {
      const eff = effectOf(t);
      const k = eff ? `effect:${eff.power}` : subOf(t);
      bySub.set(k, [...(bySub.get(k) ?? []), t]);
    }
    return [...bySub.entries()]
      .sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b))
      .map(([k, list]) => {
        const key = `${prefix}/${k}`;
        if (k.startsWith("effect:")) {
          const power = k.slice("effect:".length);
          const label = list.includes(power) ? nameOf(power) : kindWords(describe, subOf(list[0]));
          const ordered = [...list.filter((t) => t === power), ...list.filter((t) => t !== power)];
          const rows = ordered.map((t) => {
            const n = nameOf(t);
            const short = t !== power && n.toLowerCase().startsWith(`${label.toLowerCase()} `) ? upper(n.slice(label.length + 1)) : n;
            return { key: t, ts: [t], label: short, nested: t !== power && list.includes(power) };
          });
          return { key, sub: subOf(power), label, rows, ts: ordered };
        }
        const label = kindWords(describe, k);
        let rows: RowSpec[] = list.map((t) => ({ key: t, ts: [t], label: nameOf(t) }));
        const joinable =
          list.length >= 2 &&
          list.every((t) => schemaOf(t)?.["x-ui"]?.widget === "screen") &&
          list.every((t) => sameEntry(rawOf(state, t, groups), rawOf(state, list[0], groups)));
        if (joinable && !split[key]) {
          rows = [{ key: `${key}/joined`, ts: list, label: list.length === 2 ? `Both ${lower(label)}` : `All ${list.length} ${lower(label)}`, joined: key }];
        }
        return { key, sub: k, label, rows, ts: list };
      });
  };

  // Settings that belong to no room but to the whole mode (the feel, the mic, Auto: describe category "setting", in no
  // room's lists) come first and open, not under "Other": they're what the mode is about. Devices in no room stay in Other.
  const wide = (t: string) =>
    roomOf(t) === "" && (describe.targets[t] as { category?: string } | undefined)?.category === "setting" && !(subOf(t) in BEHIND);
  const sections = [
    { id: "~mode", name: "For the whole mode", ts: shown.filter(wide), flat: true },
    ...rooms.map((r) => ({ id: r.id, name: r.name, ts: shown.filter((t) => roomOf(t) === r.id), flat: false })),
    { id: "", name: rooms.length ? "Other" : "", ts: shown.filter((t) => roomOf(t) === "" && !wide(t)), flat: false },
  ].filter((r) => r.ts.length || (r.flat && wholeMode));

  const hasIssues = (ts: string[]) => ts.some((t) => issues?.[t]?.length);

  // ── Drawing ──
  const chip = (pressed: boolean) =>
    cn(
      "min-h-11 rounded-full border px-3 text-[15px] transition-colors",
      pressed ? "border-teal-300 bg-teal-50 font-semibold text-teal-900" : "border-stone-200 bg-white text-stone-700 hover:bg-stone-50",
    );

  const storeIfWords = (t: string) => {
    const labels = describe.targets[t]?.entry_schema?.properties?.store_if?.["x-ui"]?.enum_labels;
    return labels?.length === 1 ? labels[0] : "Only if it was in use";
  };

  const nowWords = (ts: string[]): string => {
    const ns = ts.map(nowValue);
    if (ns[0] === undefined || !ns.every((n) => jsonKey(n) === jsonKey(ns[0]))) return "";
    return words(ts[0], ns[0]);
  };

  const toggleRow = (key: string, ts: string[], on: boolean) => {
    say(key, "");
    if (on) {
      const off = ts.filter((t) => !view(t).on);
      const can = off.filter((t) => viewOf(saved(t)).on || startValue(t) !== undefined);
      if (can.length < off.length) say(key, CANT);
      edit(can, ops.on);
    } else {
      const lit = ts.filter((t) => view(t).on);
      const kept = lit.filter((t) => view(t).fixed === "retrieve" || relied(t, view(t).store));
      if (kept.length) say(key, RELIED_ON);
      edit(lit.filter((t) => !kept.includes(t)), ops.off);
    }
  };

  const togglePutBack = (key: string, ts: string[], on: boolean) => {
    say(key, "");
    const lit = ts.filter((t) => view(t).on && !view(t).fixed);
    const legacy = lit.filter((t) => view(t).store);
    if (!on && legacy.length) say(key, RELIED_ON);
    edit(lit.filter((t) => !legacy.includes(t)), ops.restore(on));
  };

  const row = (r: RowSpec) => {
    const { key, ts } = r;
    const v = view(ts[0]);
    const caption: string[] = [];
    if (!v.on) caption.push(OFF);
    else if (v.fixed === "retrieve") caption.push("Puts back what the last mode changed");
    else if (v.fixed === "store") caption.push("Remembers how it is, to put it back later");
    else {
      if (v.keeps) caption.push("kept there");
      if (v.restores && v.maxAge) caption.push(`only if it was changed within ${ageWords(v.maxAge)}`);
      if (v.storeIf) caption.push(lower(storeIfWords(ts[0])));
    }
    const now = nowWords(ts);
    if (now) caption.push(`now: ${now}`);
    const control = v.on && !v.fixed ? renderValue(ts[0], v.value, (x) => edit(ts, ops.value(x))) : null;
    const problems = [...new Set(ts.flatMap((t) => issues?.[t] ?? []))];
    const isMore = !!more[key];
    const ages = AGES.some(([s]) => s === v.maxAge) ? AGES : [...AGES.slice(0, -1), [v.maxAge, ageWords(v.maxAge as number)] as [number, string], AGES[AGES.length - 1]];
    return (
      <li key={key} className={cn("py-3", r.nested && "ml-1 border-l-2 border-stone-100 pl-3")}>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-[15px] font-medium leading-snug">{r.label}</p>
            {caption.length > 0 && <p className="text-sm text-muted-foreground">{upper(caption.join(" · "))}</p>}
          </div>
          <Switch
            className="mt-0.5"
            aria-label={`${r.label} in this mode`}
            checked={v.on}
            onCheckedChange={(on) => toggleRow(key, ts, on)}
          />
        </div>
        {problems.map((p) => (
          <p key={p} className="mt-1 text-sm text-amber-800">
            {p}
          </p>
        ))}
        {notice[key] && <p className="mt-1 text-sm text-amber-800">{notice[key]}</p>}
        {r.joined && (
          <button
            type="button"
            onClick={() => setSplit((x) => ({ ...x, [r.joined as string]: true }))}
            className="mt-1 min-h-9 text-sm text-teal-800 underline underline-offset-2"
          >
            Set each screen separately
          </button>
        )}
        {v.on && !v.fixed && (
          <>
            <div className="mt-2">{control ?? <p className="text-base text-stone-700">{words(ts[0], v.value)}</p>}</div>
            <div className="mt-2 flex flex-wrap gap-2">
              <button
                type="button"
                aria-pressed={v.restores}
                onClick={() => togglePutBack(key, ts, !v.restores)}
                className={chip(v.restores)}
              >
                {PUT_BACK}
              </button>
              <button
                type="button"
                aria-expanded={isMore}
                onClick={() => setMore((m) => ({ ...m, [key]: !isMore }))}
                className="min-h-11 rounded-full px-3 text-[15px] text-stone-600 hover:bg-stone-100"
              >
                ⋯ More
              </button>
            </div>
            {isMore && (
              <div className="mt-2 space-y-3 rounded-xl bg-stone-50 p-3">
                {v.restores && !v.store && (
                  <div>
                    <p className="text-sm text-stone-700">Only if it was changed within:</p>
                    <div className="mt-1 flex flex-wrap gap-2">
                      {ages.map(([s, w]) => (
                        <button
                          key={w}
                          type="button"
                          aria-pressed={v.maxAge === s}
                          onClick={() => edit(ts, ops.age(s))}
                          className={chip(v.maxAge === s)}
                        >
                          {w}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
                {v.restores && v.storeIf && <p className="text-sm text-stone-700">✓ {storeIfWords(ts[0])}</p>}
                <label className="flex items-center justify-between gap-3 text-sm text-stone-700">
                  <span>Keep it there: undo changes made by hand</span>
                  <Switch
                    aria-label={`${r.label}: keep it there`}
                    checked={v.keeps}
                    onCheckedChange={(on) => edit(ts, ops.keep(on))}
                  />
                </label>
              </div>
            )}
          </>
        )}
      </li>
    );
  };

  const noteOf = (p: Part): string | null => {
    const info = describe.subsystems?.[p.sub];
    if (info?.placeholder) return "The room can't switch it yet";
    return info?.dry_run ? `Not wired up yet: the ${lower(p.label)} won't move` : null;
  };

  const countWords = (on: number, n: number) => (on === n ? "All in this mode" : on ? `${on} of ${n} in this mode` : "None in this mode");

  const part = (p: Part) => {
    const note = noteOf(p);
    if (p.rows.length === 1) {                              // one device, or several drawn as one ("Both screens")
      return (
        <section key={p.key} className="rounded-2xl border border-stone-200 bg-card px-3">
          {note && <p className="pt-3 text-sm text-amber-800">{note}</p>}
          <ul>{p.rows.map(row)}</ul>
        </section>
      );
    }
    const on = p.ts.filter((t) => view(t).on);
    const lit = on.filter((t) => !view(t).fixed);
    const back = lit.filter((t) => view(t).restores);
    return (
      <section key={p.key} className="rounded-2xl border border-stone-200 bg-card">
        <div className="flex items-start justify-between gap-3 p-3">
          <div className="min-w-0">
            <p className="text-base font-semibold">{p.label}</p>
            <p className="text-sm text-muted-foreground">{[countWords(on.length, p.ts.length), note].filter(Boolean).join(" · ")}</p>
          </div>
          <label className="flex shrink-0 items-center gap-2 text-sm text-stone-600">
            All
            <Switch
              aria-label={`All ${lower(p.label)} in this mode`}
              checked={on.length === p.ts.length}
              onCheckedChange={(c) => toggleRow(p.key, p.ts, c)}
            />
          </label>
        </div>
        {notice[p.key] && <p className="px-3 pb-2 text-sm text-amber-800">{notice[p.key]}</p>}
        {lit.length >= 2 && (
          <div className="px-3 pb-3">
            <button
              type="button"
              aria-pressed={back.length === lit.length}
              onClick={() => togglePutBack(p.key, lit, back.length < lit.length)}
              className={chip(back.length === lit.length)}
            >
              ↩ Put back all ({back.length} of {lit.length})
            </button>
          </div>
        )}
        <ul className="divide-y divide-stone-100 border-t border-stone-100 px-3">{p.rows.map(row)}</ul>
      </section>
    );
  };

  const folded = (key: string, title: string, summary: string, isOpen: boolean, body: ReactNode, big = false) => (
    <section key={key}>
      <button
        type="button"
        aria-expanded={isOpen}
        onClick={() => setOpen((o) => ({ ...o, [key]: !isOpen }))}
        className="flex min-h-11 w-full items-center gap-2 rounded-xl px-1 text-left hover:bg-stone-100/70"
      >
        <ChevronDown className={cn("h-4 w-4 shrink-0 text-muted-foreground transition-transform", !isOpen && "-rotate-90")} aria-hidden />
        <span className={cn("min-w-0 flex-1", big ? "text-lg font-semibold text-stone-800" : "text-base font-semibold text-stone-700")}>
          {title}
        </span>
        <span className="shrink-0 text-sm text-muted-foreground">{summary}</span>
      </button>
      {isOpen && <div className="mt-2 space-y-3">{body}</div>}
    </section>
  );

  const roomBody = (id: string, ts: string[], flat = false) => {
    const devices = partsOf(flat ? ts : ts.filter((t) => !behind(subOf(t))), id);
    const settings = flat ? [] : partsOf(ts.filter((t) => behind(subOf(t))), `${id}/behind`);
    const settingTs = settings.flatMap((p) => p.ts);
    const backKey = `${id}/behind`;
    return (
      <>
        {devices.map(part)}
        {flat && wholeMode}
        {settings.length > 0 &&
          folded(
            backKey,
            "Behind the scenes",
            countWords(settingTs.filter((t) => view(t).on).length, settingTs.length),
            open[backKey] ?? hasIssues(settingTs),
            settings.map((p) =>
              p.rows.length === 1 && p.ts.length === 1 ? part({ ...p, rows: [{ ...p.rows[0], label: p.label }] }) : part(p),
            ),
          )}
      </>
    );
  };

  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">Switch on what this mode changes.</p>
      <p className="text-sm text-muted-foreground">
        ↩ Put back afterwards: when the room leaves this mode, it goes back to how it was, unless the next mode sets it.
      </p>
      {sections.map((sec, i) =>
        sec.name
          ? folded(
              `room/${sec.id}`,
              sec.name,
              `${sec.ts.filter((t) => view(t).on).length} of ${sec.ts.length} in this mode`,
              open[`room/${sec.id}`] ?? (i === 0 || (i === 1 && sections[0].flat) || hasIssues(sec.ts) || sec.ts.some((t) => view(t).on)),
              roomBody(sec.id, sec.ts, sec.flat),
              true,
            )
          : <div key="all" className="space-y-3">{roomBody(sec.id, sec.ts, sec.flat)}</div>,
      )}
    </div>
  );
}
