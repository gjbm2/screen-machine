// What each DMX channel of a known fixture type means (the makers' manuals), and the moving parts' physical motion.
// The TypeScript twin of tools/virtual_node/gigbar_model.js's channel half: the same ranges, the same estimates. The
// 3D room reads DMX only from the virtual Art-Net node (what the wire carries), never show_service's intent.
//
// Types are recognised by their fixture-type file (config/room/fixture-types/ofl/...), so a deployment can name its
// types anything: chauvet-dj/gigbar-move-ils-{par,derby,flash,laser,spot}.json and antari/z-350.json.

export type PartKind = "par" | "derby" | "flash" | "laser" | "laser50" | "spot" | "hazer" | "dimmer";

/** The part a fixture type is, from its file (and, for the laser, its mode), or null for a type this view can't draw. */
export function partOf(file: string | null | undefined, mode?: string | null, type?: string | null): PartKind | null {
  if (type === "generic-dimmer") return "dimmer";             // a house light: one dimmer channel
  if (!file) return null;
  const m = /gigbar-move-ils-(par|derby|flash|laser|spot)\.json$/.exec(file);
  if (m) return m[1] === "laser" && mode === "part-50ch" ? "laser50" : (m[1] as PartKind);
  if (/antari\/z-350\.json$/.test(file)) return "hazer";
  return null;
}

// Mechanical speeds: NOT in the manuals. Estimates for a budget LED mover and its effects.
export const MECH = { panDps: 220, tiltDps: 150, accel: 900, wheelSlotsPerS: 9, goboSlotsPerS: 8, derbyRps: 1.2,
  laserRps: 0.8, hazeWarmupS: 270 };

export const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
const inv = (v: number, lo: number, hi: number) => (v - lo) / Math.max(1, hi - lo);
export const pct = (v: number) => `${Math.round(v / 2.55)}%`;
export function hash(n: number): number {
  const x = Math.sin(n * 127.1 + 311.7) * 43758.5453;
  return x - Math.floor(x);
}

export type Shutter = { mode: "open" | "closed" | "strobe" | "pulse" | "random" | "sound"; hz?: number };
/** Par / derby / strobe-zone strobe: 000-250 slow to fast, 251-255 to sound (0-5 read as no strobe: GIGBAR_RIG §4). */
export function barStrobe(v: number): Shutter {
  if (v <= 5) return { mode: "open" };
  if (v <= 250) return { mode: "strobe", hz: lerp(1, 30, inv(v, 6, 250)) };
  return { mode: "sound" };
}
/** Spot shutter: 0-3 closed, 4-7 open, 8-76 strobe, 77-145 pulse, 146-215 random, 216-255 open. */
export function spotShutter(v: number): Shutter {
  if (v <= 3) return { mode: "closed" };
  if (v <= 7 || v >= 216) return { mode: "open" };
  if (v <= 76) return { mode: "strobe", hz: lerp(1, 30, inv(v, 8, 76)) };
  if (v <= 145) return { mode: "pulse", hz: lerp(1, 30, inv(v, 77, 145)) };
  return { mode: "random", hz: lerp(1, 30, inv(v, 146, 215)) };
}
export function shutterGain(s: Shutter, t: number, seed: number): number {
  const hz = s.hz ?? 1;
  switch (s.mode) {
    case "closed": return 0;
    case "open": return 1;
    case "strobe": return (t * hz) % 1 < 0.18 ? 1 : 0;
    case "pulse": return 1 - ((t * hz) % 1);
    case "random": {
      const k = Math.floor(t * hz * 1.3);
      return hash(k * 7 + seed) < 0.45 && (t * hz * 1.3) % 1 < 0.25 ? 1 : 0;
    }
    case "sound": return (t * 2.1) % 1 < 0.12 ? 1 : 0;      // strobe to sound: the bar's own mic (not modelled)
  }
}
export function shutterWords(s: Shutter): string {
  if (s.mode === "open") return "steady";
  if (s.mode === "closed") return "shutter closed";
  if (s.mode === "sound") return "strobing to sound";
  const w = s.mode === "strobe" ? "strobing" : s.mode === "pulse" ? "pulsing" : "random strobe";
  return `${w} ${(s.hz ?? 0).toFixed(0)} Hz`;
}
/** Derby / laser motor: 0 stop, 1-127 CW slow to fast, 128 stop, 129-255 CCW (revolutions a second). */
export function motor(v: number, rps: number): number {
  if (v === 0 || v === 128) return 0;
  return v < 128 ? lerp(0.03, rps, inv(v, 1, 127)) : -lerp(0.03, rps, inv(v, 129, 255));
}
export function motorWords(rps: number): string {
  if (!rps) return "not turning";
  const a = Math.abs(rps);
  return `turning ${rps > 0 ? "clockwise" : "anticlockwise"} ${a < 0.25 ? "slowly" : a < 0.7 ? "at medium speed" : "fast"}`;
}

