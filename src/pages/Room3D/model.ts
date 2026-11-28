// The room as configured, for the 3D room (/lights/room): show_service's rig (every fixture, its type file, its
// address, universe and pan/tilt range; GET /show/api/v1/rig) and its room model (the size, the front, where each
// appliance is, a screen's plane, a mover's pose, the named points; GET /show/api/v1/rig/space), read through
// room_service's /show pass-through. Read every few seconds, so an edit to the deployment's lights/ files shows.
//
// Coordinates stay in the room's own frame (metres: x east, y north, z up, from the south-west floor corner); the
// scene maps them once.

import { partOf, type PartKind } from "./decode";

export type Vec3 = [number, number, number];

export interface Pose {
  at: Vec3;
  rot: Vec3;                // rotation vector: fixture frame -> room (space.py, docs/LIGHTS.md §3.6)
  tilt_offset_deg: number;
  pan_sign: number;
  tilt_sign: number;
}

export interface Fixture {
  id: string;
  label: string;
  kind: string;             // lamp | screen | ...
  driver: string | null;    // artnet | hue | showbus | ...
  part: PartKind | null;    // a known DMX type (decode.partOf), else null
  universe: number | null;
  address: number | null;
  panRange: number;         // degrees, the whole range (type caps), for a mover
  tiltRange: number;
  screen: string | null;    // a screen fixture's screen id
  lastRgb: string | null;   // show_service's last colour for it ("#rrggbb"): Hue lamps (no wire to read)
  at: Vec3 | null;
  pose: Pose | null;
  plane: { normal: Vec3; up: Vec3; size: [number, number] } | null;
  aim: Vec3 | null;         // the place in the room a fixed light points at: a laser's from its tilt (the Lights
                            // page's setup lean_deg, else the 45-degree rule), else space.yaml aim
  aimDir: Vec3 | null;      // which way a fixed light on a bar points (a unit vector): its own tilt and turn (the
                            // Lights page: setup lean_deg, turn_deg), else its bar layout's, from the bar's front
  bar: string;              // the id before its last dot ("gigbar-a" for "gigbar-a.par1"): parts of one unit
  /** PLANNED (owner, 27 Nov: "not all of these objects are 'connected' (i.e. real)"): placed in the room but no light
   *  of this id in the rig: drawn, driving nothing (its channels are the view's own: ``address`` is a virtual slot). */
  planned?: boolean;
  /** A room device's kind (screen, hue-lamp, mirror-ball, mic, light-sensor) for a planned thing that isn't a light. */
  device?: string | null;
  /** What a planned device stands for, once connected (a screen, a lamp, a source, a sensor). */
  connect?: string | null;
}

export interface Point {
  name: string;
  label: string;
  at: Vec3;
  device: string | null;
}

export interface RoomModel {
  size: Vec3;
  front: string;
  frontVec: Vec3;
  centre: Vec3;
  fixtures: Fixture[];
  points: Point[];
  /** The light bars as things that can be moved (space bars): where each stands, its height, its facing, its parts. */
  bars: Bar[];
  /** Boxes no beam should cross (space.yaml keep_out, the Lights setup's 3D tab). */
  keepOut: KeepRegion[];
  /** Each fixed laser's tilt checked against them (show_service space.laser_checks). */
  lasers: Record<string, LaserCheck>;
  /** The light groups a region can be for (show.yaml lights.groups). */
  lightGroups: Record<string, { label: string; members: string[] }>;
  revision: string;
  problems: string[];
}

export interface BarPart { part: string; id: string; head: boolean; lean_deg: number | null; turn_deg: number | null }
export interface Bar { id: string; label: string; at: Vec3; facing_deg: number; front: Vec3; parts: BarPart[]; editable: boolean }

/** GET /rig/space's bars, read tolerantly. */
export function barsOf(space: any): Bar[] {
  return Object.entries(space?.bars ?? {}).flatMap(([id, b]: [string, any]) =>
    isVec3(b?.at) && isVec3(b?.front)
      ? [{ id, label: typeof b.label === "string" ? b.label : id, at: b.at as Vec3, facing_deg: Number(b.facing_deg) || 0,
           front: b.front as Vec3, editable: b.editable !== false,
           parts: Object.entries(b.parts ?? {}).map(([part, p]: [string, any]) => ({ part, id: String(p?.id ?? `${id}.${part}`),
             head: p?.head === true, lean_deg: typeof p?.lean_deg === "number" ? p.lean_deg : null,
             turn_deg: typeof p?.turn_deg === "number" ? p.turn_deg : null })) }]
      : []);
}

