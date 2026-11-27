// The 3D room (/lights/room): the deployment's room as configured (model.ts), every light where its room file puts
// it, driven by what the Art-Net wire carries (the virtual node's DMX output), the screens fed by their own live
// streams (screens.ts), Hue lamps in show_service's colour for them (they have no wire to read).
//
// Everything is built in the room's frame (metres: x east, y north, z up), under one parent that maps it into
// three.js (y up). A moving head stands at its pose (space.py's: the rotation vector, pan and tilt signs, the tilt
// offset) and points exactly as show_service's beam_dir says: its yoke turns about the pose's z by pan_sign x pan and
// its head tips from that axis by tilt_sign x tilt + tilt_offset (docs/LIGHTS.md §3.6).

import * as THREE from "three";
import { BEAM_LAYER, beamShared, BOX_PATH_GLSL, HazePost } from "./fog";
import { beamVisibility } from "./haze";
import {
  barStrobe, COLOUR_SLOTS, colourName, DERBY_RGB, hash, HazeState, laserColours, LASER_NM, laserPattern, LASER_RGB, lerp, mixPar, motor, motorWords, MECH,
  pct, shutterGain, SpotMotion, spotShutter,
} from "./decode";
const HOUSE_RGB = 0xffd9b0;                       // the house lights' warm white
import type { Fixture, RoomModel, Vec3 } from "./model";
import { ScreenFeed } from "./screens";

type RGB = [number, number, number];
const UP_Y = new THREE.Vector3(0, 1, 0);
const Z = new THREE.Vector3(0, 0, 1);
const srgb = (c: RGB) => new THREE.Color().setRGB(c[0] / 255, c[1] / 255, c[2] / 255, THREE.SRGBColorSpace);
const v3 = (a: Vec3) => new THREE.Vector3(a[0], a[1], a[2]);

// ---- shared pieces -------------------------------------------------------------------------------------------------
// A beam is clipped to the room's box per pixel (owner, 26 Nov: "edges of your conic haze sections still render past
// the incident wall"): it ends exactly on the wall, floor or ceiling it meets, however steeply it meets it.
const beamVS = `varying float vY; varying vec3 vN; varying vec3 vV; varying vec3 vW; uniform float len;
  void main() { vY = position.y / len; vec4 mv = modelViewMatrix * vec4(position, 1.0);
    vW = (modelMatrix * vec4(position, 1.0)).xyz;
    vN = normalize(normalMatrix * normal); vV = normalize(-mv.xyz); gl_Position = projectionMatrix * mv; }`;
// Beams are drawn after the haze pass (fog.ts), straight to the screen: hidden behind what's in front by the room's
// depth, and dimmed by their own distance through the haze.
const beamFS = `uniform vec3 color; uniform float opacity; uniform vec3 boxMin; uniform vec3 boxMax;
  uniform float sigma; uniform vec3 camPos; uniform sampler2D tDepth; uniform vec2 invRes;
  varying float vY; varying vec3 vN; varying vec3 vV; varying vec3 vW;
  ${BOX_PATH_GLSL}
  void main() { if (any(lessThan(vW, boxMin)) || any(greaterThan(vW, boxMax))) discard;
    if (gl_FragCoord.z > texture2D(tDepth, gl_FragCoord.xy * invRes).x + 1e-6) discard;
    float core = pow(abs(dot(vN, vV)), 1.6); float fade = 1.0 - 0.55 * clamp(vY, 0.0, 1.0);
    float T = exp(-sigma * boxPath(camPos, vW, boxMin, boxMax));
    gl_FragColor = vec4(color * opacity * core * fade * T, 1.0); }`;
/** An open cone in the air, apex at the origin, along +z (a lens's axis), brightest at its core and near the lens. */
function beam(radius: number, length: number): THREE.Mesh<THREE.ConeGeometry, THREE.ShaderMaterial> {
  const g = new THREE.ConeGeometry(radius, length, 36, 1, true);
  g.rotateX(Math.PI);
  g.translate(0, length / 2, 0);
  const m = new THREE.ShaderMaterial({
    uniforms: { ...beamShared, color: { value: new THREE.Color(1, 1, 1) }, opacity: { value: 0 }, len: { value: length },
      boxMin: { value: new THREE.Vector3(-1e4, -1e4, -1e4) }, boxMax: { value: new THREE.Vector3(1e4, 1e4, 1e4) } },
    vertexShader: beamVS, fragmentShader: beamFS, transparent: true, depthWrite: false, depthTest: false,
    blending: THREE.AdditiveBlending, side: THREE.DoubleSide,
  });
  g.rotateX(Math.PI / 2);                                       // +y -> +z: the cone runs along the lens's axis
  const mesh = new THREE.Mesh(g, m);
  mesh.frustumCulled = false;
  mesh.layers.set(BEAM_LAYER);
  mesh.userData.beamLen = length;                               // fitBeams scales it to end on the surface it meets
  return mesh;
}
function setBeam(mesh: THREE.Mesh<THREE.ConeGeometry, THREE.ShaderMaterial>, c: RGB, a: number) {
  mesh.material.uniforms.color.value.copy(srgb(c));
  mesh.material.uniforms.opacity.value = a;
  mesh.visible = a > 0.002;
}
const dotTex = (() => {
  const c = document.createElement("canvas");
  c.width = c.height = 64;
  const x = c.getContext("2d")!;
  const g = x.createRadialGradient(32, 32, 0, 32, 32, 32);
  g.addColorStop(0, "rgba(255,255,255,1)"); g.addColorStop(0.35, "rgba(255,255,255,0.7)"); g.addColorStop(1, "rgba(255,255,255,0)");
  x.fillStyle = g; x.fillRect(0, 0, 64, 64);
  return new THREE.CanvasTexture(c);
})();
function dot(size: number): THREE.Sprite {
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: dotTex, blending: THREE.AdditiveBlending, depthWrite: false, transparent: true }));
  s.scale.setScalar(size);
  return s;
}
const GOBO_TEX = Array.from({ length: 10 }, (_, slot) => {
  const c = document.createElement("canvas");
  c.width = c.height = 128;
  const x = c.getContext("2d")!;
  x.fillStyle = "#000"; x.fillRect(0, 0, 128, 128);
  const soft = (cx: number, cy: number, r: number) => {
    const g = x.createRadialGradient(cx, cy, r * 0.75, cx, cy, r);
    g.addColorStop(0, "#fff"); g.addColorStop(1, "#000");
    x.fillStyle = g; x.beginPath(); x.arc(cx, cy, r, 0, Math.PI * 2); x.fill();
  };
  if (slot === 0) soft(64, 64, 62);
  else {
    const n = [0, 6, 5, 4, 8, 3, 4, 1, 12, 7][slot];
    for (let i = 0; i < n; i++) {
      const a = (i * Math.PI * 2) / n, rr = slot === 8 ? 40 : 30;
      soft(64 + Math.cos(a) * rr, 64 + Math.sin(a) * rr, slot === 2 ? 20 : slot === 5 ? 22 : 11);
    }
  }
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
});
const dark = new THREE.MeshStandardMaterial({ color: 0x1b1d23, roughness: 0.6, metalness: 0.3 });
const darker = new THREE.MeshStandardMaterial({ color: 0x0e0f13, roughness: 0.7, metalness: 0.2 });
function lens(r: number): THREE.Mesh<THREE.CircleGeometry, THREE.MeshBasicMaterial> {
  return new THREE.Mesh(new THREE.CircleGeometry(r, 32), new THREE.MeshBasicMaterial({ color: 0x000000 }));
}
/** A mesh or group whose local +z is turned onto ``dir`` (room frame). */
function aimZ(o: THREE.Object3D, dir: THREE.Vector3) {
  o.quaternion.setFromUnitVectors(Z, dir.clone().normalize());
}

