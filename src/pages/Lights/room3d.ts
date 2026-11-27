// The 3D view's pure geometry (Room3D.tsx draws it): where a beam from inside the room lands (floor, ceiling, a wall
// or a screen's picture), a GigBAR's emitters and beams from its place, and the planning numbers the view prints under
// each bar (how much of its light lands on the screens, how much laser passes through head height).
// Room frame as space.yaml: metres, x east, y north, z up, origin the south-west floor corner.
// The GigBAR 2 layout is Open Fixture Library's chauvet-dj/gigbar-2 (1185 x 387 x 110 mm): on the bar, left to right,
// derby, par, laser, par, derby, with four white/UV strobes along the top row. The beam angles are this view's
// estimates (no maker's figure in OFL): change them here once the bars are in the room.

export type V3 = [number, number, number];

export const GIGBAR_KIND = "gigbar-2";
export const BAR_LENGTH_M = 1.185;
export const PAR_HALF_ANGLE_DEG = 20;
export const DERBY_BEAMS = 12;
/** Derby beamlets sit on two rings round the bar's aim, this far out (degrees). */
export const DERBY_RINGS_DEG: [number, number] = [18, 38];
export const LASER_GRID = 5;
export const LASER_SPREAD_DEG = 24;
/** Where heads are in a room of people standing or sitting (m): a laser here is in someone's eyes. */
export const HEAD_ZONE_M: [number, number] = [1.0, 1.9];

export const add = (a: V3, b: V3): V3 => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
export const sub = (a: V3, b: V3): V3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
export const mul = (a: V3, k: number): V3 => [a[0] * k, a[1] * k, a[2] * k];
export const dot = (a: V3, b: V3): number => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
export const cross = (a: V3, b: V3): V3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
export const len = (a: V3): number => Math.hypot(a[0], a[1], a[2]);
export function unit(a: V3): V3 {
  const l = len(a);
  return l > 1e-9 ? mul(a, 1 / l) : [0, 0, 0];
}
const rad = (d: number) => (d * Math.PI) / 180;
const deg = (r: number) => (r * 180) / Math.PI;

export interface Picture {
  id: string;
  label: string;
  at: V3;
  normal: V3;
  up: V3;
  size: [number, number];
}

export interface Hit {
  at: V3;
  dist: number;
  /** floor | ceiling | west | east | south | north (the room's walls), or a screen's id. */
  surface: string;
  /** The surface's normal, into the room. */
  normal: V3;
}

/** Where a ray from inside a room of `size` meets it (the nearest face), and a screen's picture when it lands on one. */
export function hitRoom(o: V3, d: V3, size: V3, pictures: Picture[] = []): Hit | null {
  let best: Hit | null = null;
  const faces: [number, number, string, V3][] = [
    [0, 0, "west", [1, 0, 0]],
    [0, size[0], "east", [-1, 0, 0]],
    [1, 0, "south", [0, 1, 0]],
    [1, size[1], "north", [0, -1, 0]],
    [2, 0, "floor", [0, 0, 1]],
    [2, size[2], "ceiling", [0, 0, -1]],
  ];
  for (const [axis, at, surface, normal] of faces) {
    const dv = d[axis];
    if (Math.abs(dv) < 1e-9) continue;
    const t = (at - o[axis]) / dv;
    if (t <= 1e-6 || (best && t >= best.dist)) continue;
    best = { at: add(o, mul(d, t)), dist: t, surface, normal };
  }
  if (!best) return null;
  const on = pictureAt(best.at, pictures);
  return on ? { ...best, surface: on } : best;
}

/** The screen whose picture holds point `p` (on or just off its plane), if any. */
export function pictureAt(p: V3, pictures: Picture[]): string | null {
  for (const s of pictures) {
    const v = sub(p, s.at);
    if (Math.abs(dot(v, s.normal)) > 0.2) continue;
    const right = unit(cross(s.up, s.normal));
    if (Math.abs(dot(v, right)) <= s.size[0] / 2 && Math.abs(dot(v, s.up)) <= s.size[1] / 2) return s.id;
  }
  return null;
}