export const COLOUR_SLOTS: [string, [number, number, number]][] = [["white", [255, 255, 255]], ["red", [255, 32, 32]],
  ["orange", [255, 138, 26]], ["yellow", [255, 232, 26]], ["green", [34, 255, 68]], ["blue", [42, 75, 255]],
  ["CTO (warm)", [255, 197, 143]], ["cyan", [26, 232, 255]], ["magenta", [255, 42, 255]], ["lime", [191, 255, 26]]];
const COLOUR_RANGES = [[0, 6], [7, 13], [14, 20], [21, 27], [28, 34], [35, 41], [42, 48], [49, 55], [56, 62], [63, 64]];
function colourWheel(v: number): { pos?: number; spin?: number } {
  for (let i = 0; i < COLOUR_RANGES.length; i++) if (v <= COLOUR_RANGES[i][1]) return { pos: i };
  if (v <= 189) return { pos: inv(v, 65, 189) * 10 };
  if (v <= 221) return { spin: lerp(MECH.wheelSlotsPerS, 0.3, inv(v, 190, 221)) };
  if (v <= 223) return { spin: 0 };
  return { spin: -lerp(0.3, MECH.wheelSlotsPerS, inv(v, 224, 255)) };
}
function goboWheel(v: number): { slot?: number; spin?: number; shake?: number } {
  if (v <= 53) return { slot: Math.floor(v / 6) };
  if (v <= 63) return { slot: 9 };
  if (v <= 117) return { slot: 9 - Math.floor((v - 64) / 6), shake: lerp(1, 8, ((v - 64) % 6) / 5) };
  if (v <= 127) return { slot: 0 };
  if (v <= 189) return { spin: lerp(0.2, MECH.goboSlotsPerS, inv(v, 128, 189)) };
  if (v <= 193) return { spin: 0 };
  return { spin: -lerp(0.2, MECH.goboSlotsPerS, inv(v, 194, 255)) };
}
const LASER_V2: [number, string | null][] = [[5, null], [41, "red"], [77, "green"], [113, "blue"], [149, "red + green"],
  [185, "red + blue"], [221, "green + blue"], [255, "red + green + blue"]];
const LASER_V1: [number, string | null][] = [[0, null], [36, "image colour"], [71, "red"], [73, null], [107, "green"],
  [110, null], [143, "blue"], [147, null], [179, "red + green"], [184, null], [215, "red + blue"], [221, null],
  [255, "green + blue"]];
export function laserColours(v: number, v1: boolean): string[] | null {
  const w = (v1 ? LASER_V1 : LASER_V2).find(([hi]) => v <= hi)?.[1] ?? null;
  if (!w) return null;
  return w === "image colour" ? ["red", "green", "blue"] : w.split(" + ");
}
export const LASER_RGB: Record<string, [number, number, number]> = { red: [255, 30, 30], green: [30, 255, 60], blue: [60, 80, 255] };
export const DERBY_RGB: [number, number, number][] = [[255, 40, 40], [40, 255, 70], [60, 90, 255], [255, 250, 240]];

/** An RGBAW+UV par's colour (0..255 each) and level (0..1). */
export function mixPar(r: number, g: number, b: number, a: number, w: number, uv: number) {
  const c = [r + a + w + uv * 0.35, g + a * 0.62 + w * 0.95, b + w * 0.88 + uv * 0.95];
  const m = Math.max(1, ...c);
  return { rgb: c.map((x) => (x / m) * 255) as [number, number, number], level: Math.min(1, m / 255) };
}
export function colourName([r, g, b]: number[]): string {
  const mx = Math.max(r, g, b), mn = Math.min(r, g, b);
  if (mx < 1) return "off";
  const s = (mx - mn) / mx;
  let h = 0;
  if (mx !== mn) {
    if (mx === r) h = ((g - b) / (mx - mn)) % 6; else if (mx === g) h = (b - r) / (mx - mn) + 2; else h = (r - g) / (mx - mn) + 4;
    h = (h * 60 + 360) % 360;
  }
  if (s < 0.18) return "white";
  if (s < 0.42 && h >= 15 && h < 60) return "warm white";
  if (s < 0.65 && (h < 15 || h >= 330)) return "pink";
  if (s < 0.65 && h >= 15 && h < 45) return "peach";
  const names: [number, string][] = [[12, "red"], [28, "orange"], [45, "amber"], [65, "yellow"], [95, "lime"], [150, "green"],
    [190, "cyan"], [250, "blue"], [280, "violet"], [320, "magenta"], [345, "pink"], [360, "red"]];
  return names.find(([hi]) => h < hi)?.[1] ?? "red";
}

