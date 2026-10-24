// The Lights setup page's pure helpers: reading the rig view into rows, the light types in the order the page
// offers them, the add/edit form's checks and body, and every word the page shows about a light. No React and
// nothing imported at run time, so lib.test.ts runs it under plain node (--experimental-strip-types).

import type { Tone } from "../Room/tones";

/** Who a light follows (POST /show/api/v1/rig/follow): the music, nothing, or a steady colour. */
export type FollowValue = "dmx" | "off" | { hex: string; bri: number };

/** One light as the page shows it (show_service GET /api/v1/rig, one per-fixture row; screens left out). */
export interface LightRow {
  id: string;
  label: string;
  kind: string | null;
  type: string | null;
  type_label: string | null;
  output: string | null;
  node_ip: string | null;
  universe: number | null;
  address: number | null;
  footprint: number | null;
  placement: string | null;
  role: string | null;
  zone: string | null;
  follow: FollowValue | null;
  online: boolean | null;
  last_rgb: [number, number, number] | null;
  /** On the Hue bridge (identify only; set up in the bridge, not here). */
  hue: boolean;
  /** On an Art-Net box: editable and removable here. */
  dmx: boolean;
  /** What it can do (show_service's FixtureCaps): Move it's controls come from this alone. Null: not known yet. */
  caps: Caps | null;
  /** Its own settings (fixtures.setup.yaml): home, limits, named positions, slot names, its normal beam. */
  setup: Setup;
}

/** A kind of light the room can drive (GET /show/api/v1/rig/types). */
export interface LightType {
  id: string;
  label: string;
  source: string | null;
  footprint: number;
  channels: string[];
  kind: string | null;
}

/** The add/edit form as typed (numbers stay text until checked, so typing never jumps). */
export interface Draft {
  type: string | null;
  node_ip: string;
  universe: string;
  address: string;
  label: string;
  placement: string | null;
  zone: string | null;
}

/** POST /show/api/v1/rig/fixtures (PUT takes any part of it). */
export interface FixtureBody {
  label?: string;
  type?: string;
  node_ip?: string;
  universe?: number;
  address?: number;
  placement?: string | null;
  zone?: string | null;
}

export interface StatusWords {
  tone: Tone;
  text: string;
  pulse?: boolean;
}

export interface PlacementChoice {
  key: string;
  placement: string;
  zone: string | null;
  words: string;
}

export interface Overlap {
  id: string;
  label: string;
  from: number;
  to: number;
}

export const FIRST_CHANNEL = 1;
export const LAST_CHANNEL = 512;

type Obj = Record<string, unknown>;

const isObj = (v: unknown): v is Obj => !!v && typeof v === "object" && !Array.isArray(v);

function str(v: unknown): string | null {
  return typeof v === "string" && v.trim() ? v.trim() : null;
}

function int(v: unknown): number | null {
  if (typeof v === "number" && Number.isFinite(v)) return Math.round(v);
  if (typeof v === "string" && /^\s*-?\d+\s*$/.test(v)) return Number(v.trim());
  return null;
}

function bool(v: unknown): boolean | null {
  return typeof v === "boolean" ? v : null;
}

const clamp255 = (n: number) => Math.max(0, Math.min(255, Math.round(n)));

/** "#rrggbb" (or "#rgb") as [r, g, b], else null. */
export function hexToRgb(hex: string): [number, number, number] | null {
  const m = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return null;
  const h = m[1].length === 3 ? m[1].replace(/./g, (c) => c + c) : m[1];
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}

export function rgbToHex([r, g, b]: [number, number, number]): string {
  return `#${[r, g, b].map((n) => clamp255(n).toString(16).padStart(2, "0")).join("")}`;
}

function parseRgb(v: unknown): [number, number, number] | null {
  if (Array.isArray(v) && v.length >= 3 && v.slice(0, 3).every((n) => typeof n === "number" && Number.isFinite(n))) {
    return [clamp255(v[0] as number), clamp255(v[1] as number), clamp255(v[2] as number)];
  }
  if (typeof v === "string") return hexToRgb(v);
  if (isObj(v) && [v.r, v.g, v.b].every((n) => typeof n === "number")) {
    return [clamp255(v.r as number), clamp255(v.g as number), clamp255(v.b as number)];
  }
  return null;
}

export function parseFollow(v: unknown): FollowValue | null {
  if (v === "dmx" || v === "off") return v;
  if (isObj(v)) {
    const hex = str(v.hex);
    if (hex && hexToRgb(hex)) {
      const bri = typeof v.bri === "number" && Number.isFinite(v.bri) ? Math.max(0, Math.min(100, v.bri)) : 100;
      return { hex, bri };
    }
  }
  return null;
}

/** One row from the contract's per-fixture row, filling gaps (footprint, the box, the universe) from registry_view. */
function toRow(o: Obj, rv: Obj | null): LightRow | null {
  const id = str(o.id);
  if (!id) return null;
  const types = rv && isObj(rv.types) ? rv.types : {};
  const outputs = rv && isObj(rv.outputs) ? rv.outputs : {};
  const nodes = rv && isObj(rv.nodes) ? rv.nodes : {};
  const type = str(o.type);
  const typeDef = type && isObj(types[type]) ? (types[type] as Obj) : null;
  const output = str(o.output);
  const outDef = output && isObj(outputs[output]) ? (outputs[output] as Obj) : null;
  const nodeId = outDef ? str(outDef.node) : null;
  const nodeDef = nodeId && isObj(nodes[nodeId]) ? (nodes[nodeId] as Obj) : null;
  const driver = str(o.driver) ?? (outDef ? str(outDef.driver) : null);
  const node_ip = str(o.node_ip) ?? (nodeDef ? str(nodeDef.last_ip) : null);
  const universe = int(o.universe) ?? (outDef ? int(outDef.port_address) : null);
  const address = int(o.address);
  const fp = int(o.footprint) ?? (typeDef ? int(typeDef.footprint) : null);
  const hue = driver === "hue" || (!driver && !!str(o.light));
  return {
    id,
    label: str(o.label) ?? id,
    kind: str(o.kind) ?? (typeDef ? str(typeDef.kind) : null),
    type,
    type_label: str(o.type_label) ?? (typeDef ? str(typeDef.label) : null),
    output,
    node_ip,
    universe,
    address,
    footprint: fp && fp > 0 ? fp : null,
    placement: str(o.placement),
    role: str(o.role),
    zone: str(o.zone),
    follow: parseFollow(o.follow),
    online: bool(o.online),
    last_rgb: parseRgb(o.last_rgb),
    hue,
    dmx: !hue && (driver === "artnet" || address !== null || node_ip !== null),
    caps: parseCaps(o.caps),
    setup: parseSetup(o.setup),
  };
}

/**
 * Every light in GET /show/api/v1/rig: the contract's rows (`fixtures`, a list or a map by id), else the registry's
 * own fixtures (`registry_view.fixtures`, an older show_service). Screens are not lights, so they are left out.
 */
