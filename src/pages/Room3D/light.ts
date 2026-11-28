// The light in the 3D room, in real units (owner, 27 Nov: "you're assuming each light individually lands in a dark
// room, whereas lots of lights will increase the ambient ... lasers and derbys will have a little less visual impact
// than you're depicting"; "how audience will experience the room as realistic as possible (without going nuts on
// raytracing)").
//
// How it is worked out (the practice of lighting previsualisers and game renderers, without tracing rays):
//
//   1. ONE PICTURE IN REAL UNITS. Every source's strength is its maker's figure (candela = lux x m2, lumens, laser
//      mW), every surface's brightness comes out in cd/m2, and all of it is ADDED in one linear picture: a beam in the
//      air adds its light to whatever is behind it. Only then is the picture turned into what a screen can show
//      (fog.ts). So a beam that glows at 0.1 cd/m2 is striking over a wall at 0.03 and gone over a wall at 10: nothing
//      is drawn "as if the room were dark". (Lagarde & de Rousiers, "Moving Frostbite to Physically Based Rendering",
//      2014: photometric units end to end, one exposure at the end.)
//   2. LIGHT BOUNCES. Light landing on a wall re-lights the room. A wash's or a mover's pool is given a small light
//      of its own on the wall, shining back into the room (Keller's "instant radiosity", 1997: one virtual light per
//      pool); every later bounce, and every other source's, is an even fill worked out as lighting designers do for a
//      room (the integrating-sphere estimate): fill lux = reflectance x lumens / (surface area x (1 - reflectance)).
//   3. HAZE IS LIT BY THE WHOLE ROOM, not only by the beam passing through it: the lit air is a veil over everything
//      behind it (fog.ts), and a beam is the light its own cone scatters towards you, worked out exactly along each
//      line of sight (no ray marching: the inverse-square integral along a ray has a closed form). Haze throws light
//      mostly forwards, so a beam coming towards you is several times brighter than one seen from behind the bar.
//   4. THE EYE ADAPTS (fog.ts): the picture's exposure follows how bright the view is, quickly when it brightens and
//      slowly when it darkens, and a dark room stays dark rather than being lifted to a mid grey (Krawczyk, Myszkowski
//      & Seidel, "Perceptual effects in real-time tone mapping", 2005). Bright things also throw a little glare
//      across the eye (Spencer et al., 1995).
//
// What is the maker's (chauvetdj.com, GigBAR Move ILS): par 1,205 lux at 2 m, beam 22 / field 33 degrees; moving head
// 5,140 lux at 2 m, beam 17 degrees; strobe 130 lux at 2 m, beam 30 / field 62 degrees; laser diodes 638 nm 30 mW,
// 520 nm 20 mW, 450 nm 50 mW; derby coverage 131 degrees. (The maker's sheet for the GigBAR Move WITHOUT ILS gives its
// moving head 1,056 lux: a fifth. This bar's own page is followed; if the real heads look much weaker than the pars
// suggest, that is the figure to change.) What is an ESTIMATE, to check against the real room: the derby's output
// (the maker gives none, for this bar or its siblings), how the par's six emitters share its output, the house lights and lamps
// (800 lm each), a screen's white (250 cd/m2), the laser beams' width, how forward-throwing the haze is, and the
// room's surfaces (the lighting designer's standard room: ceiling 70 %, walls 50 %, floor 20 % reflective).

import * as THREE from "three";

export type V3 = THREE.Vector3;

/** How much of the light landing on each surface it reflects (0-1). ESTIMATES: the standard 70 / 50 / 20 room. */
export const REFLECT = { ceiling: 0.7, wall: 0.5, floor: 0.2 };

export const PHOTO = {
  par: { cd: 1205 * 4, beam_deg: 22, field_deg: 33 },        // every emitter full, on its axis
  spot: { cd: 5140 * 4, beam_deg: 17 },                      // open white
  strobe: { cd: 130 * 4, beam_deg: 30, field_deg: 62 },      // all four LEDs
  // ESTIMATES (no maker's figure): lumens leaving a derby per colour (two ~1 W LEDs each, half lost in the lens),
  // the beams each colour is split into, and one beam's width.
  derby: { lm: [30, 80, 15, 90], beams: 12, beam_deg: 3.5 },
  laser: { mW: { red: 30, green: 20, blue: 50 } as Record<string, number>,
    // the eye's sensitivity at each diode's wavelength (CIE V: 638, 520, 450 nm); 683 lm per W at its peak
    V: { red: 0.19, green: 0.71, blue: 0.038 } as Record<string, number>,
    aperture_m: 0.003, spread_rad: 0.002 },                  // a beam's width: at the unit, and per metre (the manual: under 2 mrad)
  house_lm: 800,                                             // a ceiling downlight (ESTIMATE)
  lamp_lm: 800,                                              // a Hue lamp at full white (ESTIMATE)
  screen_nits: 250,                                          // a screen's white (ESTIMATE)
};