/** A bar's frame from its place: forward (its aim), up (square to it) and right (along the bar). */
export interface BarFrame {
  at: V3;
  fwd: V3;
  up: V3;
  right: V3;
}

export function barFrame(at: V3, normal: V3, up: V3 = [0, 0, 1]): BarFrame {
  const fwd = unit(normal);
  let u = unit(sub(up, mul(fwd, dot(up, fwd))));
  if (len(u) < 0.5) u = unit(sub([0, 1, 0], mul(fwd, fwd[1])));
  return { at, fwd, up: u, right: unit(cross(u, fwd)) };   // right, up, fwd: a right-handed frame (three.js makeBasis)
}

/** Turn (degrees from east, anticlockwise seen from above: 90 = north) and tilt (degrees, negative = down) of an aim. */
export function aimAngles(normal: V3): { turn: number; tilt: number } {
  const n = unit(normal);
  return { turn: Math.round(deg(Math.atan2(n[1], n[0]))), tilt: Math.round(deg(Math.asin(Math.max(-1, Math.min(1, n[2]))))) };
}

export function aimFrom(turn: number, tilt: number): V3 {
  const t = rad(tilt);
  const y = rad(turn);
  return [Math.cos(t) * Math.cos(y), Math.cos(t) * Math.sin(y), Math.sin(t)];
}

/** A direction `polar` degrees off the frame's aim, at `around` degrees round it (0 = towards `right`). */
export function offAxis(f: BarFrame, polar: number, around: number): V3 {
  const p = rad(polar);
  const a = rad(around);
  const side = add(mul(f.right, Math.cos(a)), mul(f.up, Math.sin(a)));
  return unit(add(mul(f.fwd, Math.cos(p)), mul(side, Math.sin(p))));
}

export type Emitter = "derby" | "par" | "laser" | "strobe";

/** The GigBAR 2's emitters along the bar (offsets in metres from its centre: right, up). */
export const GIGBAR_EMITTERS: { kind: Emitter; right: number; up: number; n: number }[] = (() => {
  const col = BAR_LENGTH_M / 7;
  return [
    { kind: "derby", right: -3 * col, up: -0.02, n: 0 },
    { kind: "par", right: -1 * col, up: -0.02, n: 0 },
    { kind: "laser", right: 0, up: -0.02, n: 0 },
    { kind: "par", right: 1 * col, up: -0.02, n: 1 },
    { kind: "derby", right: 3 * col, up: -0.02, n: 1 },
    { kind: "strobe", right: -3 * col, up: 0.035, n: 0 },
    { kind: "strobe", right: -1 * col, up: 0.035, n: 1 },
    { kind: "strobe", right: 1 * col, up: 0.035, n: 2 },
    { kind: "strobe", right: 3 * col, up: 0.035, n: 3 },
  ];
})();

export function emitterAt(f: BarFrame, e: { right: number; up: number }): V3 {
  return add(add(f.at, mul(f.right, e.right)), mul(f.up, e.up));
}

/** One derby's beamlets at spin `spin` degrees (the two derbies turn opposite ways). */
export function derbyDirs(f: BarFrame, spin: number, n: number): V3[] {
  const s = n ? -spin : spin;
  return Array.from({ length: DERBY_BEAMS }, (_, k) =>
    offAxis(f, DERBY_RINGS_DEG[k % 2], (k * 360) / DERBY_BEAMS + s + (k % 2) * (180 / DERBY_BEAMS)),
  );
}

/** The laser's grid of beams, swept by `sway` (-1..1) across and turned by `turn` degrees. */
export function laserDirs(f: BarFrame, sway: number, turn: number): V3[] {
  const out: V3[] = [];
  const step = (2 * LASER_SPREAD_DEG) / (LASER_GRID - 1);
  for (let i = 0; i < LASER_GRID; i++) {
    for (let j = 0; j < LASER_GRID; j++) {
      const x = -LASER_SPREAD_DEG + i * step + sway * 12;
      const y = -LASER_SPREAD_DEG + j * step;
      const polar = Math.hypot(x, y);
      out.push(offAxis(f, polar, deg(Math.atan2(y, x)) + turn));
    }
  }
  return out;
}