export function rowsFromRig(rig: unknown): LightRow[] {
  if (!isObj(rig)) return [];
  const rv = isObj(rig.registry_view) ? rig.registry_view : null;
  const raw = rig.fixtures ?? rig.rows ?? (rv ? rv.fixtures : undefined);
  let list: Obj[] = [];
  if (Array.isArray(raw)) list = raw.filter(isObj);
  else if (isObj(raw)) list = Object.entries(raw).flatMap(([id, v]) => (isObj(v) ? [{ id, ...v }] : []));
  return list.flatMap((o) => {
    const r = toRow(o, rv);
    return r && r.kind !== "screen" ? [r] : [];
  });
}

/** The registry's problems (a hand-file clash, a bad entry) in their own words. */
export function rigProblems(rig: unknown): string[] {
  if (!isObj(rig)) return [];
  const reg = isObj(rig.registry) ? rig.registry : isObj(rig.registry_view) ? rig.registry_view : null;
  const out = [...problemWords(reg?.problems), ...problemWords(rig.problems)];
  const err = reg ? str(reg.error) : null;
  if (err) out.unshift(err);
  return [...new Set(out)];
}

/** Problems as plain sentences, whatever their shape: strings, or {message | detail | why | problem, path?}. */
export function problemWords(raw: unknown): string[] {
  if (raw == null) return [];
  if (typeof raw === "string") return raw.trim() ? [raw.trim()] : [];
  if (Array.isArray(raw)) return raw.flatMap((p) => problemWords(p));
  if (isObj(raw)) {
    const words = str(raw.message) ?? str(raw.detail) ?? str(raw.why) ?? str(raw.problem) ?? str(raw.text);
    return words ? [words] : [];
  }
  return [];
}

/** GET /show/api/v1/rig/types (a list, or {types: [...]}) as the kinds a DMX light can be: channelled types only,
 *  the generic ones first in the server's order, then the rest (Open Fixture Library and the like) by name. */
export function typesFrom(raw: unknown): LightType[] {
  const list = Array.isArray(raw) ? raw : isObj(raw) && Array.isArray(raw.types) ? raw.types : [];
  const all: LightType[] = list.filter(isObj).flatMap((o) => {
    const id = str(o.id);
    const footprint = int(o.footprint) ?? (Array.isArray(o.channels) ? o.channels.length : 0);
    if (!id || footprint <= 0) return [];
    return [
      {
        id,
        label: str(o.label) ?? id,
        source: str(o.source),
        footprint,
        channels: Array.isArray(o.channels) ? o.channels.flatMap((c) => (typeof c === "string" ? [c] : [])) : [],
        kind: str(o.kind),
      },
    ];
  });
  const generic = all.filter(isGeneric);
  const rest = all.filter((t) => !isGeneric(t)).sort((a, b) => a.label.localeCompare(b.label));
  return [...generic, ...rest];
}

export function isGeneric(t: LightType): boolean {
  return t.source === "generic" || t.id.startsWith("generic-");
}

// -- words -------------------------------------------------------------------------------------------------------------

const cap = (s: string) => (s ? s[0].toUpperCase() + s.slice(1) : s);

/** "Above the north screen", "Opposite the south screen", "North side", or a free-text placement as written. */
export function placementWords(placement: string | null, zone: string | null): string | null {
  const p = placement?.trim() || null;
  const z = zone?.trim() || null;
  if (p && z && /^(above|opposite|below|beside)$/i.test(p)) return `${cap(p.toLowerCase())} the ${z} screen`;
  if (p && z) return `${cap(p)} (${z} side)`;
  if (p) return cap(p);
  if (z) return `${cap(z)} side`;
  return null;
}

/** The placements the rig reports, as choices: every (placement, zone) the lights have, plus each known placement
 *  word on every known side, so a new light can go "above" a screen no lamp is above yet. */
export function placementChoices(rows: LightRow[]): PlacementChoice[] {
  const placements: string[] = [];
  const zones: string[] = [];
  const add = (list: string[], v: string | null) => {
    if (v && !list.includes(v)) list.push(v);
  };
  const seen: PlacementChoice[] = [];
  const keys = new Set<string>();
  const push = (placement: string, zone: string | null) => {
    const key = `${placement}\u0000${zone ?? ""}`;
    if (keys.has(key)) return;
    keys.add(key);
    seen.push({ key, placement, zone, words: placementWords(placement, zone) ?? placement });
  };
  for (const r of rows) {
    add(zones, r.zone);
    if (r.placement) add(placements, r.placement);
  }
  zones.sort();
  // Words that pair with a side ("above" + "north") go on every side; anything else stays as the lights have it.
  const paired = placements.filter((p) => zones.length > 0 && placementWords(p, zones[0])?.endsWith(" screen"));
  for (const z of zones) for (const p of paired) push(p, z);
  for (const r of rows) if (r.placement && !paired.includes(r.placement)) push(r.placement, r.zone);
  return seen;
}

/** The status pill: Not answering, Following the music, Off, Online (nothing while it isn't known yet). */
export function statusOf(r: LightRow): StatusWords | null {
  if (r.online === false) return { tone: "bad", text: "Not answering" };
  if (r.follow === "dmx") return { tone: "live", text: "Following the music", pulse: true };
  if (r.follow === "off") return { tone: "muted", text: "Off" };
  if (r.online === true) return { tone: "ok", text: "Online" };
  return null;
}

/** What it does when it isn't being identified, in a sentence. */
export function doingWords(r: LightRow): string | null {
  const f = r.follow;
  if (f === "dmx") return "Follows the music";
  if (f === "off") return "Off (dark)";
  if (f) return `A steady colour at ${Math.round(f.bri)}%`;
  return null;
}

/** The swatch: what it showed last, else its steady colour, else nothing (grey). */
export function swatchOf(r: LightRow): string | null {
  if (r.last_rgb) return r.last_rgb.some((n) => n > 0) ? rgbToHex(r.last_rgb) : null;
  const f = r.follow;
  return f && typeof f === "object" ? f.hex : null;
}

/** What it is: the type's own label, else its id. */
export function typeWords(r: LightRow, types: LightType[] = []): string | null {
  return r.type_label ?? types.find((t) => t.id === r.type)?.label ?? r.type;
}

export function channelRange(address: number, footprint: number): [number, number] {
  return [address, address + Math.max(1, footprint) - 1];
}

/** "Uses channel 10" / "Uses channels 10–13". */
export function footprintWords(address: number | null, footprint: number | null): string | null {
  if (address === null || !footprint || footprint < 1) return null;
  const [a, b] = channelRange(address, footprint);
  return a === b ? `Uses channel ${a}` : `Uses channels ${a}–${b}`;
}

/** Where it's plugged in: "Box 192.168.1.60 · universe 1 · channels 10–13". */
export function wiringWords(r: LightRow): string | null {
  if (!r.dmx) return null;
  const parts: string[] = [];
  if (r.node_ip) parts.push(`Box ${r.node_ip}`);
  if (r.universe !== null) parts.push(`universe ${r.universe}`);
  const fp = footprintWords(r.address, r.footprint);
  if (fp) parts.push(fp.replace(/^Uses c/, "c"));
  else if (r.address !== null) parts.push(`from channel ${r.address}`);
  return parts.length ? parts.join(" · ") : null;
}