// ---- a moving head's physical state: pan and tilt travel, the wheels turn (estimates: MECH) ------------------------
export class SpotMotion {
  pan = 0; tilt = 0; vp = 0; vt = 0; wheel = 0; gobo = 0; shake = 0;
  targetPan = 0; targetTilt = 0; wheelSpin: number | undefined; goboSpin: number | undefined;

  /** One step: ``ch(i)`` reads the spot's channel i (0-based from its address); ranges are its type's, centred. */
  step(ch: (i: number) => number, dt: number, panRange: number, tiltRange: number) {
    this.targetPan = ((ch(0) * 256 + ch(1)) / 65535 - 0.5) * panRange;
    this.targetTilt = ((ch(2) * 256 + ch(3)) / 65535 - 0.5) * tiltRange;
    const sp = 1 - (ch(4) / 255) * 0.96;                       // pan/tilt speed: 0 fastest (assumed, §4)
    [this.pan, this.vp] = slew(this.pan, this.vp, this.targetPan, MECH.panDps * sp, dt);
    [this.tilt, this.vt] = slew(this.tilt, this.vt, this.targetTilt, MECH.tiltDps * sp, dt);
    const cw = colourWheel(ch(5));
    this.wheelSpin = cw.spin;
    if (cw.spin !== undefined) this.wheel = (this.wheel + cw.spin * dt + 100) % 10;
    else this.wheel = toward(this.wheel, cw.pos ?? 0, MECH.wheelSlotsPerS * dt);
    const gw = goboWheel(ch(6));
    this.goboSpin = gw.spin;
    this.shake = gw.shake ?? 0;
    if (gw.spin !== undefined) this.gobo = (this.gobo + gw.spin * dt + 100) % 10;
    else this.gobo = toward(this.gobo, gw.slot ?? 0, MECH.goboSlotsPerS * dt);
  }
  colour(): [number, number, number] {
    return COLOUR_SLOTS[Math.round(this.wheel) % 10][1];
  }
  goboSlot(t: number): number {
    return Math.round(this.gobo + (this.shake ? Math.sin(t * this.shake * 6.28) * 0.3 : 0)) % 10;
  }
}
function slew(cur: number, vel: number, target: number, vmax: number, dt: number): [number, number] {
  const err = target - cur;
  const want = Math.max(-vmax, Math.min(vmax, err * 6));
  vel += Math.max(-MECH.accel * dt, Math.min(MECH.accel * dt, want - vel));
  cur += vel * dt;
  if (Math.abs(target - cur) < 0.2 && Math.abs(vel) < 5) return [target, 0];
  return [cur, vel];
}
function toward(cur: number, target: number, maxStep: number): number {
  let d = (target - cur) % 10;
  if (d > 5) d -= 10;
  if (d < -5) d += 10;
  return (cur + Math.max(-maxStep, Math.min(maxStep, d)) + 10) % 10;
}

// ---- the hazer: it heats once DMX arrives (about 4.5 min), hazes on channel 1, sleeps when DMX stops ---------------
export class HazeState {
  firstDmx: number | null = null; lastSeen: number | null = null; density = 0; out = 0; fan = 0.2; ready = false;
  step(haze: number, fan: number, haveDmx: boolean, t: number, dt: number, skipWarmup: boolean) {
    if (haveDmx) { if (this.firstDmx === null) this.firstDmx = t; this.lastSeen = t; }
    else if (this.lastSeen !== null && t - this.lastSeen > 3) this.firstDmx = null;
    this.ready = this.firstDmx !== null && (skipWarmup || t - this.firstDmx >= MECH.hazeWarmupS);
    this.out = this.ready && haze >= 5 ? lerp(0.01, 1, inv(haze, 5, 255)) : 0;
    this.fan = fan <= 4 ? 0.2 : lerp(0.21, 1, inv(fan, 5, 255));
    this.density = Math.min(1, this.density + this.out * (0.02 + 0.03 * this.fan) * dt) * Math.exp(-dt / 150);
  }
  words(t: number): string {
    const s = this.firstDmx === null ? "asleep (no DMX)" : !this.ready
      ? `heating, ${Math.max(0, MECH.hazeWarmupS - (t - this.firstDmx)).toFixed(0)} s to go` : this.out > 0 ? "making haze" : "ready";
    return `${s} · output ${Math.round(this.out * 100)}% · fan ${Math.round(this.fan * 100)}%`;
  }
}
