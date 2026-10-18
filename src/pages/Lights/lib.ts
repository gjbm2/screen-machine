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
export const FIELD_STEP: Record<DraftField, number> = { type: 1, node_ip: 2, universe: 2, address: 3, label: 4 };

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