// -- the form ----------------------------------------------------------------------------------------------------------

export const IPV4 = /^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$/;

/** A whole number typed in a box, or null. */
export function wholeNumber(s: string): number | null {
  return /^\s*\d+\s*$/.test(s) ? Number(s.trim()) : null;
}

const sameBox = (r: LightRow, ip: string, universe: number) => r.node_ip === ip && r.universe === universe;

/** The first channel after every light already on that box's universe (1 when it has none), or null when full. */
export function nextFreeChannel(rows: LightRow[], ip: string, universe: number | null, exceptId?: string | null): number | null {
  if (!ip.trim() || universe === null) return null;
  let end = 0;
  for (const r of rows) {
    if (r.id === exceptId || !sameBox(r, ip.trim(), universe) || r.address === null) continue;
    end = Math.max(end, channelRange(r.address, r.footprint ?? 1)[1]);
  }
  return end + 1 <= LAST_CHANNEL ? end + 1 : null;
}

/** The lights on the same box and universe whose channels this one would share. */
export function overlapsWith(
  rows: LightRow[],
  ip: string,
  universe: number | null,
  address: number | null,
  footprint: number | null,
  exceptId?: string | null,
): Overlap[] {
  if (!ip.trim() || universe === null || address === null || !footprint) return [];
  const [a, b] = channelRange(address, footprint);
  return rows.flatMap((r) => {
    if (r.id === exceptId || !sameBox(r, ip.trim(), universe) || r.address === null) return [];
    const [c, d] = channelRange(r.address, r.footprint ?? 1);
    return c <= b && a <= d ? [{ id: r.id, label: r.label, from: c, to: d }] : [];
  });
}

export function emptyDraft(): Draft {
  return { type: null, node_ip: "", universe: "1", address: "", label: "", placement: null, zone: null };
}

export function draftFromRow(r: LightRow): Draft {
  return {
    type: r.type,
    node_ip: r.node_ip ?? "",
    universe: r.universe !== null ? String(r.universe) : "1",
    address: r.address !== null ? String(r.address) : "",
    label: r.label,
    placement: r.placement,
    zone: r.zone,
  };
}

export type DraftField = "type" | "node_ip" | "universe" | "address" | "label";

/** What has to change before it can be saved, by field, in plain words (nothing here is a warning: see overlaps). */
export function checkDraft(d: Draft, type: LightType | null | undefined): Partial<Record<DraftField, string>> {
  const out: Partial<Record<DraftField, string>> = {};
  if (!d.type) out.type = "Pick what kind of light it is.";
  if (!IPV4.test(d.node_ip.trim())) out.node_ip = "The box's address is four numbers with dots between them, as its label or app shows it.";
  const u = wholeNumber(d.universe);
  if (u === null || u < 1) out.universe = "The universe is a whole number, 1 or more.";
  const a = wholeNumber(d.address);
  if (a === null || a < FIRST_CHANNEL || a > LAST_CHANNEL) out.address = `The first channel is a number from ${FIRST_CHANNEL} to ${LAST_CHANNEL}.`;
  else if (type && a + type.footprint - 1 > LAST_CHANNEL) {
    out.address = `It needs ${type.footprint} channels, so it can start at ${LAST_CHANNEL - type.footprint + 1} at the latest.`;
  }
  if (!d.label.trim()) out.label = "Give it a name.";
  return out;
}

/** The step of the add flow each field is asked on (1-based). */
export const FIELD_STEP: Record<DraftField, number> = { type: 1, node_ip: 2, universe: 2, address: 3, label: 5 };

/** The whole body for POST (call only once checkDraft is empty). */
export function draftBody(d: Draft): FixtureBody {
  const body: FixtureBody = {
    label: d.label.trim(),
    type: d.type ?? undefined,
    node_ip: d.node_ip.trim(),
    universe: wholeNumber(d.universe) ?? 1,
    address: wholeNumber(d.address) ?? FIRST_CHANNEL,
  };
  if (d.placement) body.placement = d.placement;
  if (d.zone) body.zone = d.zone;
  return body;
}

/** Only what changed, for PUT (placement and zone go as null when cleared). Empty when nothing changed. */
export function changedBody(d: Draft, r: LightRow): FixtureBody {
  const full = draftBody(d);
  const out: FixtureBody = {};
  if (full.label !== r.label) out.label = full.label;
  if (full.type && full.type !== r.type) out.type = full.type;
  if (full.node_ip !== (r.node_ip ?? "")) out.node_ip = full.node_ip;
  if (full.universe !== r.universe) out.universe = full.universe;
  if (full.address !== r.address) out.address = full.address;
  if ((d.placement ?? null) !== r.placement) out.placement = d.placement ?? null;
  if ((d.zone ?? null) !== r.zone) out.zone = d.zone ?? null;
  return out;
}

// -- moving lights: capabilities, setup and Move it's controls (docs/LIGHTS.md §2.3, §8.3) -----------------------------

export interface AxisCaps {
  /** Centred degrees: 0 is the middle of the light's range. */
  min: number;
  max: number;
  fine: boolean;
}

export interface SlotChoice {
  slot: number;
  name: string;
  hex: string | null;
  /** The wheel's Open slot (no pattern). */
  open: boolean;
  /** It can shake (gobo slots). */
  shake: boolean;
}

export interface MacroChoice {
  channel: string;
  name: string;
  choices: string[];
}

/** What a light can do (show_service's FixtureCaps). The page builds Move it's controls from this alone. */
export interface Caps {
  attrs: string[];
  pan: AxisCaps | null;
  tilt: AxisCaps | null;
  zoom: { min: number; max: number; known: boolean } | null;
  focus: boolean;
  iris: boolean;
  frost: boolean;
  colour: "mix" | "wheel" | "none";
  colour_slots: SlotChoice[];
  gobo_slots: SlotChoice[];
  gobo2_slots: SlotChoice[];
  prism_slots: SlotChoice[];
  spins: string[];
  cct: { min_k: number; max_k: number } | null;
  shutter: string[];
  macros: MacroChoice[];
  /** It turns and tilts. */
  moves: boolean;
}

export type Axis = "pan" | "tilt";
export type PanTilt = { pan: number; tilt: number };

/** A light's own settings (fixtures.setup.yaml, written by POST .../setup). */
export interface Setup {
  home: PanTilt | null;
  limits: Partial<Record<Axis, [number, number]>>;
  positions: Record<string, PanTilt>;
  jog_flip: Record<Axis, boolean>;
  beam: Record<string, number>;
}

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function strList(v: unknown): string[] {
  return Array.isArray(v) ? v.flatMap((x) => (typeof x === "string" && x ? [x] : [])) : [];
}

function axisCaps(v: unknown): AxisCaps | null {
  if (!isObj(v)) return null;
  const min = num(v.min);
  const max = num(v.max);
  return min !== null && max !== null && max > min ? { min, max, fine: v.fine === true } : null;
}

function slotList(v: unknown): SlotChoice[] {
  if (!Array.isArray(v)) return [];
  return v.filter(isObj).flatMap((o) => {
    const slot = int(o.slot);
    if (slot === null) return [];
    const hex = str(o.hex);
    return [{ slot, name: str(o.name) ?? `Slot ${slot}`, hex: hex && hexToRgb(hex) ? hex : null, open: o.open === true, shake: o.shake === true }];
  });
}

