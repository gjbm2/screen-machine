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
  bar: string;              // the id before its last dot ("gigbar-a" for "gigbar-a.par1"): parts of one unit
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
  revision: string;
  problems: string[];
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
      part: partOf(t.file, t.mode),
      universe: typeof row.universe === "number" ? row.universe : null,
      address: typeof row.address === "number" ? row.address : null,
      panRange: span(caps.pan, t.physical?.pan_deg ?? 540),
      tiltRange: span(caps.tilt, t.physical?.tilt_deg ?? 270),
      screen: typeof c.screen === "string" ? c.screen : null,
      lastRgb: typeof row.last_rgb === "string" ? row.last_rgb : null,
      at: isVec3(a.at) ? a.at : pose ? pose.at : null,
      pose,
      plane: pl ? { normal: pl.normal, up: pl.up, size: [pl.size[0], pl.size[1]] } : null,
      bar: row.id.includes(".") ? row.id.slice(0, row.id.lastIndexOf(".")) : row.id,
    };
  });
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
    revision: `${rig?.registry?.revision ?? ""}/${space?.revision ?? ""}`,
    problems: [...(rig?.registry?.problems ?? []), ...(space?.problems ?? [])],
  };
}