// ---- the parts' live objects -------------------------------------------------------------------------------------
interface Ctx { ch: (f: Fixture, i: number) => number; t: number; dt: number; air: number; haze: HazeState; size: Vec3 }
interface Part { f: Fixture; update(c: Ctx): void; words?(c: Ctx): string }

/** Where a ray from inside the room meets a wall, the floor or the ceiling (room frame). */
/** The bar's laser (two kinds: laser() below). lift: its tilt above the bar's front when the room model gives no aim.
 *  Burst (grating): orders each way (9 x 9 beams a colour), the maker's coverage angle (the red grid's full width), how
 *  fast the beams dim with their order, and how bright the beams (in haze) and dots draw. Figures (pattern laser): a
 *  figure's size, the angle between the two apertures' figures and between the diodes', points per figure, rays shown
 *  in haze, how fast a shape breathes. Estimates but for cover_deg. */
const LASER = { lift: 20 * Math.PI / 180, orders: 4, cover_deg: 93, falloff: 0.3, beam: 0.22, dot: 2.2, dot_m: 0.03,
  span_deg: 7, pair_deg: 4, diode_deg: 5, points: 48, rays: 8, drift: 0.9 };

/** A round, soft-edged dot for the laser's landing points (a point sprite is a square without one). */
let _dot: THREE.Texture | null = null;
function dotSprite(): THREE.Texture {
  if (_dot) return _dot;
  const cv = document.createElement("canvas");
  cv.width = cv.height = 32;
  const g = cv.getContext("2d")!, gr = g.createRadialGradient(16, 16, 0, 16, 16, 16);
  gr.addColorStop(0, "#fff"); gr.addColorStop(0.45, "#fff"); gr.addColorStop(1, "rgba(255,255,255,0)");
  g.fillStyle = gr; g.fillRect(0, 0, 32, 32);
  _dot = new THREE.CanvasTexture(cv);
  return _dot;
}

function hitRoom(o: THREE.Vector3, d: THREE.Vector3, size: Vec3): THREE.Vector3 {
  let t = Infinity;
  const lo = [0, 0, 0], hi = size;
  for (const [k, ax] of (["x", "y", "z"] as const).entries()) {
    if (d[ax] > 1e-6) t = Math.min(t, (hi[k] - o[ax]) / d[ax]);
    else if (d[ax] < -1e-6) t = Math.min(t, (lo[k] - o[ax]) / d[ax]);
  }
  return o.clone().addScaledVector(d, t * 0.995);
}

type ScreenObj = { feed: ScreenFeed; mesh: THREE.Mesh<THREE.PlaneGeometry, THREE.MeshBasicMaterial>; light: THREE.PointLight;
  src: unknown; tex: THREE.Texture | null; sampledAt: number };

export interface RoomSimOptions { canvas: HTMLCanvasElement; room: string }

export class RoomSim {
  readonly renderer: THREE.WebGLRenderer;
  readonly scene = new THREE.Scene();
  readonly camera = new THREE.PerspectiveCamera(70, 1, 0.05, 80);
  private root = new THREE.Group();                 // room frame -> three.js
  private built = new THREE.Group();
  private parts: Part[] = [];
  private beams: THREE.Mesh[] = [];                 // every beam in the air: each frame cut to the surface it meets
  private screens = new Map<string, ScreenObj>();
  private model: RoomModel | null = null;
  private revision = "";
  haze = new HazeState();
  dmx = new Uint8Array(512);
  dmxAt = 0;
  universe: number | null = null;
  /** The haze's extinction, per metre (haze.ts sigmaOf: show_service's modelled haze in the air). 0: clear air, where
   *  a beam shows only as the pool it lights on a surface, as in a real room. */
  sigma = 0;
  private post: HazePost;
  /** An aid, not the room: draw every beam's path half-visible whatever the haze, to see where the heads aim. */
  showPaths = false;
  scale = 1;
  private orbit = { th: 0, ph: 1.2, r: 6 };
  private target = new THREE.Vector3();
  private w = 1; private h = 1;

  constructor(private o: RoomSimOptions) {
    this.renderer = new THREE.WebGLRenderer({ canvas: o.canvas, antialias: true });
    this.renderer.setPixelRatio(1);
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.15;
    this.renderer.shadowMap.enabled = true;          // a spot's gobo is projected through its shadow camera
    this.renderer.shadowMap.type = THREE.BasicShadowMap;
    this.scene.background = new THREE.Color(0x05060a);
    this.post = new HazePost(this.renderer);
    this.root.matrixAutoUpdate = false;              // (x, y, z) room -> (x, z, -y) three
    this.root.matrix.set(1, 0, 0, 0, 0, 0, 1, 0, 0, -1, 0, 0, 0, 0, 0, 1);
    this.root.updateMatrixWorld(true);
    this.scene.add(this.root);
    this.root.add(this.built);
    this.scene.add(new THREE.HemisphereLight(0x8890a8, 0x202228, 0.35));
  }

  /** The room in three.js world coordinates. */
  private world(p: THREE.Vector3): THREE.Vector3 {
    return new THREE.Vector3(p.x, p.z, -p.y);
  }

  resize(w: number, h: number) {
    this.w = Math.max(1, w); this.h = Math.max(1, h);
    this.renderer.setSize(Math.round(this.w * this.scale), Math.round(this.h * this.scale), false);
    this.o.canvas.style.width = `${this.w}px`;
    this.o.canvas.style.height = `${this.h}px`;
    this.camera.aspect = this.w / this.h;
    this.camera.updateProjectionMatrix();
  }