/** show_service's caps for a light, read tolerantly; null when it sent none (an older show_service). */
export function parseCaps(v: unknown): Caps | null {
  if (!isObj(v)) return null;
  const attrs = strList(v.attrs);
  const pan = axisCaps(v.pan);
  const tilt = axisCaps(v.tilt);
  const zoomRaw = isObj(v.zoom) ? v.zoom : null;
  const zmin = zoomRaw ? num(zoomRaw.min) : null;
  const zmax = zoomRaw ? num(zoomRaw.max) : null;
  const cctRaw = isObj(v.cct) ? v.cct : null;
  const kmin = cctRaw ? num(cctRaw.min_k) : null;
  const kmax = cctRaw ? num(cctRaw.max_k) : null;
  const macros = Array.isArray(v.macros)
    ? v.macros.filter(isObj).flatMap((m) => {
        const channel = str(m.channel);
        if (!channel) return [];
        const choices = Array.isArray(m.choices) ? m.choices.flatMap((c) => (isObj(c) && str(c.word) ? [str(c.word) as string] : [])) : [];
        return choices.length ? [{ channel, name: str(m.name) ?? channel, choices }] : [];
      })
    : [];
  return {
    attrs,
    pan,
    tilt,
    zoom: zmin !== null && zmax !== null && zmax > zmin ? { min: zmin, max: zmax, known: zoomRaw?.known === true } : null,
    focus: v.focus === true,
    iris: v.iris === true,
    frost: v.frost === true,
    colour: v.colour === "mix" || v.colour === "wheel" ? v.colour : "none",
    colour_slots: slotList(v.colour_slots),
    gobo_slots: slotList(v.gobo_slots),
    gobo2_slots: slotList(v.gobo2_slots),
    prism_slots: slotList(v.prism_slots),
    spins: strList(v.spins),
    cct: kmin !== null && kmax !== null && kmax > kmin ? { min_k: kmin, max_k: kmax } : null,
    shutter: strList(v.shutter),
    macros,
    moves: typeof v.moves === "boolean" ? v.moves : !!(pan && tilt),
  };
}

function panTilt(v: unknown): PanTilt | null {
  if (!isObj(v)) return null;
  const pan = num(v.pan);
  const tilt = num(v.tilt);
  return pan !== null && tilt !== null ? { pan, tilt } : null;
}

function pair(v: unknown): [number, number] | null {
  if (!Array.isArray(v) || v.length !== 2) return null;
  const a = num(v[0]);
  const b = num(v[1]);
  return a !== null && b !== null ? [Math.min(a, b), Math.max(a, b)] : null;
}

export function emptySetup(): Setup {
  return { home: null, limits: {}, positions: {}, jog_flip: { pan: false, tilt: false }, beam: {} };
}

/** A light's setup as POST .../setup answers it (or a rig row carries it), read tolerantly. */
export function parseSetup(v: unknown): Setup {
  const out = emptySetup();
  if (!isObj(v)) return out;
  out.home = panTilt(v.home);
  if (isObj(v.limits)) {
    for (const ax of ["pan", "tilt"] as Axis[]) {
      const p = pair(v.limits[ax]);
      if (p) out.limits[ax] = p;
    }
  }
  if (isObj(v.positions)) {
    for (const [name, pos] of Object.entries(v.positions)) {
      const p = panTilt(pos);
      if (p) out.positions[name] = p;
    }
  }
  if (isObj(v.jog_flip)) out.jog_flip = { pan: v.jog_flip.pan === true, tilt: v.jog_flip.tilt === true };
  if (isObj(v.beam)) for (const [k, x] of Object.entries(v.beam)) if (num(x) !== null) out.beam[k] = x as number;
  return out;
}

/** Move it's numbers (show.yaml lights.jog, GET /rig lights_ui); the same starting points as show_service's. */
export interface LightsUi {
  send_hz: number;
  idle_s: number;
  nudge_deg: { fine: number; coarse: number };
  drag_deg_per_px: { fine: number; coarse: number };
}

export const LIGHTS_UI_DEFAULT: LightsUi = {
  send_hz: 20,
  idle_s: 60,
  nudge_deg: { fine: 1, coarse: 5 },
  drag_deg_per_px: { fine: 0.1, coarse: 0.5 },
};

export function lightsUiFrom(rig: unknown): LightsUi {
  const d = LIGHTS_UI_DEFAULT;
  const ui = isObj(rig) && isObj(rig.lights_ui) ? rig.lights_ui : {};
  const pos = (v: unknown, dflt: number) => (num(v) !== null && (v as number) > 0 ? (v as number) : dflt);
  const two = (v: unknown, dflt: { fine: number; coarse: number }) => ({
    fine: pos(isObj(v) ? v.fine : null, dflt.fine),
    coarse: pos(isObj(v) ? v.coarse : null, dflt.coarse),
  });
  return {
    send_hz: pos(ui.send_hz, d.send_hz),
    idle_s: pos(ui.idle_s, d.idle_s),
    nudge_deg: two(ui.nudge_deg, d.nudge_deg),
    drag_deg_per_px: two(ui.drag_deg_per_px, d.drag_deg_per_px),
  };
}

/** One control on Move it, from a capability (never from a type). `group` is the heading it sits under. */
export type Control =
  | { kind: "slider"; group: string; attr: string; label: string; min: number; max: number; step: number; ends: [string, string]; unit?: string }
  | { kind: "colour"; group: string; attr: "colour"; label: string }
  | { kind: "slots"; group: string; attr: string; wheel: "colour" | "gobo" | "gobo2"; label: string; slots: SlotChoice[] }
  | { kind: "spin"; group: string; attr: string; label: string }
  | { kind: "switch"; group: string; attr: string; label: string; on: unknown; off: unknown }
  | { kind: "macro"; group: string; attr: "macro"; channel: string; label: string; choices: string[] };

/** A shake switched on shakes at this share of the fastest (a starting point: the slider-free control). */
export const SHAKE_ON = 0.5;