/** Evenly spread directions inside a cone round the aim (a par's wash), for sampling. */
export function coneDirs(f: BarFrame, halfAngle: number, rings = 6, perRing = 16): V3[] {
  const out: V3[] = [f.fwd];
  for (let r = 1; r <= rings; r++) {
    const polar = (halfAngle * r) / rings;
    for (let k = 0; k < perRing; k++) out.push(offAxis(f, polar, (k * 360) / perRing + r * 7));
  }
  return out;
}

/** Does a beam from `o` along `d` (to its hit, `dist` m) pass through head height away from the bar? */
export function throughHeads(o: V3, d: V3, dist: number): boolean {
  for (let t = 0.6; t <= dist; t += 0.2) {
    const z = o[2] + d[2] * t;
    if (z >= HEAD_ZONE_M[0] && z <= HEAD_ZONE_M[1]) return true;
  }
  return false;
}

export interface BarReport {
  /** Share of the pars' light that lands on a screen's picture (0..1). */
  parsOnScreens: number;
  /** Share of the derby dots that cross a screen's picture over a full turn (0..1). */
  derbyOnScreens: number;
  /** Share of the laser's beams that pass through head height (0..1). */
  laserAtHeads: number;
  /** Where most of the pars' light lands. */
  parsLand: string | null;
}

/** The planning numbers for a GigBAR at `f` in a room of `size` with these screens. */
export function reportBar(f: BarFrame, size: V3, pictures: Picture[]): BarReport {
  const onScreen = (h: Hit | null) => !!h && pictures.some((p) => p.id === h.surface);
  let parN = 0;
  let parOn = 0;
  const land: Record<string, number> = {};
  for (const e of GIGBAR_EMITTERS.filter((x) => x.kind === "par")) {
    const o = emitterAt(f, e);
    for (const d of coneDirs(f, PAR_HALF_ANGLE_DEG)) {
      const h = hitRoom(o, d, size, pictures);
      parN++;
      if (onScreen(h)) parOn++;
      if (h) land[h.surface] = (land[h.surface] ?? 0) + 1;
    }
  }
  let dN = 0;
  let dOn = 0;
  for (const e of GIGBAR_EMITTERS.filter((x) => x.kind === "derby")) {
    const o = emitterAt(f, e);
    for (let spin = 0; spin < 360; spin += 10) {
      for (const d of derbyDirs(f, spin, e.n)) {
        dN++;
        if (onScreen(hitRoom(o, d, size, pictures))) dOn++;
      }
    }
  }
  let lN = 0;
  let lHeads = 0;
  const lo = emitterAt(f, GIGBAR_EMITTERS.find((x) => x.kind === "laser")!);
  for (const sway of [-1, -0.5, 0, 0.5, 1]) {
    for (const d of laserDirs(f, sway, 0)) {
      const h = hitRoom(lo, d, size, pictures);
      lN++;
      if (h && throughHeads(lo, d, h.dist)) lHeads++;
    }
  }
  const top = Object.entries(land).sort((a, b) => b[1] - a[1])[0];
  return {
    parsOnScreens: parN ? parOn / parN : 0,
    derbyOnScreens: dN ? dOn / dN : 0,
    laserAtHeads: lN ? lHeads / lN : 0,
    parsLand: top ? top[0] : null,
  };
}

/** A surface in words ("the floor", "the east wall", a screen by its name). */
export function surfaceWords(s: string | null, pictures: Picture[]): string {
  if (!s) return "nowhere";
  const p = pictures.find((x) => x.id === s);
  if (p) return `the ${p.label}`;
  if (s === "floor" || s === "ceiling") return `the ${s}`;
  return `the ${s} wall`;
}