/** Which way a fixed light points: ``lean`` degrees forward from straight up (90: level), towards its bar's ``front``
 *  turned ``turn`` degrees to the right (show_service space.aim_dir, the same sum). */
export function aimDirOf(front: Vec3, lean: number, turn: number): Vec3 {
  const h = Math.hypot(front[0], front[1]) || 1, fx = front[0] / h, fy = front[1] / h, ax = -fy, ay = fx;
  const t = (turn * Math.PI) / 180, a = (lean * Math.PI) / 180;
  const hx = fx * Math.cos(t) + ax * Math.sin(t), hy = fy * Math.cos(t) + ay * Math.sin(t);
  return [hx * Math.sin(a), hy * Math.sin(a), Math.cos(a)];
}

export interface KeepRegion { name: string; label: string; centre: Vec3; size: Vec3; groups: string[]; editable: boolean }
export interface LaserCheck { lean_deg: number; crosses: string[]; clear_lean_deg: number | null }

/** GET /rig/space's keep-out regions, laser checks and light groups, read tolerantly. */
export function keepOutOf(space: any): Pick<RoomModel, "keepOut" | "lasers" | "lightGroups"> {
  const keepOut: KeepRegion[] = Object.entries(space?.keep_out ?? {}).flatMap(([name, e]: [string, any]) =>
    isVec3(e?.centre) && isVec3(e?.size)
      ? [{ name, label: typeof e.label === "string" ? e.label : name, centre: e.centre, size: e.size,
           groups: Array.isArray(e.groups) ? e.groups.filter((g: unknown) => typeof g === "string") : [],
           editable: e.editable !== false }]
      : []);
  const lasers: Record<string, LaserCheck> = {};
  for (const [id, c] of Object.entries(space?.lasers ?? {}) as [string, any][]) {
    if (typeof c?.lean_deg === "number") {
      lasers[id] = { lean_deg: c.lean_deg, crosses: Array.isArray(c.crosses) ? c.crosses : [],
        clear_lean_deg: typeof c.clear_lean_deg === "number" ? c.clear_lean_deg : null };
    }
  }
  const lightGroups: Record<string, { label: string; members: string[] }> = {};
  for (const [g, e] of Object.entries(space?.light_groups ?? {}) as [string, any][]) {
    lightGroups[g] = { label: typeof e?.label === "string" ? e.label : g, members: Array.isArray(e?.members) ? e.members : [] };
  }
  return { keepOut, lasers, lightGroups };
}

const isVec3 = (v: unknown): v is Vec3 => Array.isArray(v) && v.length === 3 && v.every((x) => typeof x === "number");

async function getJson(url: string): Promise<any> {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}