/** Move it's controls for what a light can do (docs/LIGHTS.md §8.3's table), in the order the page shows them. */
export function controlsFor(caps: Caps | null): Control[] {
  if (!caps) return [];
  const has = (a: string) => caps.attrs.includes(a);
  const spins = (a: string) => caps.spins.includes(a) || has(a);
  const out: Control[] = [];
  if (has("intensity")) {
    out.push({ kind: "slider", group: "Brightness", attr: "intensity", label: "Brightness", min: 0, max: 1, step: 0.01, ends: ["Dark", "Full"] });
  }
  if (caps.colour === "mix") out.push({ kind: "colour", group: "Colour", attr: "colour", label: "Colour" });
  if (caps.colour === "wheel" && caps.colour_slots.length) {
    out.push({ kind: "slots", group: "Colour", attr: "colour_slot", wheel: "colour", label: "Colour", slots: caps.colour_slots });
  }
  if (spins("colour_spin")) out.push({ kind: "spin", group: "Colour", attr: "colour_spin", label: "Spin the colours" });
  if (caps.cct) {
    out.push({ kind: "slider", group: "Colour", attr: "cct", label: "White warmth", min: caps.cct.min_k, max: caps.cct.max_k, step: 50, ends: ["Warm", "Cool"], unit: "K" });
  }
  for (const [wheel, group] of [["gobo", "Pattern"], ["gobo2", "Second pattern"]] as const) {
    const slots = wheel === "gobo" ? caps.gobo_slots : caps.gobo2_slots;
    if (!slots.length) continue;
    out.push({ kind: "slots", group, attr: wheel, wheel, label: group, slots });
    if (spins(`${wheel}_spin`)) out.push({ kind: "spin", group, attr: `${wheel}_spin`, label: "Spin" });
    if (slots.some((s) => s.shake) || has(`${wheel}_shake`)) {
      out.push({ kind: "switch", group, attr: `${wheel}_shake`, label: "Shake", on: SHAKE_ON, off: 0 });
    }
  }
  if (caps.prism_slots.length || has("prism")) {
    out.push({ kind: "switch", group: "Split the beam", attr: "prism", label: "Split the beam", on: caps.prism_slots[0]?.slot || 1, off: 0 });
    if (spins("prism_spin")) out.push({ kind: "spin", group: "Split the beam", attr: "prism_spin", label: "Spin" });
  }
  if (caps.zoom) {
    out.push({ kind: "slider", group: "Beam", attr: "zoom", label: "Beam size", min: caps.zoom.min, max: caps.zoom.max, step: 0.5, ends: ["Narrow", "Wide"], unit: "°" });
  }
  if (caps.focus) out.push({ kind: "slider", group: "Beam", attr: "focus", label: "Focus", min: 0, max: 1, step: 0.01, ends: ["Near", "Far"] });
  if (caps.iris) out.push({ kind: "slider", group: "Beam", attr: "iris", label: "Iris", min: 0, max: 1, step: 0.01, ends: ["Small", "Open"] });
  if (caps.frost) out.push({ kind: "slider", group: "Beam", attr: "frost", label: "Soft edge", min: 0, max: 1, step: 0.01, ends: ["Sharp", "Soft"] });
  if (caps.shutter.includes("closed")) {
    out.push({ kind: "switch", group: "Shutter", attr: "shutter", label: "Shutter open", on: { mode: "open" }, off: { mode: "closed" } });
  }
  for (const m of caps.macros) {
    out.push({ kind: "macro", group: "More", attr: "macro", channel: m.channel, label: m.name, choices: m.choices });
  }
  return out;
}

/** The beam attributes "Keep this beam as its normal look" keeps. */
export const BEAM_ATTRS = ["zoom", "focus", "iris", "frost"];

export function hasBeam(caps: Caps | null): boolean {
  return !!caps && (!!caps.zoom || caps.focus || caps.iris || caps.frost);
}

const deg = (x: number) => `${x < 0 ? "−" : ""}${Math.abs(Math.round(x))}°`;

/** The pad's readout: "Turned 45° · Tilted −20°". */
export function turnWords(pan: number, tilt: number): string {
  return `Turned ${deg(pan)} · Tilted ${deg(tilt)}`;
}

/** "Turns from −120° to 90°", or "Turns anywhere" with no limits set. */
export function limitWords(setup: Setup, axis: Axis): string {
  const verb = axis === "pan" ? "Turns" : "Tilts";
  const p = setup.limits[axis];
  return p ? `${verb} from ${deg(p[0])} to ${deg(p[1])}` : `${verb} anywhere (no limits)`;
}

/** The hand-back countdown starts this many seconds before the show takes the light back. */
export const HAND_BACK_WARN_S = 15;

export type HoldState = "starting" | "holding" | "released" | "cant";

/** Move it's banner: who has the light, and the countdown before the show takes it back. */
export function holdWords(state: HoldState, leftS: number | null, why?: string | null): { text: string; warn: boolean } {
  if (state === "cant") return { text: why || "This light can't be moved from here yet.", warn: true };
  if (state === "released") return { text: "Handed back to the show. Tap to hold it again.", warn: false };
  if (state === "starting") return { text: "Taking this light from the show…", warn: false };
  if (leftS !== null && leftS <= HAND_BACK_WARN_S) {
    return { text: `Handing it back to the show in ${Math.max(0, Math.ceil(leftS))} s`, warn: true };
  }
  return { text: "The show leaves this light alone while this is open.", warn: false };
}

/** A place's or a position's id from its name, as show_service makes them: "The piano" -> "piano". */
export function placeId(words: string): string {
  const s = words.trim();
  if (/^[a-z0-9][a-z0-9_.-]{0,63}$/.test(s)) return s;
  return s
    .toLowerCase()
    .replace(/^(the|a|an)\s+/, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 64)
    .replace(/-+$/g, "");
}

/** Words for a name with no label: "mirror_ball" -> "Mirror ball". */
export function nameWords(name: string): string {
  const s = name.replace(/[-_.]+/g, " ").trim();
  return s ? s[0].toUpperCase() + s.slice(1) : name;
}