  // -- the room, rebuilt when the deployment's files change --------------------------------------------------------
  setModel(m: RoomModel) {
    const first = this.model === null;
    this.model = m;
    if (m.revision === this.revision) { this.refreshLamps(m); return; }
    this.revision = m.revision;
    this.clear();
    this.buildRoom(m);
    const byBar = new Map<string, Fixture[]>();
    for (const f of m.fixtures) if (f.part && f.part !== "dimmer") byBar.set(f.bar, [...(byBar.get(f.bar) ?? []), f]);
    for (const f of m.fixtures) if (f.part === "dimmer" && f.at) this.parts.push(this.houseLight(f));
    for (const [, fs] of byBar) this.buildUnit(fs, m);
    for (const f of m.fixtures) {
      if (f.kind === "screen" && f.screen && f.plane && f.at) this.buildScreen(f);
      else if (!f.part && f.kind === "lamp" && f.at) this.buildLamp(f);
    }
    for (const p of m.points) if (p.device === "mirror_ball") this.buildBall(v3(p.at));
    this.beams = [];
    this.built.traverse((o) => { if ((o as THREE.Object3D).userData?.beamLen) this.beams.push(o as THREE.Mesh); });
    const [X, Y, H] = m.size, eps = 0.01;
    for (const b of this.beams) {
      const u = (b.material as THREE.ShaderMaterial).uniforms;
      u.boxMin.value.set(-eps, -eps, -Y - eps);
      u.boxMax.value.set(X + eps, H + eps, eps);
    }
    this.post.boxMin.set(-eps, -eps, -Y - eps);
    this.post.boxMax.set(X + eps, H + eps, eps);
    if (first) this.view("side");                          // across the beams: the most readable start
  }

  private clear() {
    for (const s of this.screens.values()) s.feed.close();
    this.screens.clear();
    this.parts = [];
    this.root.remove(this.built);
    this.built.traverse((o) => {
      const any = o as any;
      any.geometry?.dispose?.();
      if (any.material && !Array.isArray(any.material) && any.material !== dark && any.material !== darker) any.material.dispose?.();
    });
    this.built = new THREE.Group();
    this.root.add(this.built);
  }