/** The room as show_service has it now. ``room``: room_service's base URL (resolveRoomUrl). */
export async function loadRoom(room: string): Promise<RoomModel> {
  const base = room.replace(/\/+$/, "");
  const [rig, space] = await Promise.all([getJson(`${base}/show/api/v1/rig`), getJson(`${base}/show/api/v1/rig/space`)]);
  const view = rig?.registry_view ?? {};
  const types: Record<string, any> = view.types ?? {};
  const cfg: Record<string, any> = view.fixtures ?? {};
  const rows: any[] = Array.isArray(rig?.fixtures) ? rig.fixtures : [];
  const apps: Record<string, any> = space?.appliances ?? {};
  const rs = space?.room ?? {};
  const rsize: Vec3 = isVec3(rs.size) ? rs.size : [6, 6, 3];
  const mid: Vec3 = isVec3(rs.centre) ? rs.centre : [rsize[0] / 2, rsize[1] / 2, rsize[2] / 2];
  /** Where a laser points, from how it is tilted (the Lights page's setup lean_deg: degrees forward, towards the
   *  middle of the room, from straight up). Not set: the tilt that keeps its most forward beam 45 degrees off
   *  vertical (owner, 27 Nov: clear of a mirror ball in the middle of the room). */
  const leanAim = (at: Vec3, spread: number, lean: number | null): Vec3 => {
    const deg = lean ?? 45 - spread / 2, a = (deg * Math.PI) / 180;
    const dx = mid[0] - at[0], dy = mid[1] - at[1], h = Math.hypot(dx, dy) || 1;
    return [at[0] + (dx / h) * Math.sin(a), at[1] + (dy / h) * Math.sin(a), at[2] + Math.cos(a)];
  };
  const bars = barsOf(space);
  const onBar = new Map<string, { front: Vec3; lean: number | null; turn: number | null }>();
  for (const b of bars) for (const p of b.parts) onBar.set(p.id, { front: b.front, lean: p.lean_deg, turn: p.turn_deg });
  /** How each bar part points as the room view says (its setup, else the layout's, a laser the rule): what a planned
   *  part, which has no light row yet, is drawn by. */
  const partAim = new Map<string, { lean_deg: number; turn_deg: number }>();
  for (const b of Object.values(space?.bars ?? {}) as any[])
    for (const p of Object.values(b?.parts ?? {}) as any[])
      if (typeof p?.id === "string" && typeof p?.aim?.lean_deg === "number")
        partAim.set(p.id, { lean_deg: p.aim.lean_deg, turn_deg: Number(p.aim.turn_deg) || 0 });
  /** A fixed light's direction from its bar: its own tilt and turn, else the layout's; a laser with neither, the
   *  45-degree rule. Null: not on a bar, or nothing says how it points (the view's own default then). */
  const dirOnBar = (row: any): Vec3 | null => {
    const b = onBar.get(row.id);
    if (!b) return null;
    const spread = typeof row.spread_deg === "number" ? row.spread_deg : null;
    const lean = typeof row.setup?.lean_deg === "number" ? row.setup.lean_deg : b.lean ?? (spread !== null ? 45 - spread / 2 : null);
    if (lean === null) return null;
    return aimDirOf(b.front, lean, typeof row.setup?.turn_deg === "number" ? row.setup.turn_deg : b.turn ?? 0);
  };
  const fixtures: Fixture[] = rows.map((row) => {
    const c = cfg[row.id] ?? {};
    const t = types[row.type] ?? {};
    const a = apps[row.id] ?? {};
    const pose = a.pose && isVec3(a.pose.at) && isVec3(a.pose.rot) ? (a.pose as Pose) : null;
    const pl = a.plane && isVec3(a.plane.normal) && isVec3(a.plane.up) && Array.isArray(a.plane.size) ? a.plane : null;
    const caps = row.caps ?? {};
    const span = (c: any, d: number) => (c && typeof c.min === "number" ? c.max - c.min : d);
    return {
      id: row.id,
      label: row.label ?? row.id,
      kind: row.kind ?? "lamp",
      driver: row.driver ?? null,
      part: partOf(t.file, t.mode, row.type),
      universe: typeof row.universe === "number" ? row.universe : null,
      address: typeof row.address === "number" ? row.address : null,
      panRange: span(caps.pan, t.physical?.pan_deg ?? 540),
      tiltRange: span(caps.tilt, t.physical?.tilt_deg ?? 270),
      screen: typeof c.screen === "string" ? c.screen : null,
      lastRgb: typeof row.last_rgb === "string" ? row.last_rgb : null,
      at: isVec3(a.at) ? a.at : pose ? pose.at : null,
      pose,
      plane: pl ? { normal: pl.normal, up: pl.up, size: [pl.size[0], pl.size[1]] } : null,
      aimDir: dirOnBar(row),
      aim: (() => {
        const at = isVec3(a.at) ? (a.at as Vec3) : null;
        const d = dirOnBar(row);
        if (at && d) return [at[0] + d[0], at[1] + d[1], at[2] + d[2]] as Vec3;   // on a bar: from the bar's front
        const lean = typeof row.setup?.lean_deg === "number" ? row.setup.lean_deg : null;
        const spread = typeof row.spread_deg === "number" ? row.spread_deg : null;
        if (at && spread !== null && (lean !== null || !isVec3(a.aim))) return leanAim(at, spread, lean);
        return isVec3(a.aim) ? a.aim : null;
      })(),
      bar: row.id.includes(".") ? row.id.slice(0, row.id.lastIndexOf(".")) : row.id,
    };
  });
  // planned things: appliances with a kind and no light of that id, and a bar's parts with no light yet (from its
  // layout's part types). A light type is drawn as that light; a room device as itself. Their channels are the
  // view's own virtual slots (from 1, in order), so selecting one can light it up.
  const known = new Set(rows.map((r) => String(r.id)));
  let vslot = 1;
  const plannedLight = (id: string, typeId: string, label: string, a: any, barId: string): Fixture | null => {
    const t = types[typeId];
    if (!t) return null;
    const fp = Number(t.footprint) || 1, address = vslot;
    vslot += fp;
    const pose = a?.pose && isVec3(a.pose.at) && isVec3(a.pose.rot) ? (a.pose as Pose) : null;
    return { id, label, kind: "lamp", driver: null, part: partOf(t.file, t.mode, typeId), universe: null, address,
      panRange: t.physical?.pan_deg ?? 540, tiltRange: t.physical?.tilt_deg ?? 270, screen: null, lastRgb: null,
      at: isVec3(a?.at) ? a.at : pose ? pose.at : null, pose, plane: null, aimDir: dirOnBar({ id, setup: partAim.get(id) ?? {} }), aim: isVec3(a?.aim) ? a.aim : null,
      bar: barId, planned: true, device: null, connect: null };
  };
  const layouts: Record<string, any> = space?.layouts ?? {};
  for (const b of bars) {
    const parts = layouts[(space?.bars ?? {})[b.id]?.layout]?.parts ?? {};
    for (const p of b.parts) {
      if (known.has(p.id)) continue;
      const pt = parts[p.part]?.type;
      const f = typeof pt === "string" ? plannedLight(p.id, pt, `${b.label} ${p.part.replace(/(\d+)$/, " $1")}`, apps[p.id], b.id) : null;
      if (f) fixtures.push(f);
    }
  }
  for (const [id, a] of Object.entries(apps) as [string, any][]) {
    if (known.has(id) || typeof a?.kind !== "string" || !isVec3(a.at) || fixtures.some((f) => f.id === id)) continue;
    const label = typeof a.label === "string" ? a.label : id;
    if (types[a.kind]) {
      const f = plannedLight(id, a.kind, label, a, id);
      if (f) fixtures.push(f);
      continue;
    }
    const pl = a.plane && isVec3(a.plane.normal) && isVec3(a.plane.up) && Array.isArray(a.plane.size) ? a.plane : null;
    fixtures.push({ id, label, kind: a.kind === "screen" ? "screen" : "device", driver: null, part: null, universe: null,
      address: null, panRange: 0, tiltRange: 0, screen: null, lastRgb: null, at: a.at, pose: null,
      plane: pl ? { normal: pl.normal, up: pl.up, size: [pl.size[0], pl.size[1]] } : null, aimDir: null, aim: isVec3(a.aim) ? a.aim : null, bar: id,
      planned: !a.connect, device: a.kind, connect: typeof a.connect === "string" ? a.connect : null });
  }
  const points: Point[] = Object.entries(space?.points ?? {}).flatMap(([name, p]: [string, any]) =>
    isVec3(p?.at) ? [{ name, label: p.label ?? name, at: p.at, device: p.device ?? null }] : []);
  const r = space?.room ?? {};
  const size: Vec3 = isVec3(r.size) ? r.size : [6, 6, 3];
  return {
    size,
    front: r.front ?? "north",
    frontVec: isVec3(r.front_vec) ? r.front_vec : [0, 1, 0],
    centre: isVec3(r.centre) ? r.centre : [size[0] / 2, size[1] / 2, size[2] / 2],
    fixtures,
    points,
    bars,
    ...keepOutOf(space),
    revision: `${rig?.registry?.revision ?? ""}/${space?.revision ?? ""}`,
    problems: [...(rig?.registry?.problems ?? []), ...(space?.problems ?? [])],
  };
}