/** "Moving head" from "Moving head: pan, tilt (fine), … (13 channels)". */
export function shortTypeName(label: string | null | undefined): string {
  const s = (label ?? "").split(/[:(]/)[0].trim();
  return s || "Light";
}

/** The name a new light is saved under before it's named (step 3): its kind and the next free number. */
export function provisionalName(t: LightType | null | undefined, rows: LightRow[]): string {
  const base = shortTypeName(t?.label);
  const taken = new Set(rows.map((r) => r.label.toLowerCase()));
  for (let n = 1; ; n++) {
    const name = `${base} ${n}`;
    if (!taken.has(name.toLowerCase())) return name;
  }
}

// -- the room: the Room map's space and the live view (docs/LIGHTS.md §3, §8.1) ----------------------------------------

export type Vec3 = [number, number, number];
export const FRONTS = ["north", "east", "south", "west"] as const;
export type Front = (typeof FRONTS)[number];

export interface Sample {
  pan: number;
  tilt: number;
  point: string | null;
}

export interface Pose {
  at: Vec3;
  rms_deg: number;
  samples: number;
}

export interface PlacedThing {
  at: Vec3 | null;
  mount: string | null;
  plane: { normal: Vec3 | null; up: Vec3 | null; size: [number, number] | null } | null;
  teach: Sample[];
  pose: Pose | null;
}

export interface Place {
  name: string;
  label: string;
  at: Vec3;
  from: string | null;
}

export interface Area {
  name: string;
  label: string;
  surface: string;
  centre: Vec3;
  size: [number, number];
}

export interface Target {
  name: string;
  label: string;
  kind: string;
  at: Vec3 | null;
}

/** GET /rig/space, read tolerantly (show_service's SpaceView + targets). */
export interface RoomSpace {
  error: string | null;
  problems: string[];
  size: Vec3 | null;
  front: Front;
  points: Place[];
  regions: Area[];
  appliances: Record<string, PlacedThing>;
  targets: Target[];
}

function vec3(v: unknown): Vec3 | null {
  if (!Array.isArray(v) || v.length !== 3) return null;
  const xs = v.map(num);
  return xs.every((x) => x !== null) ? (xs as Vec3) : null;
}

function size2(v: unknown): [number, number] | null {
  if (!Array.isArray(v) || v.length !== 2) return null;
  const a = num(v[0]);
  const b = num(v[1]);
  return a !== null && b !== null && a > 0 && b > 0 ? [a, b] : null;
}

export function frontOf(v: unknown): Front {
  return (FRONTS as readonly string[]).includes(v as string) ? (v as Front) : "north";
}

export function parseSpace(v: unknown): RoomSpace {
  const o = isObj(v) ? v : {};
  const room = isObj(o.room) ? o.room : {};
  const size = vec3(room.size);
  const points = isObj(o.points)
    ? Object.entries(o.points).flatMap(([name, p]) => {
        const at = isObj(p) ? vec3(p.at) : null;
        return at && isObj(p) ? [{ name, label: str(p.label) ?? nameWords(name), at, from: str(p.from) }] : [];
      })
    : [];
  const regions = isObj(o.regions)
    ? Object.entries(o.regions).flatMap(([name, r]) => {
        if (!isObj(r)) return [];
        const centre = vec3(r.centre);
        const sz = size2(r.size);
        return centre && sz ? [{ name, label: str(r.label) ?? nameWords(name), surface: str(r.surface) ?? "floor", centre, size: sz }] : [];
      })
    : [];
  const appliances: Record<string, PlacedThing> = {};
  if (isObj(o.appliances)) {
    for (const [id, a] of Object.entries(o.appliances)) {
      if (!isObj(a)) continue;
      const pl = isObj(a.plane) ? a.plane : null;
      const pose = isObj(a.pose) && vec3(a.pose.at) ? { at: vec3(a.pose.at) as Vec3, rms_deg: num(a.pose.rms_deg) ?? 0, samples: int(a.pose.samples) ?? 0 } : null;
      appliances[id] = {
        at: vec3(a.at),
        mount: str(a.mount),
        plane: pl ? { normal: vec3(pl.normal), up: vec3(pl.up), size: size2(pl.size) } : null,
        teach: Array.isArray(a.teach)
          ? a.teach.filter(isObj).flatMap((s) => (num(s.pan) !== null && num(s.tilt) !== null ? [{ pan: s.pan as number, tilt: s.tilt as number, point: str(s.point) }] : []))
          : [],
        pose,
      };
    }
  }
  const targets = Array.isArray(o.targets)
    ? o.targets.filter(isObj).flatMap((t) => {
        const name = str(t.name);
        return name ? [{ name, label: str(t.label) ?? nameWords(name), kind: str(t.kind) ?? "point", at: vec3(t.at) }] : [];
      })
    : [];
  return { error: str(o.error), problems: problemWords(o.problems), size, front: frontOf(room.front), points, regions, appliances, targets };
}

export interface LiveFixture {
  at: Vec3 | null;
  hit: Vec3 | null;
  surface: string | null;
  pan: number | null;
  tilt: number | null;
  zoom_deg: number | null;
  gobo: string | null;
  prism: boolean;
  intensity: number;
  hex: string | null;
  held: boolean;
  calibrated: boolean;
}

export interface LiveFlow {
  dir: string;
  at: string | null;
  at_xyz: Vec3 | null;
}

export interface LiveView {
  flow: LiveFlow | null;
  fixtures: Record<string, LiveFixture>;
}

/** GET /rig/live (show_service's LiveView), read tolerantly. */
export function parseLive(v: unknown): LiveView {
  const o = isObj(v) ? v : {};
  const fl = isObj(o.flow) ? o.flow : null;
  const fixtures: Record<string, LiveFixture> = {};
  if (isObj(o.fixtures)) {
    for (const [id, f] of Object.entries(o.fixtures)) {
      if (!isObj(f)) continue;
      fixtures[id] = {
        at: vec3(f.at),
        hit: vec3(f.hit),
        surface: str(f.surface),
        pan: num(f.pan),
        tilt: num(f.tilt),
        zoom_deg: num(f.zoom_deg),
        gobo: str(f.gobo),
        prism: f.prism === true,
        intensity: num(f.intensity) ?? 0,
        hex: str(f.hex),
        held: f.held === true,
        calibrated: f.calibrated === true,
      };
    }
  }
  return { flow: fl && str(fl.dir) ? { dir: str(fl.dir) as string, at: str(fl.at), at_xyz: vec3(fl.at_xyz) } : null, fixtures };
}

/** The flow in Room-page words: "Flowing upwards", "Turning clockwise". */
export function flowWords(dir: string | null | undefined, atLabel?: string | null): string {
  switch (dir) {
    case "up":
      return "Flowing upwards";
    case "down":
      return "Flowing downwards";
    case "left":
      return "Flowing to the left";
    case "right":
      return "Flowing to the right";
    case "cw":
      return "Turning clockwise";
    case "ccw":
      return "Turning anticlockwise";
    case "outward":
      return "Spreading outwards";
    case "inward":
      return "Gathering inwards";
    case "towards":
      return atLabel ? `Flowing towards ${atLabel}` : "Flowing towards one place";
    case "away":
      return atLabel ? `Flowing away from ${atLabel}` : "Flowing away from one place";
    default:
      return "Still";
  }
}

/** "It knows where it hangs (within 1°)." for a calibrated light. */
export function poseWords(pose: Pose | null, taught: number): string {
  if (pose) return `It knows where it hangs (within ${Math.max(1, Math.round(pose.rms_deg))}°).`;
  return taught > 0 ? `Learnt ${taught} of 3.` : "Point it at 3 places so the map can show where it shines.";
}

// -- the Room map's plan: room metres <-> SVG pixels, the front wall at the top ------------------------------------------

/** Unit vectors in the floor plane (x east, y north): the wall people face (f) and their right (r). */
const FRONT_AXES: Record<Front, { f: [number, number]; r: [number, number] }> = {
  north: { f: [0, 1], r: [1, 0] },
  east: { f: [1, 0], r: [0, -1] },
  south: { f: [0, -1], r: [-1, 0] },
  west: { f: [-1, 0], r: [0, 1] },
};

/** The plan's frame: the room drawn from above with its front wall at the top, so left and right on the page are the
 *  room's left and right as people face the front. `s` px per metre. */
export interface PlanFrame {
  front: Front;
  size: Vec3;
  s: number;
  pad: number;
  /** The SVG's width and the plan's height in px (the side strip is `sideH` more). */
  w: number;
  h: number;
  sideH: number;
  u0: number;
  v1: number;
}

export function planFrame(size: Vec3, front: Front, widthPx: number, pad = 24): PlanFrame {
  const { f, r } = FRONT_AXES[front];
  const corners: [number, number][] = [
    [0, 0],
    [size[0], 0],
    [0, size[1]],
    [size[0], size[1]],
  ];
  const us = corners.map(([x, y]) => x * r[0] + y * r[1]);
  const vs = corners.map(([x, y]) => x * f[0] + y * f[1]);
  const u0 = Math.min(...us);
  const span = Math.max(...us) - u0;
  const s = Math.max(1, (Math.max(widthPx, 2 * pad + 40) - 2 * pad) / Math.max(span, 0.1));
  const depth = Math.max(...vs) - Math.min(...vs);
  return { front, size, s, pad, w: widthPx, h: depth * s + 2 * pad, sideH: size[2] * s + 2 * pad, u0, v1: Math.max(...vs) };
}

/** A room point (x, y) on the plan, in px. */
export function toPlan(fr: PlanFrame, x: number, y: number): [number, number] {
  const { f, r } = FRONT_AXES[fr.front];
  const u = x * r[0] + y * r[1];
  const v = x * f[0] + y * f[1];
  return [fr.pad + (u - fr.u0) * fr.s, fr.pad + (fr.v1 - v) * fr.s];
}

/** A plan point (px) back in the room's metres (x, y). */
export function fromPlan(fr: PlanFrame, px: number, py: number): [number, number] {
  const { f, r } = FRONT_AXES[fr.front];
  const u = (px - fr.pad) / fr.s + fr.u0;
  const v = fr.v1 - (py - fr.pad) / fr.s;
  return [u * r[0] + v * f[0], u * r[1] + v * f[1]];
}

/** The side strip (looking at the front from behind the room's left-right axis): x px as the plan's, z up. */
export function toSide(fr: PlanFrame, x: number, y: number, z: number): [number, number] {
  return [toPlan(fr, x, y)[0], fr.pad + (fr.size[2] - z) * fr.s];
}

export function fromSide(fr: PlanFrame, py: number): number {
  return fr.size[2] - (py - fr.pad) / fr.s;
}

/** Where a compass wall sits on the plan: "top" | "right" | "bottom" | "left". */
export function wallSide(fr: PlanFrame, wall: Front): "top" | "right" | "bottom" | "left" {
  const d: Record<Front, [number, number]> = { north: [0, 1], east: [1, 0], south: [0, -1], west: [-1, 0] };
  const { f, r } = FRONT_AXES[fr.front];
  const [dx, dy] = d[wall];
  if (dx * f[0] + dy * f[1] > 0.5) return "top";
  if (dx * f[0] + dy * f[1] < -0.5) return "bottom";
  return dx * r[0] + dy * r[1] > 0 ? "right" : "left";
}

export function clampToRoom(size: Vec3, x: number, y: number): [number, number] {
  return [Math.min(size[0], Math.max(0, x)), Math.min(size[1], Math.max(0, y))];
}

/** A screen dragged on the plan sits on the nearest wall, facing into the room. */
export function snapToWall(size: Vec3, x: number, y: number): { x: number; y: number; wall: Front; normal: Vec3 } {
  const [cx, cy] = clampToRoom(size, x, y);
  const d: [Front, number][] = [
    ["west", cx],
    ["east", size[0] - cx],
    ["south", cy],
    ["north", size[1] - cy],
  ];
  const wall = d.reduce((a, b) => (b[1] < a[1] ? b : a))[0];
  if (wall === "west") return { x: 0, y: cy, wall, normal: [1, 0, 0] };
  if (wall === "east") return { x: size[0], y: cy, wall, normal: [-1, 0, 0] };
  if (wall === "south") return { x: cx, y: 0, wall, normal: [0, 1, 0] };
  return { x: cx, y: size[1], wall, normal: [0, -1, 0] };
}

/** Something on the Room map: every light and screen the rig knows (screens too, unlike rowsFromRig). */
export interface Placeable {
  id: string;
  label: string;
  kind: "screen" | "mover" | "lamp";
  zone: string | null;
  /** A screen fixture's screen (publish-destinations id, e.g. "north-screen"), for its name. */
  screen: string | null;
}

export function placeablesFromRig(rig: unknown): Placeable[] {
  if (!isObj(rig)) return [];
  const raw = rig.fixtures;
  const rv = isObj(rig.registry_view) && isObj(rig.registry_view.fixtures) ? (rig.registry_view.fixtures as Obj) : {};
  const list: Obj[] = Array.isArray(raw) ? raw.filter(isObj) : isObj(raw) ? Object.entries(raw).flatMap(([id, v]) => (isObj(v) ? [{ id, ...v }] : [])) : [];
  return list.flatMap((o) => {
    const id = str(o.id);
    if (!id) return [];
    const caps = parseCaps(o.caps);
    const kind = str(o.kind) === "screen" ? "screen" : caps?.moves ? "mover" : "lamp";
    const reg = isObj(rv[id]) ? (rv[id] as Obj) : null;
    return [{ id, label: str(o.label) ?? id, kind, zone: str(o.zone), screen: str(o.screen) ?? (reg ? str(reg.screen) : null) }];
  });
}

// -- the room around a light: its name, where it is, what it does, which modes use it ------------------------------------

/** One of the site's rooms (room_service GET /api/v1/status `rooms`, from rooms.json). */
export interface SiteRoomLite {
  id: string;
  name: string;
  /** Hue lamps by fixture id ("hue.ne"); "light" names every DMX light, "light.<id>" one. */
  lights: string[];
}

/** Whether a room's lights list holds this light. */
export function roomHasLight(lights: string[], id: string, dmx: boolean): boolean {
  if (lights.includes(id)) return true;
  return dmx && (lights.includes("light") || lights.includes(`light.${id}`));
}

/** The room a light is in (the first that lists it), or null. */
export function roomOfLight<R extends SiteRoomLite>(rooms: R[], id: string, dmx: boolean): R | null {
  return rooms.find((r) => roomHasLight(r.lights ?? [], id, dmx)) ?? null;
}

/** A light's name without its room's ("Drawing room NE" in the Drawing room is "NE"); the whole name when that would
 *  leave nothing. The room's name comes from rooms.json, never from code. */
export function shortName(label: string, roomName?: string | null): string {
  const t = label.trim();
  const r = roomName?.trim();
  if (r && t.length > r.length && t.toLowerCase().startsWith(r.toLowerCase())) {
    const rest = t.slice(r.length);
    if (/^[\s:·,–-]/.test(rest)) {
      const left = rest.replace(/^[\s:·,–-]+/, "").trim();
      if (left) return left;
    }
  }
  return t;
}

/** "Hue lamp" / "DMX light": the kind tag on a light's row. */
export function kindWords(r: { hue: boolean; dmx: boolean }): string | null {
  return r.hue ? "Hue lamp" : r.dmx ? "DMX light" : null;
}

/** A screen's name inside a sentence: "North screen" reads "north screen" ("TV 2" stays as it is). */
export function inSentence(name: string): string {
  return /^[A-Z][a-z]/.test(name) ? name[0].toLowerCase() + name.slice(1) : name;
}

/** A screen on the map, by its name (status `screens[id].name`). */
export interface ScreenSpot {
  name: string;
  at: Vec3;
}

const NEAR_WALL_M = 1.2;
const BESIDE_M = 1.5;
const OPPOSITE: Record<Front, Front> = { north: "south", south: "north", east: "west", west: "east" };

/** Where a light is in words, from where it sits on the Room map: "Above the north screen", "Opposite the south
 *  screen", "In the north-east corner", "On the east wall", "Near the middle of the room". Null when the room has no
 *  size yet. */
export function mapWhereWords(at: Vec3, size: Vec3 | null, screens: ScreenSpot[]): string | null {
  if (!size) return null;
  const [x, y, z] = at;
  const dist: Record<Front, number> = { west: x, east: size[0] - x, south: y, north: size[1] - y };
  const wall = snapToWall(size, x, y).wall;
  const along = (w: Front, p: Vec3) => (w === "west" || w === "east" ? p[1] : p[0]);
  if (dist[wall] <= NEAR_WALL_M) {
    let best: { words: string; d: number } | null = null;
    for (const s of screens) {
      const sw = snapToWall(size, s.at[0], s.at[1]).wall;
      const d = Math.abs(along(sw, at) - along(sw, s.at));
      if (d > BESIDE_M || (best && d >= best.d)) continue;
      const name = inSentence(s.name);
      if (sw === wall) {
        const words = z > s.at[2] + 0.3 ? `Above the ${name}` : z < s.at[2] - 0.3 ? `Below the ${name}` : `Beside the ${name}`;
        best = { words, d };
      } else if (OPPOSITE[sw] === wall) {
        best = { words: `Opposite the ${name}`, d };
      }
    }
    if (best) return best.words;
  }
  const near = (Object.keys(dist) as Front[]).filter((w) => dist[w] <= NEAR_WALL_M);
  const ns = near.find((w) => w === "north" || w === "south");
  const ew = near.find((w) => w === "east" || w === "west");
  if (ns && ew) return `In the ${ns}-${ew} corner`;
  if (near.length) return `On the ${near.sort((a, b) => dist[a] - dist[b])[0]} wall`;
  return "Near the middle of the room";
}

/** Where a light is: from the Room map when it's placed there, else its placement words. */
export function whereWords(
  id: string,
  space: RoomSpace | null,
  screens: ScreenSpot[],
  placement: string | null,
  zone: string | null,
): string | null {
  const at = space?.appliances[id]?.at ?? null;
  return (at && mapWhereWords(at, space?.size ?? null, screens)) || placementWords(placement, zone);
}

/** The screens placed on the map, by name: each screen fixture's screen name (status), else its own label. */
export function screenSpots(space: RoomSpace | null, things: Placeable[], names: Record<string, string>): ScreenSpot[] {
  if (!space) return [];
  return things.flatMap((t) => {
    const at = space.appliances[t.id]?.at;
    if (t.kind !== "screen" || !at) return [];
    return [{ name: (t.screen && names[t.screen]) || t.label, at }];
  });
}

/** A reason a light couldn't identify, fit to show: never a file or config path. */
export function identifyWhy(raw: string | null | undefined): string {
  const w = raw?.trim();
  if (!w || /[/\\]|\.(ya?ml|json|py)\b|\b[a-z_]+\.[a-z_]+\.[a-z_]+\b/i.test(w)) return "This light can't flash right now.";
  return `This light can't flash right now: ${w}`;
}

/** room_service's fixture status -> the pill's tone (as the Room page shows a Hue lamp). */
export const FIXTURE_STATUS_TONE: Record<string, Tone> = {
  following: "live",
  armed: "info",
  house: "muted",
  off: "muted",
  taken_over: "warn",
  error: "bad",
  unreachable: "bad",
  unavailable: "bad",
};

/** A Hue lamp as room_service's fixtures view has it (GET /api/v1/fixtures), the parts these helpers read. */
export interface FixtureLite {
  status?: string;
  status_text?: string;
  dmx?: Record<string, unknown> | null;
  dmx_hex?: string | null;
  light?: { on?: boolean; bri?: number; hex?: string | null } | null;
}

/** The lamp's status pill from room_service ("Off", "Following the music", ...). */
export function fixtureStatus(f: FixtureLite): StatusWords | null {
  if (!f.status_text) return null;
  return { tone: FIXTURE_STATUS_TONE[f.status ?? ""] ?? "muted", text: f.status_text, pulse: f.status === "following" };
}

/** What the lamp shows now: the show's colour while following, else the bridge's reading (dark: none). */
export function fixtureSwatch(f: FixtureLite): { hex: string | null; dim: number } {
  if (f.status === "following") {
    const level = typeof f.dmx?.level === "number" ? (f.dmx.level as number) : 1;
    return { hex: f.dmx_hex ?? null, dim: level };
  }
  const l = f.light;
  if (!l || !l.on || !l.hex) return { hex: null, dim: 0 };
  return { hex: l.hex, dim: Math.max(0.25, (l.bri ?? 100) / 100) };
}

/** Quick colours for a light at a steady colour. */
export const COLOUR_PRESETS: { name: string; hex: string }[] = [
  { name: "Warm", hex: "#ffc367" },
  { name: "Soft white", hex: "#ffe2b8" },
  { name: "Daylight", hex: "#f4f6ff" },
  { name: "Amber", hex: "#ff9a2e" },
  { name: "Red", hex: "#ff2a1a" },
  { name: "Pink", hex: "#ff4fa0" },
  { name: "Purple", hex: "#8a3dff" },
  { name: "Blue", hex: "#2f6bff" },
  { name: "Teal", hex: "#17c3b2" },
  { name: "Green", hex: "#3ddc5a" },
];

/** The modes as room_service's describe resolves them (GET /api/v1/room/describe `modes`), the parts read here. */
export type DescribeModes = Record<
  string,
  {
    label?: string;
    abstract?: boolean;
    order?: number;
    entries?: Record<string, { value?: unknown; words?: string; selector?: string }>;
  }
>;

export interface ModeUse {
  mode: string;
  label: string;
  /** What the mode does with it, in room_service's words ("Follows the music"). */
  words: string;
  follows: boolean;
}

/** Which modes set this light, and to what: its own entry (hue.<id>, light.<id>), else an entry for its kind or its
 *  room's lights (a light added since the room last read the modes). */
export function lightInModes(modes: DescribeModes | null | undefined, target: string, kinds: string[]): ModeUse[] {
  if (!modes) return [];
  return Object.entries(modes)
    .filter(([, m]) => !m.abstract)
    .sort(([, a], [, b]) => (a.order ?? 0) - (b.order ?? 0))
    .flatMap(([mode, m]) => {
      const entries = m.entries ?? {};
      const e = entries[target] ?? Object.values(entries).find((x) => !!x.selector && kinds.includes(x.selector));
      if (!e) return [];
      const follows = e.value === "dmx" || e.value === "show";
      const words = e.words ?? (follows ? "Follows the music" : e.value === "off" ? "Off" : "Set");
      return [{ mode, label: m.label ?? mode, words, follows }];
    });
}

/** The modes, grouped by what they do with the light: [["Follows the music", ["Party", "Disco"]], ...]. */
export function modeUseLines(uses: ModeUse[]): [string, string[]][] {
  const out = new Map<string, string[]>();
  for (const u of uses) out.set(u.words, [...(out.get(u.words) ?? []), u.label]);
  return [...out.entries()];
}

/** "Party, Disco and Ambient". */
export function andList(items: string[]): string {
  return items.length <= 1 ? (items[0] ?? "") : `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}