/** The brightest a lit lens or LED is drawn, cd/m2 (the real ones are far beyond what the picture's numbers hold). */
export const FACE_CAP = 20000;
/** With everything off a room is not black: a little light from elsewhere (lux, bluish). */
export const NIGHT_LUX = new THREE.Vector3(0.06, 0.08, 0.12);

const Y = (r: number, g: number, b: number) => 0.2126 * r + 0.7152 * g + 0.0722 * b;
const lin = (v: number) => { const x = v / 255; return x <= 0.04045 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); };
/** A display colour (0-255) as linear light. */
export function linear(c: readonly number[], out = new THREE.Vector3()): V3 {
  return out.set(lin(c[0]), lin(c[1]), lin(c[2]));
}
/** A display colour as the light of ONE lumen (or candela) of it: a deep blue needs far more "blue" per lumen than a
 *  green does, which is how a saturated light looks brighter than its lumens say. */
export function perLumen(c: readonly number[], out = new THREE.Vector3()): V3 {
  linear(c, out);
  return out.multiplyScalar(1 / Math.max(1e-4, Y(out.x, out.y, out.z)));
}
export const lumens = (v: V3) => Y(v.x, v.y, v.z);

/** The solid angle (sr) a three.js spotlight of this cone fills, weighted by its edge falloff: lumens = cd x this. */
export function coneOmega(angle: number, penumbra: number): number {
  const c0 = Math.cos(angle), c1 = Math.cos(angle * (1 - penumbra)), n = 96;
  let s = 0;
  for (let i = 0; i < n; i++) {
    const u = c0 + ((i + 0.5) / n) * (1 - c0);
    const t = c1 > c0 ? Math.min(1, Math.max(0, (u - c0) / (c1 - c0))) : 1;
    s += t * t * (3 - 2 * t);
  }
  return 2 * Math.PI * (s / n) * (1 - c0);
}

// A par's six emitters: each one's colour, and its share of the par's output with everything on. ESTIMATES: red,
// green and blue together make half of it (in the proportions that mix to white), amber and white most of the rest,
// and UV almost none (it is seen as a dim violet).
const PAR_EMITTERS: { rgb: V3; share: number }[] = [
  { rgb: perLumen([255, 0, 0]), share: 0.106 }, { rgb: perLumen([0, 255, 0]), share: 0.358 },
  { rgb: perLumen([0, 0, 255]), share: 0.036 }, { rgb: perLumen([255, 205, 0]), share: 0.14 },
  { rgb: perLumen([255, 249, 240]), share: 0.34 }, { rgb: perLumen([90, 0, 250]), share: 0.02 },
];
/** A par's light on its axis (candela, as linear red, green, blue) for its six channels (0-255: R G B A W UV). LEDs
 *  dim in proportion to their channel. */
export function parCd(v: number[], out = new THREE.Vector3()): V3 {
  out.set(0, 0, 0);
  for (let i = 0; i < 6; i++) out.addScaledVector(PAR_EMITTERS[i].rgb, PAR_EMITTERS[i].share * PHOTO.par.cd * (v[i] / 255));
  return out;
}

/** A laser diode's light in lumens: mW x 0.683 x the eye's sensitivity at its wavelength. */
export const laserLm = (name: string) => PHOTO.laser.mW[name] * 0.683 * PHOTO.laser.V[name];

/** The room's own part in its light: its surface area and mean reflectance give the even fill every lumen adds. */
export class RoomLight {
  area = 1; rho = 0.5;
  /** This frame's lumens (linear r, g, b): all of it, and what is left to spread evenly (a source whose first pool
   *  has its own light on the wall hands on only what that pool reflects). */
  readonly all = new THREE.Vector3();
  readonly spread = new THREE.Vector3();
  /** The even fill (lux) and the room's mean brightness (cd/m2: what lights the haze), from this frame's lumens. */
  readonly fill = new THREE.Vector3();
  readonly mean = new THREE.Vector3();

  setRoom([x, y, h]: readonly number[]) {
    const walls = 2 * (x + y) * h, flat = x * y;
    this.area = walls + 2 * flat;
    this.rho = (walls * REFLECT.wall + flat * (REFLECT.ceiling + REFLECT.floor)) / this.area;
  }
  begin() { this.all.set(0, 0, 0); this.spread.set(0, 0, 0); }
  /** ``lm`` lumens enter the room. pooled: its first pool on a surface is lit separately (poolReflect of it carries on). */
  add(lm: V3, pooled = 0) {
    this.all.add(lm);
    this.spread.addScaledVector(lm, pooled > 0 ? pooled : 1);
  }
  end() {
    const k = this.rho / (this.area * (1 - this.rho));
    this.fill.copy(this.spread).multiplyScalar(k).add(NIGHT_LUX);
    this.mean.copy(this.all).multiplyScalar(k / Math.PI).addScaledVector(NIGHT_LUX, this.rho / Math.PI);
  }
  /** The light in the room in lux (the average over its surfaces, direct and bounced). */
  get lux(): number { return lumens(this.all) / (this.area * (1 - this.rho)); }
}