  private buildRoom(m: RoomModel) {
    const [X, Y, H] = m.size;
    const wall = new THREE.MeshLambertMaterial({ color: 0x3a3d46, side: THREE.FrontSide });
    const floor = new THREE.MeshLambertMaterial({ color: 0x2e3038 });
    const plane = (w: number, h: number, mat: THREE.Material, at: Vec3, normal: Vec3, up: Vec3) => {
      const p = new THREE.Mesh(new THREE.PlaneGeometry(w, h), mat);
      const zN = v3(normal).normalize(), yU = v3(up).normalize(), xR = new THREE.Vector3().crossVectors(yU, zN);
      p.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(xR, yU, zN));
      p.position.set(...at);
      p.receiveShadow = true;
      this.built.add(p);
    };
    plane(X, Y, floor, [X / 2, Y / 2, 0], [0, 0, 1], [0, 1, 0]);
    plane(X, Y, wall, [X / 2, Y / 2, H], [0, 0, -1], [0, 1, 0]);
    plane(X, H, wall, [X / 2, 0, H / 2], [0, 1, 0], [0, 0, 1]);
    plane(X, H, wall, [X / 2, Y, H / 2], [0, -1, 0], [0, 0, 1]);
    plane(Y, H, wall, [0, Y / 2, H / 2], [1, 0, 0], [0, 0, 1]);
    plane(Y, H, wall, [X, Y / 2, H / 2], [-1, 0, 0], [0, 0, 1]);
    const grid = new THREE.GridHelper(Math.max(X, Y), Math.round(Math.max(X, Y) * 2), 0x22252e, 0x22252e);
    grid.rotation.x = Math.PI / 2;
    grid.position.set(X / 2, Y / 2, 0.003);
    this.built.add(grid);
  }

  // -- a GigBAR: its parts on one bar, on a tripod ----------------------------------------------------------------
  private buildUnit(fs: Fixture[], m: RoomModel) {
    const placed = fs.filter((f) => f.at);
    if (!placed.length) return;
    const pars = placed.filter((f) => f.part === "par").sort((a, b) => a.id.localeCompare(b.id));
    const centre = placed.reduce((s, f) => s.add(v3(f.at!)), new THREE.Vector3()).multiplyScalar(1 / placed.length);
    const spot = placed.find((f) => f.part === "spot" && f.pose);
    // the bar's front: its heads' pan-0 direction (the pose's x), else towards the room's centre
    let front = spot ? new THREE.Vector3(1, 0, 0).applyQuaternion(rotQ(spot.pose!.rot)) : v3(m.centre).sub(centre);
    front.z = 0;
    if (front.lengthSq() < 1e-6) front = new THREE.Vector3(0, 1, 0);
    front.normalize();
    // the bar's axis: par 1 -> par 2 (left to right as you face it), else across the front
    let axis = pars.length >= 2 ? v3(pars[1].at!).sub(v3(pars[0].at!)) : new THREE.Vector3(front.y, -front.x, 0).negate();
    axis.z = 0;
    axis = axis.lengthSq() > 1e-6 ? axis.normalize() : new THREE.Vector3(-front.y, front.x, 0);
    const len = pars.length >= 2 ? v3(pars[1].at!).distanceTo(v3(pars[0].at!)) + 0.12 : 1.12;
    const barZ = (pars[0]?.at ?? placed[0].at!)[2];
    const mid = new THREE.Vector3(centre.x, centre.y, barZ);
    const up = new THREE.Vector3(0, 0, 1);
    // body, tripod
    const body = new THREE.Mesh(new THREE.BoxGeometry(len, 0.17, 0.15), darker);
    body.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(axis, front, up));
    body.position.copy(mid);
    this.built.add(body);
    const pole = new THREE.Mesh(new THREE.CylinderGeometry(0.02, 0.025, barZ - 0.08, 12), dark);
    aimY(pole, up); pole.position.set(mid.x, mid.y, (barZ - 0.08) / 2);
    this.built.add(pole);
    for (let i = 0; i < 3; i++) {
      const a = (i * Math.PI * 2) / 3 + Math.PI / 2;
      const foot = new THREE.Vector3(mid.x + Math.cos(a) * 0.55, mid.y + Math.sin(a) * 0.55, 0), hub = new THREE.Vector3(mid.x, mid.y, 0.75);
      const d = hub.clone().sub(foot);
      const leg = new THREE.Mesh(new THREE.CylinderGeometry(0.012, 0.014, d.length(), 8), dark);
      aimY(leg, d); leg.position.copy(foot).addScaledVector(d, 0.5);
      this.built.add(leg);
    }
    const frontOff = 0.086;
    for (const f of placed) {
      const at = v3(f.at!);
      const side = at.clone().sub(mid).dot(axis) < 0 ? -1 : 1;           // left or right of the bar's middle
      if (f.part === "par") this.parts.push(this.par(f, at.addScaledVector(front, frontOff), front, axis, side));
      else if (f.part === "derby") this.parts.push(this.derby(f, at.addScaledVector(front, frontOff), front, axis));
      else if (f.part === "flash") this.parts.push(this.strobe(f, at.addScaledVector(front, frontOff), front, axis));
      else if (f.part === "laser" || f.part === "laser50" || f.part === "laser51") this.parts.push(this.laser(f, at.addScaledVector(front, frontOff), front, axis));
      else if (f.part === "spot") this.parts.push(this.spot(f, front));
      else if (f.part === "hazer") this.parts.push(this.hazer(f));
    }
  }

  private par(f: Fixture, at: THREE.Vector3, front: THREE.Vector3, axis: THREE.Vector3, side: number): Part {
    const dir = front.clone().addScaledVector(new THREE.Vector3(0, 0, 1), 0.45).addScaledVector(axis, side * 0.5).normalize();
    const l = lens(0.055); aimZ(l, dir); l.position.copy(at); this.built.add(l);
    const light = new THREE.SpotLight(0xffffff, 0, 0, THREE.MathUtils.degToRad(17), 0.55, 1.6);
    light.position.copy(at); light.target.position.copy(at.clone().add(dir));
    this.built.add(light, light.target);
    const b = beam(0.9, 4.2); aimZ(b, dir); b.position.copy(at); this.built.add(b);
    return {
      f,
      update: (c) => {
        const v = [0, 1, 2, 3, 4, 5].map((i) => c.ch(f, i));
        const { rgb, level } = mixPar(v[0], v[1], v[2], v[3], v[4], v[5]);
        const a = level * shutterGain(barStrobe(c.ch(f, 6)), c.t, hash(f.id.length));
        l.material.color.copy(srgb(rgb)).multiplyScalar(0.15 + 2.2 * a);
        light.color.copy(srgb(rgb)); light.intensity = 26 * a;
        setBeam(b, rgb, 0.22 * a * c.air);
      },
    };
  }

  private derby(f: Fixture, at: THREE.Vector3, front: THREE.Vector3, axis: THREE.Vector3): Part {
    const l = lens(0.05); aimZ(l, front); l.position.copy(at); this.built.add(l);
    const ax = front.clone().addScaledVector(new THREE.Vector3(0, 0, 1), 0.55).normalize();
    const e1 = axis.clone(), e2 = new THREE.Vector3().crossVectors(ax, e1).normalize();
    const beams = Array.from({ length: 12 }, (_, i) => {
      const m = beam(0.035, 5); m.position.copy(at); this.built.add(m);
      const d = dot(0.11); this.built.add(d);
      return { m, d, ci: i % 4, i };
    });
    let rot = 0;
    return {
      f,
      update: (c) => {
        const g = shutterGain(barStrobe(c.ch(f, 4)), c.t, hash(f.id.length + 3));
        const lv = [0, 1, 2, 3].map((i) => (c.ch(f, i) / 255) * g);
        l.material.color.setRGB(lv[0] + lv[3] * 0.8, lv[1] + lv[3] * 0.8, lv[2] + lv[3] * 0.8).multiplyScalar(1.2);
        rot += motor(c.ch(f, 5), MECH.derbyRps) * c.dt * Math.PI * 2;
        for (const bm of beams) {
          const a = lv[bm.ci];
          const off = 0.35 + 0.55 * hash(bm.i * 3.1 + f.id.length), around = rot + bm.i * 4.8;
          const dir = ax.clone().multiplyScalar(Math.cos(off))
            .addScaledVector(e1, Math.sin(off) * Math.cos(around)).addScaledVector(e2, Math.sin(off) * Math.sin(around)).normalize();
          aimZ(bm.m, dir);
          setBeam(bm.m, DERBY_RGB[bm.ci], 0.22 * a * c.air);
          bm.d.position.copy(hitRoom(at, dir, c.size));
          bm.d.material.color.copy(srgb(DERBY_RGB[bm.ci])).multiplyScalar(a * 1.1);
          bm.d.visible = a > 0.01;
        }
      },
    };
  }

  private strobe(f: Fixture, at: THREE.Vector3, front: THREE.Vector3, axis: THREE.Vector3): Part {
    const panels = [-0.4, -0.13, 0.13, 0.4].map((o) => {
      const p = new THREE.Mesh(new THREE.PlaneGeometry(0.12, 0.03), new THREE.MeshBasicMaterial({ color: 0x000000 }));
      p.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(axis, new THREE.Vector3(0, 0, 1), front));
      p.position.copy(at).addScaledVector(axis, o).add(new THREE.Vector3(0, 0, -0.055));
      this.built.add(p);
      return p;
    });
    const glow = new THREE.PointLight(0xf0f4ff, 0, 0, 1.6);
    glow.position.copy(at).addScaledVector(front, 0.9).add(new THREE.Vector3(0, 0, -0.1));
    this.built.add(glow);
    return {
      f,
      update: (c) => {
        const g = shutterGain(barStrobe(c.ch(f, 4)), c.t, hash(f.id.length + 7));
        let sum = 0;
        panels.forEach((p, i) => { const a = (c.ch(f, i) / 255) * g; sum += a; p.material.color.setRGB(a * 2, a * 2, a * 2.1); });
        glow.intensity = 1.4 * sum;
      },
    };
  }

  // The bar's laser. Two different lasers are sold under this bar's name, and the DMX chart tells them apart
  // (docs/GIGBAR_RIG.md §1.4; owner, 27 Nov: "do you have any clue what the laser ACTUALLY shows in DMX mode"):
  //   - 52CH chart (the EU unit: colour, strobe, rotation; no patterns): a GRATING laser. Each lit diode is split
  //     into a square grid of thin beams (laserBurst below), which the rotation channel turns.
  //   - 51CH / 50CH chart (the US unit: colour and one of 32 patterns): a few fat beams, each landing as a small
  //     line figure, in mirrored pairs from its two apertures (laserFigures).
  private laser(f: Fixture, at: THREE.Vector3, front: THREE.Vector3, axis: THREE.Vector3): Part {
    return f.part === "laser" ? this.laserBurst(f, at, front, axis) : this.laserFigures(f, at, front, axis);
  }

  /** Where the laser points (space.yaml aim, else the bar's front lifted LASER.lift) and two axes across it. */
  private laserAxes(f: Fixture, at: THREE.Vector3, front: THREE.Vector3, axis: THREE.Vector3) {
    const up = new THREE.Vector3(0, 0, 1);
    const w = f.aim ? v3(f.aim).sub(at).normalize()
      : front.clone().multiplyScalar(Math.cos(LASER.lift)).addScaledVector(up, Math.sin(LASER.lift)).normalize();
    const u0 = (Math.abs(w.dot(axis)) < 0.95 ? axis.clone() : front.clone()).addScaledVector(w, -w.dot(axis)).normalize();
    const v0 = new THREE.Vector3().crossVectors(w, u0).normalize();
    return { w, u0, v0 };
  }

  // The grating laser (EU unit, 52CH). What is known, and from where:
  //   - Colour, strobe (under 4 a second) and rotation are all DMX gives it (EU manual Rev. 10 / 11). No patterns.
  //   - It is sold as a grating laser covering 93 degrees (the maker's and a retailer's data), and its 80 mW diodes
  //     are Class 3R only because the light is split into many beams of under 5 mW each (the manual's laser data).
  //   - A grating bends each colour by its own angle (sin = order x wavelength / pitch): the red grid is the widest,
  //     the blue the tightest, and only the centre beam of each lands on the same spot. Two diodes are two grids of
  //     two colours, never one mixed colour.
  //   - Footage of the sibling bar's grating laser shows it: a burst of dozens of needle beams in haze, dots on
  //     the walls.
  // NOT known (no footage of an EU unit under DMX): how many orders it throws (LASER.orders: 9 x 9 here), how fast
  // the grating turns (MECH.laserRps), and whether the grid is square. Estimates, to check against the real bar.
  private laserBurst(f: Fixture, at: THREE.Vector3, front: THREE.Vector3, axis: THREE.Vector3): Part {
    const M = LASER.orders, side = 2 * M + 1, per = side * side, names = ["red", "green", "blue"], n = per * 3;
    const bg = new THREE.BufferGeometry();
    bg.setAttribute("position", new THREE.BufferAttribute(new Float32Array(n * 6), 3));
    bg.setAttribute("color", new THREE.BufferAttribute(new Float32Array(n * 6), 3));
    const beams = new THREE.LineSegments(bg, new THREE.LineBasicMaterial({ vertexColors: true, blending: THREE.AdditiveBlending, transparent: true, depthWrite: false }));
    beams.frustumCulled = false;
    this.built.add(beams);
    const dg = new THREE.BufferGeometry();
    dg.setAttribute("position", new THREE.BufferAttribute(new Float32Array(n * 3), 3));
    dg.setAttribute("color", new THREE.BufferAttribute(new Float32Array(n * 3), 3));
    const dots = new THREE.Points(dg, new THREE.PointsMaterial({ size: LASER.dot_m, map: dotSprite(), vertexColors: true, blending: THREE.AdditiveBlending, transparent: true, depthWrite: false, sizeAttenuation: true }));
    dots.frustumCulled = false;
    this.built.add(dots);
    const { w, u0, v0 } = this.laserAxes(f, at, front, axis);
    const step0 = Math.sin(LASER.cover_deg * Math.PI / 360) / M;       // red's step per order, as a sine
    const tints = names.map((nm) => srgb(LASER_RGB[nm] as RGB));
    const dir = new THREE.Vector3(), u = new THREE.Vector3(), v = new THREE.Vector3();
    let rot = 0;
    return {
      f,
      update: (c) => {
        const cols = laserColours(c.ch(f, 0), false);
        const st = c.ch(f, 1);
        const on = !!cols && (!st || (c.t * lerp(0.5, 4, st / 255)) % 1 < 0.5);        // pulses: 4 a second at most
        rot += motor(c.ch(f, 2), MECH.laserRps) * c.dt * Math.PI * 2;                   // the grating turns
        dots.visible = on;
        beams.visible = on && c.air > 0.01;
        if (!on || !cols) return;
        u.copy(u0).multiplyScalar(Math.cos(rot)).addScaledVector(v0, Math.sin(rot));
        v.copy(v0).multiplyScalar(Math.cos(rot)).addScaledVector(u0, -Math.sin(rot));
        const bp = bg.attributes.position as THREE.BufferAttribute, bc = bg.attributes.color as THREE.BufferAttribute;
        const dp = dg.attributes.position as THREE.BufferAttribute, dc = dg.attributes.color as THREE.BufferAttribute;
        names.forEach((nm, ci) => {
          const lit = cols.includes(nm), step = step0 * LASER_NM[nm] / LASER_NM.red, t = tints[ci];
          for (let j = 0; j < per; j++) {
            const i = ci * per + j, om = (j % side) - M, on2 = Math.floor(j / side) - M;
            const sx = om * step, sy = on2 * step, s2 = sx * sx + sy * sy;
            if (!lit || s2 > 0.97) {                                                      // dark, or past the grating's reach
              bc.setXYZ(i * 2, 0, 0, 0); bc.setXYZ(i * 2 + 1, 0, 0, 0); dc.setXYZ(i, 0, 0, 0);
              continue;
            }
            dir.copy(w).multiplyScalar(Math.sqrt(1 - s2)).addScaledVector(u, sx).addScaledVector(v, sy);
            const end = hitRoom(at, dir, c.size);
            const gain = 1 / (1 + LASER.falloff * (om * om + on2 * on2));                 // the outer orders are dimmer
            const a = LASER.beam * gain * c.air;
            bp.setXYZ(i * 2, at.x, at.y, at.z); bp.setXYZ(i * 2 + 1, end.x, end.y, end.z);
            bc.setXYZ(i * 2, t.r * a, t.g * a, t.b * a); bc.setXYZ(i * 2 + 1, t.r * a * 0.5, t.g * a * 0.5, t.b * a * 0.5);
            dp.setXYZ(i, end.x, end.y, end.z);
            const d = LASER.dot * (0.35 + 0.65 * gain);
            dc.setXYZ(i, t.r * d, t.g * d, t.b * d);
          }
        });
        bp.needsUpdate = bc.needsUpdate = dp.needsUpdate = dc.needsUpdate = true;
      },
    };
  }

  // The pattern laser (US unit, 51CH; the older one-channel 50CH chart too), from footage of US units: a few fat
  // beams from two apertures, each landing as a small, sharp line figure a few degrees across, the two apertures'
  // figures mirror images of each other. Each lit diode draws its own figure beside the others (they don't mix).
  // The 51CH pattern channel picks one of 32 figures; what each number looks like isn't published, so each is
  // given a shape of its own here that breathes slowly. LASER.span_deg and the spacings are read off the footage.
  private laserFigures(f: Fixture, at: THREE.Vector3, front: THREE.Vector3, axis: THREE.Vector3): Part {
    const N = LASER.points, names = ["red", "green", "blue"], slots = 6, R = LASER.rays;
    const fg = new THREE.BufferGeometry();
    fg.setAttribute("position", new THREE.BufferAttribute(new Float32Array(slots * N * 6), 3));
    fg.setAttribute("color", new THREE.BufferAttribute(new Float32Array(slots * N * 6), 3));
    const lines = new THREE.LineSegments(fg, new THREE.LineBasicMaterial({ vertexColors: true, blending: THREE.AdditiveBlending, transparent: true, depthWrite: false }));
    lines.frustumCulled = false;
    this.built.add(lines);
    const rg = new THREE.BufferGeometry();
    rg.setAttribute("position", new THREE.BufferAttribute(new Float32Array(slots * R * 6), 3));
    rg.setAttribute("color", new THREE.BufferAttribute(new Float32Array(slots * R * 6), 3));
    const rays = new THREE.LineSegments(rg, new THREE.LineBasicMaterial({ vertexColors: true, blending: THREE.AdditiveBlending, transparent: true, depthWrite: false }));
    rays.frustumCulled = false;
    this.built.add(rays);
    const { w, u0, v0 } = this.laserAxes(f, at, front, axis);
    const span = Math.tan(LASER.span_deg * Math.PI / 360) * 0.55;
    const tints = names.map((nm) => srgb(LASER_RGB[nm] as RGB));
    const dir = new THREE.Vector3(), prev = new THREE.Vector3();
    let morph = hash(f.address ?? 7) * 10;
    return {
      f,
      update: (c) => {
        const cols = laserColours(c.ch(f, 0), true);
        lines.visible = !!cols;
        rays.visible = !!cols && c.air > 0.01;
        if (!cols) return;
        morph += c.dt * LASER.drift;
        const q = f.part === "laser51" ? laserPattern(c.ch(f, 1)) * 2.39996 + 0.25 * Math.sin(morph * 0.5) : morph;
        const a2 = 0.55 * Math.sin(q * 0.61), a3 = 0.4 * Math.sin(q * 0.43 + 1.7), p2 = q * 0.37, p3 = q * 0.29 + 0.9;
        const fp = fg.attributes.position as THREE.BufferAttribute, fc = fg.attributes.color as THREE.BufferAttribute;
        const rp = rg.attributes.position as THREE.BufferAttribute, rc = rg.attributes.color as THREE.BufferAttribute;
        for (let s = 0; s < slots; s++) {
          const ap = s < 3 ? -1 : 1, d = s % 3, lit = cols.includes(names[d]), t = tints[d];
          const off = Math.tan(ap * (LASER.pair_deg / 2 + (d - 1) * LASER.diode_deg) * Math.PI / 180);
          const k = lit ? 1.6 : 0, ka = lit ? 0.10 * c.air : 0;
          for (let i = 0; i <= N; i++) {
            const th = (i % N) / N * Math.PI * 2;
            const x = (Math.cos(th) + a2 * Math.cos(2 * th + p2) + a3 * Math.cos(3 * th + p3)) * span * ap;
            const y = (Math.sin(th) + a2 * Math.sin(2 * th - p2 * 1.3) + a3 * Math.sin(3 * th + p3 * 0.7)) * span;
            dir.copy(w).addScaledVector(u0, off + x).addScaledVector(v0, y).normalize();
            const end = lit ? hitRoom(at, dir, c.size) : at;
            if (i > 0) {
              const j = (s * N + i - 1) * 2;
              fp.setXYZ(j, prev.x, prev.y, prev.z); fp.setXYZ(j + 1, end.x, end.y, end.z);
              fc.setXYZ(j, t.r * k, t.g * k, t.b * k); fc.setXYZ(j + 1, t.r * k, t.g * k, t.b * k);
            }
            prev.copy(end);
            if (i < N && i % Math.floor(N / R) === 0) {
              const j = (s * R + Math.min(R - 1, Math.floor(i / Math.floor(N / R)))) * 2;
              rp.setXYZ(j, at.x, at.y, at.z); rp.setXYZ(j + 1, end.x, end.y, end.z);
              rc.setXYZ(j, t.r * ka, t.g * ka, t.b * ka); rc.setXYZ(j + 1, t.r * ka, t.g * ka, t.b * ka);
            }
          }
        }
        fp.needsUpdate = fc.needsUpdate = rp.needsUpdate = rc.needsUpdate = true;
      },
    };
  }

  private spot(f: Fixture, front: THREE.Vector3): Part {
    const pose = f.pose ?? { at: f.at!, rot: [0, 0, Math.atan2(front.y, front.x)] as Vec3, tilt_offset_deg: 0, pan_sign: 1, tilt_sign: 1 };
    const base = new THREE.Group();
    base.position.copy(v3(pose.at));
    base.quaternion.copy(rotQ(pose.rot));
    this.built.add(base);
    const yoke = new THREE.Group(); base.add(yoke);
    const foot = new THREE.Mesh(new THREE.CylinderGeometry(0.055, 0.06, 0.03, 20), dark);
    foot.rotation.x = Math.PI / 2; foot.position.z = -0.095; yoke.add(foot);
    for (const s of [-1, 1]) {
      const arm = new THREE.Mesh(new THREE.BoxGeometry(0.05, 0.014, 0.12), dark);
      arm.position.set(0, s * 0.058, -0.02); yoke.add(arm);
    }
    const head = new THREE.Group(); yoke.add(head);
    const can = new THREE.Mesh(new THREE.CylinderGeometry(0.045, 0.05, 0.13, 20), darker);
    can.rotation.x = Math.PI / 2; can.position.z = 0.01; head.add(can);
    const hl = lens(0.038); hl.position.z = 0.077; head.add(hl);
    const light = new THREE.SpotLight(0xffffff, 0, 0, THREE.MathUtils.degToRad(8.5), 0.35, 1.5);
    light.position.z = 0.08; light.target.position.z = 3; head.add(light, light.target);
    light.castShadow = true; light.shadow.mapSize.set(512, 512); light.shadow.camera.near = 0.1; light.shadow.camera.far = 25;
    light.map = GOBO_TEX[0];
    const b = beam(0.75, 6); b.position.z = 0.08; head.add(b);
    const core = beam(0.07, 6); core.position.z = 0.08; head.add(core);
    const mo = new SpotMotion();
    const off = THREE.MathUtils.degToRad(pose.tilt_offset_deg || 0);
    return {
      f,
      update: (c) => {
        mo.step((i) => c.ch(f, i), c.dt, f.panRange, f.tiltRange);
        yoke.rotation.z = THREE.MathUtils.degToRad(pose.pan_sign * mo.pan);           // about the pose's z (pan axis)
        head.rotation.y = THREE.MathUtils.degToRad(pose.tilt_sign * mo.tilt) + off;   // tips z towards x (tilt)
        const dim = c.ch(f, 7) / 255, a = dim * shutterGain(spotShutter(c.ch(f, 8)), c.t, hash(f.id.length + 9));
        const rgb = mo.colour(), g = mo.goboSlot(c.t);
        if (light.map !== GOBO_TEX[g]) light.map = GOBO_TEX[g];
        light.color.copy(srgb(rgb)); light.intensity = 160 * a;
        hl.material.color.copy(srgb(rgb)).multiplyScalar(0.1 + 2.5 * a);
        setBeam(b, rgb, 0.75 * a * c.air);
        setBeam(core, rgb, 1.6 * a * c.air);
      },
      words: (c) => {
        const dim = c.ch(f, 7), sh = spotShutter(c.ch(f, 8));
        const name = COLOUR_SLOTS[Math.round(mo.wheel) % 10][0], gobo = Math.round(mo.gobo) % 10;
        const moving = Math.abs(mo.pan - mo.targetPan) > 1 || Math.abs(mo.tilt - mo.targetTilt) > 1 ? " · moving" : "";
        const aim = `pan ${mo.targetPan.toFixed(0)}°, tilt ${mo.targetTilt.toFixed(0)}°${moving}`;
        return dim === 0 || sh.mode === "closed" ? `dark · ${aim}` : `${name} ${Math.round(dim / 2.55)}% · ${gobo ? `gobo ${gobo}` : "no gobo"} · ${aim}`;
      },
    };
  }

  private hazer(f: Fixture): Part {
    const at = v3(f.at!);
    const box = new THREE.Mesh(new THREE.BoxGeometry(0.32, 0.15, 0.39), darker);
    box.position.copy(at); this.built.add(box);
    const led = new THREE.Mesh(new THREE.SphereGeometry(0.015, 8, 8), new THREE.MeshBasicMaterial({ color: 0x111111 }));
    led.position.copy(at).add(new THREE.Vector3(0, 0, 0.12)); this.built.add(led);
    return {
      f,
      update: (c) => {
        c.haze.step(c.ch(f, 0), c.ch(f, 1), performance.now() - this.dmxAt < 1500, c.t, c.dt, false);
        const h = c.haze, t = c.t;
        led.material.color.set(h.firstDmx === null ? 0x111111 : !h.ready ? (t % 1 < 0.5 ? 0x22ff22 : 0x002200)
          : h.out > 0 ? (t % 0.6 < 0.3 ? 0xffbb22 : 0x332200) : 0xffbb22);
      },
      words: (c) => c.haze.words(c.t),
    };
  }

  // -- screens, lamps that have no wire, the glitterball ------------------------------------------------------------
  private buildScreen(f: Fixture) {
    const pl = f.plane!;
    const n = v3(pl.normal).normalize(), u = v3(pl.up).normalize(), r = new THREE.Vector3().crossVectors(u, n);
    const mesh = new THREE.Mesh(new THREE.PlaneGeometry(pl.size[0], pl.size[1]), new THREE.MeshBasicMaterial({ color: 0x14161e }));
    mesh.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(r, u, n));
    mesh.position.copy(v3(f.at!)).addScaledVector(n, 0.012);
    this.built.add(mesh);
    const light = new THREE.PointLight(0xffffff, 0, 0, 1.8);
    light.position.copy(v3(f.at!)).addScaledVector(n, 0.8);
    this.built.add(light);
    this.screens.set(f.id, { feed: new ScreenFeed(this.o.room, f.screen!), mesh, light, src: null, tex: null, sampledAt: 0 });
  }

  private lampMeshes = new Map<string, { m: THREE.Mesh<THREE.SphereGeometry, THREE.MeshBasicMaterial>; l: THREE.PointLight }>();
  /** A house light: a ceiling downlight on one dimmer channel, warm white, lighting the room below it. */
  private houseLight(f: Fixture): Part {
    const at = v3(f.at!);
    const down = new THREE.Vector3(0, 0, -1);
    const rim = new THREE.Mesh(new THREE.CylinderGeometry(0.1, 0.1, 0.03, 24), dark);
    aimY(rim, down); rim.position.copy(at); this.built.add(rim);
    const face = new THREE.Mesh(new THREE.CircleGeometry(0.08, 24), new THREE.MeshBasicMaterial({ color: 0x000000 }));
    aimZ(face, down); face.position.copy(at).add(new THREE.Vector3(0, 0, -0.017)); this.built.add(face);
    const light = new THREE.SpotLight(HOUSE_RGB, 0, 0, THREE.MathUtils.degToRad(60), 0.9, 1.4);
    light.position.copy(at).add(new THREE.Vector3(0, 0, -0.05)); light.target.position.copy(at).add(new THREE.Vector3(0, 0, -1));
    this.built.add(light, light.target);
    return {
      f,
      update: (c) => {
        const a = c.ch(f, 0) / 255, k = a * a;                 // a dimmer's curve: roughly square law
        face.material.color.set(HOUSE_RGB).multiplyScalar(0.05 + 3 * k);
        light.intensity = 45 * k;
      },
      words: (c) => (c.ch(f, 0) ? `on ${pct(c.ch(f, 0))}` : "off"),
    };
  }

  private buildLamp(f: Fixture) {
    const m = new THREE.Mesh(new THREE.SphereGeometry(0.09, 16, 12), new THREE.MeshBasicMaterial({ color: 0x111111 }));
    m.position.copy(v3(f.at!)); this.built.add(m);
    const l = new THREE.PointLight(0xffffff, 0, 0, 1.6); l.position.copy(v3(f.at!)); this.built.add(l);
    this.lampMeshes.set(f.id, { m, l });
    this.refreshLamp(f);
  }
  private refreshLamps(m: RoomModel) { for (const f of m.fixtures) if (this.lampMeshes.has(f.id)) this.refreshLamp(f); }
  private refreshLamp(f: Fixture) {
    const lm = this.lampMeshes.get(f.id);
    if (!lm) return;
    const hex = f.lastRgb && /^#[0-9a-f]{6}$/i.test(f.lastRgb) ? f.lastRgb : "#000000";
    const c = new THREE.Color(hex);
    lm.m.material.color.copy(c).multiplyScalar(1.4);
    lm.l.color.copy(c); lm.l.intensity = 6 * Math.max(c.r, c.g, c.b);
  }

  private buildBall(at: THREE.Vector3) {
    const ball = new THREE.Mesh(new THREE.SphereGeometry(0.2, 24, 16),
      new THREE.MeshStandardMaterial({ color: 0xcfd4dc, metalness: 1, roughness: 0.15, flatShading: true }));
    ball.position.copy(at); this.built.add(ball);
    this.parts.push({ f: { id: "mirror_ball" } as Fixture, update: (c) => { ball.rotation.z += c.dt * 0.6; } });
  }

  // -- each frame --------------------------------------------------------------------------------------------------
  private last = performance.now();
  frame(now: number) {
    const dt = Math.min(0.1, (now - this.last) / 1000);
    this.last = now;
    const m = this.model;
    if (!m) return;
    const air = this.showPaths ? 0.55 : beamVisibility(this.sigma);
    const ch = (f: Fixture, i: number) => {
      if (f.address == null || (this.universe != null && f.universe != null && f.universe !== this.universe)) return 0;
      const a = f.address - 1 + i;
      return a >= 0 && a < 512 ? this.dmx[a] : 0;
    };
    const c: Ctx = { ch, t: now / 1000, dt, air, haze: this.haze, size: m.size };
    for (const p of this.parts) p.update(c);
    this.fitBeams(m.size);
    for (const s of this.screens.values()) this.screenFrame(s, now);
    beamShared.sigma.value = this.showPaths ? 0 : this.sigma;
    this.post.render(this.scene, this.camera, this.sigma);
  }

  private screenFrame(s: ScreenObj, now: number) {
    const src = s.feed.source;
    if (src !== s.src) {
      s.src = src;
      s.tex?.dispose();
      s.tex = null;
      if (src instanceof HTMLVideoElement) s.tex = new THREE.VideoTexture(src);
      else if (src instanceof HTMLImageElement) { s.tex = new THREE.Texture(src); s.tex.needsUpdate = true; }
      if (s.tex) s.tex.colorSpace = THREE.SRGBColorSpace;
      s.mesh.material.map = s.tex;
      s.mesh.material.color.set(s.tex ? 0xffffff : 0x14161e);
      s.mesh.material.needsUpdate = true;
    } else if (src instanceof HTMLImageElement && s.tex && s.tex.image !== src) {
      s.tex.needsUpdate = true;
    }
    if (src && now - s.sampledAt > 250) {                     // the picture lights the room a little, in its colour
      s.sampledAt = now;
      try {
        const cv = sampleCanvas, x = cv.getContext("2d", { willReadFrequently: true })!;
        x.drawImage(src, 0, 0, 8, 5);
        const d = x.getImageData(0, 0, 8, 5).data;
        let r = 0, g = 0, b = 0;
        for (let i = 0; i < d.length; i += 4) { r += d[i]; g += d[i + 1]; b += d[i + 2]; }
        const k = d.length / 4;
        s.light.color.copy(srgb([r / k, g / k, b / k]));
        s.light.intensity = (2.5 * (r + g + b)) / (k * 765);
      } catch { s.light.intensity = 0; }
    }
  }

  /** Each visible beam runs from its lens to the wall, floor or ceiling its axis meets (owner, 26 Nov: "lighting
   *  cones should be shown transecting the surface they arrive on, not just rendered as fixed-length cones"): its
   *  axis is found in the room's frame and the cone scaled to that distance (uniformly, so its angle stays). */
  private fitBeams(size: Vec3) {
    this.root.updateMatrixWorld(true);
    const o = new THREE.Vector3(), tip = new THREE.Vector3();
    for (const b of this.beams) {
      if (!b.visible) continue;
      const len = b.userData.beamLen as number;
      b.scale.setScalar(1);
      b.updateMatrixWorld(true);
      b.getWorldPosition(o); this.root.worldToLocal(o);
      tip.set(0, 0, 1); b.localToWorld(tip); this.root.worldToLocal(tip);
      const d = tip.sub(o).normalize();
      const hit = hitRoom(o, d, size);
      b.scale.setScalar((Math.max(0.05, hit.distanceTo(o)) * 1.8) / len);   // past the wall; the clip cuts it there
    }
  }

  /** Every part's words, for the side panel: [{unit, rows: [[part, words]]}]. */
  words(): { unit: string; rows: [string, string][] }[] {
    const m = this.model;
    if (!m) return [];
    const t = performance.now() / 1000;
    const ch = (f: Fixture, i: number) => (f.address == null ? 0 : this.dmx[f.address - 1 + i] ?? 0);
    const c: Ctx = { ch, t, dt: 0, air: 1, haze: this.haze, size: m.size };
    const out = new Map<string, [string, string][]>();
    for (const p of this.parts) {
      const f = p.f;
      if (!f.label) continue;
      let w = p.words ? p.words(c) : "";
      if (!w) {
        if (f.part === "par") {
          const v = [0, 1, 2, 3, 4, 5].map((i) => ch(f, i)), { rgb, level } = mixPar(v[0], v[1], v[2], v[3], v[4], v[5]);
          w = level < 0.004 ? "off" : `${colourName(rgb)} ${Math.round(level * 100)}%${barStrobe(ch(f, 6)).mode !== "open" ? " · strobing" : ""}`;
        } else if (f.part === "derby") {
          const names = ["red", "green", "blue", "white"];
          const lit = names.map((n, i) => [n, ch(f, i)] as const).filter(([, v]) => v > 0).map(([n, v]) => `${n} ${Math.round(v / 2.55)}%`);
          const r = motor(ch(f, 5), MECH.derbyRps);
          w = `${lit.length ? lit.join(", ") : "off"} · ${r ? (r > 0 ? "turning clockwise" : "turning anticlockwise") : "still"}`;
        } else if (f.part === "flash") {
          const top = Math.max(...[0, 1, 2, 3].map((i) => ch(f, i)));
          w = top ? `white ${Math.round(top / 2.55)}%${barStrobe(ch(f, 4)).mode !== "open" ? " · strobing" : ""}` : "off";
        } else if (f.part === "laser" || f.part === "laser50" || f.part === "laser51") {
          const cols = laserColours(ch(f, 0), f.part !== "laser");
          const how = f.part === "laser" ? ` · ${motorWords(motor(ch(f, 2), MECH.laserRps))}${ch(f, 1) ? " · strobing" : ""}`
            : f.part === "laser51" ? ` · pattern ${laserPattern(ch(f, 1)) + 1}` : "";
          w = cols ? `ON: ${cols.join(" + ")}${how}` : "off";
        }
      }
      const unit = f.part === "hazer" ? "Hazer" : f.part === "dimmer" ? "House lights" : f.bar;
      out.set(unit, [...(out.get(unit) ?? []), [f.label.replace(/^Bar [A-Z] /, ""), w]]);
    }
    for (const [id, lm] of this.lampMeshes) {
      const f = m.fixtures.find((x) => x.id === id);
      if (f) out.set("Lamps", [...(out.get("Lamps") ?? []), [f.label, f.lastRgb ?? "–"]]);
      void lm;
    }
    for (const [id, s] of this.screens) {
      const f = m.fixtures.find((x) => x.id === id);
      out.set("Screens", [...(out.get("Screens") ?? []), [f?.label ?? id, (s.feed.state === "live" ? `live (${s.feed.session})` : s.feed.state) + (s.feed.rejoins ? ` · rejoined ${s.feed.rejoins}×` : "")]]);
    }
    return [...out].map(([unit, rows]) => ({ unit, rows }));
  }

  // -- camera ------------------------------------------------------------------------------------------------------
  view(which: "front" | "side" | "above") {
    const m = this.model;
    if (!m) return;
    const f = v3(m.frontVec).setZ(0).normalize();
    const c = v3(m.centre);
    const depth = Math.abs(f.x) * m.size[0] + Math.abs(f.y) * m.size[1];
    const tgt = c.clone().addScaledVector(f, depth * 0.25).setZ(Math.min(2, m.size[2] * 0.5));
    this.target.copy(this.world(tgt));
    const wf = this.world(f).normalize();                     // the front, in three's frame
    const th0 = Math.atan2(-wf.x, -wf.z);                      // looking along the front: the camera behind the target
    const r = depth * 0.62;
    if (which === "front") this.orbit = { th: th0, ph: 1.45, r };
    else if (which === "side") this.orbit = { th: th0 + 0.85, ph: 1.15, r };
    else this.orbit = { th: th0, ph: 0.8, r };
    this.place();
  }
  orbitBy(dx: number, dy: number) { this.orbit.th -= dx * 0.006; this.orbit.ph -= dy * 0.006; this.place(); }
  zoomBy(dy: number) { this.orbit.r *= Math.exp(dy * 0.001); this.place(); }
  private place() {
    const o = this.orbit;
    o.ph = Math.max(0.12, Math.min(1.62, o.ph));
    o.r = Math.max(0.8, Math.min(20, o.r));
    this.camera.position.set(this.target.x + o.r * Math.sin(o.ph) * Math.sin(o.th), this.target.y + o.r * Math.cos(o.ph),
      this.target.z + o.r * Math.sin(o.ph) * Math.cos(o.th));
    this.camera.lookAt(this.target);
  }

  dispose() {
    this.clear();
    this.post.dispose();
    this.renderer.dispose();
  }
}

const sampleCanvas = (() => { const c = document.createElement("canvas"); c.width = 8; c.height = 5; return c; })();
/** A rotation vector (axis x angle, radians) as a quaternion. */
function rotQ(rv: Vec3): THREE.Quaternion {
  const v = v3(rv), a = v.length();
  return a < 1e-12 ? new THREE.Quaternion() : new THREE.Quaternion().setFromAxisAngle(v.normalize(), a);
}
/** A mesh whose local +y (three's cylinder axis) is turned onto ``dir``. */
function aimY(o: THREE.Object3D, dir: THREE.Vector3) {
  o.quaternion.setFromUnitVectors(UP_Y, dir.clone().normalize());
}
