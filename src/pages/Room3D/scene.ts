// The 3D room (/lights/room): the deployment's room as configured (model.ts), every light where its room file puts
// it, driven by what the Art-Net wire carries (the virtual node's DMX output), the screens fed by their own live
// streams (screens.ts), Hue lamps in show_service's colour for them (they have no wire to read).
//
// How bright everything is: light.ts (each light at its maker's output, the light bouncing round the room, the haze
// lit by all of it), beams.ts (a beam's own light in the air) and fog.ts (the picture as an adapting eye takes it in).
//
// Everything is built in the room's frame (metres: x east, y north, z up), under one parent that maps it into
// three.js (y up). A moving head stands at its pose (space.py's: the rotation vector, pan and tilt signs, the tilt
// offset) and points exactly as show_service's beam_dir says: its yoke turns about the pose's z by pan_sign x pan and
// its head tips from that axis by tilt_sign x tilt + tilt_offset (docs/LIGHTS.md §3.6).

import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { airCone, airLines, setCone } from "./beams";
import { beamShared, HazePost, SCREEN_ALPHA } from "./fog";
import {
  barStrobe, COLOUR_SLOTS, colourName, DERBY_RGB, hash, HazeState, laserColours, LASER_NM, laserPattern, LASER_RGB, lerp, mixPar, motor, motorWords, MECH,
  pct, shutterGain, SpotMotion, spotShutter,
} from "./decode";
import { coneOmega, FACE_CAP, laserLm, linear, parCd, perLumen, PHOTO, REFLECT, RoomLight } from "./light";
const HOUSE_RGB = [255, 217, 176];                // the house lights' warm white
import type { Fixture, RoomModel, Vec3 } from "./model";
import { ScreenFeed } from "./screens";

const UP_Y = new THREE.Vector3(0, 1, 0);
const Z = new THREE.Vector3(0, 0, 1);
const v3 = (a: Vec3) => new THREE.Vector3(a[0], a[1], a[2]);
const rad = THREE.MathUtils.degToRad;

// ---- shared pieces -------------------------------------------------------------------------------------------------
// Everything here is in real units (light.ts): a light's colour is its candela, a lit thing's colour its cd/m2.

/** A light's strength: candela on its axis, as linear red, green, blue. */
function setLight(l: THREE.Light, cd: THREE.Vector3) {
  l.color.setRGB(cd.x, cd.y, cd.z);
  l.intensity = 1;
}

// A lit lens or LED as you see it: glaring when you are in its beam, a faint glow from the side.
const faceVS = `varying vec3 vN; varying vec3 vW;
  void main() { vec4 w = modelMatrix * vec4(position, 1.0); vW = w.xyz; vN = normalize(mat3(modelMatrix) * normal);
    gl_Position = projectionMatrix * viewMatrix * w; }`;
const faceFS = `uniform vec3 nits; uniform float cosIn; uniform float cosOut; varying vec3 vN; varying vec3 vW;
  void main() { float c = dot(normalize(vN), normalize(cameraPosition - vW));
    gl_FragColor = vec4(nits * (0.02 + 0.98 * smoothstep(cosOut, cosIn, c)), 1.0); }`;
type Face = THREE.Mesh<THREE.BufferGeometry, THREE.ShaderMaterial>;
/** A light's face (its normal: the geometry's +z). Full brightness within ``inDeg`` of its axis, a glow past ``outDeg``. */
function face(g: THREE.BufferGeometry, inDeg: number, outDeg: number): Face {
  return new THREE.Mesh(g, new THREE.ShaderMaterial({ vertexShader: faceVS, fragmentShader: faceFS,
    uniforms: { nits: { value: new THREE.Vector3() }, cosIn: { value: Math.cos(rad(inDeg)) }, cosOut: { value: Math.cos(rad(outDeg)) } } }));
}
/** A face's brightness from its light's candela and its own area (m2): cd/m2, no brighter than FACE_CAP. */
function setFace(m: Face, cd: THREE.Vector3, area: number) {
  const top = Math.max(cd.x, cd.y, cd.z, 1e-6);
  m.material.uniforms.nits.value.copy(cd).multiplyScalar(Math.min(1 / area, FACE_CAP / top));
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
/** How much of the dot's disc its light fills (its centre is full, its edge nothing). */
const DOT_FILL = 0.356;
/** A blob of light lying on a surface (its normal: the mesh's +z), added to what the surface already shows. */
function blob(): THREE.Mesh<THREE.PlaneGeometry, THREE.MeshBasicMaterial> {
  return new THREE.Mesh(new THREE.PlaneGeometry(1, 1),
    new THREE.MeshBasicMaterial({ map: dotTex, blending: THREE.AdditiveBlending, depthWrite: false, transparent: true }));
}
/** How much of the open beam each gobo lets through (filled in as the gobos are drawn). */
const GOBO_OPEN: number[] = [];
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
  const px = x.getImageData(0, 0, 128, 128).data;
  let sum = 0;
  for (let i = 0; i < px.length; i += 4) sum += px[i];
  GOBO_OPEN[slot] = slot === 0 ? sum : sum / GOBO_OPEN[0];
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
});
GOBO_OPEN[0] = 1;
const dark = new THREE.MeshStandardMaterial({ color: 0x1b1d23, roughness: 0.6, metalness: 0.3 });
const darker = new THREE.MeshStandardMaterial({ color: 0x0e0f13, roughness: 0.7, metalness: 0.2 });
/** A mesh or group whose local +z is turned onto ``dir`` (room frame). */
function aimZ(o: THREE.Object3D, dir: THREE.Vector3) {
  o.quaternion.setFromUnitVectors(Z, dir.clone().normalize());
}

// ---- the parts' live objects -------------------------------------------------------------------------------------
/** air: is there haze to show a beam in. room: where each part reports the lumens it sends into the room. */
interface Ctx { ch: (f: Fixture, i: number) => number; t: number; dt: number; air: boolean; haze: HazeState; size: Vec3; room: RoomLight }
interface Part { f: Fixture; update(c: Ctx): void; words?(c: Ctx): string }

/** Where a ray from inside the room meets a wall, the floor or the ceiling (room frame). */
/** The bar's laser (two kinds: laser() below). lift: its tilt above the bar's front when the room model gives no aim.
 *  Burst (grating): orders each way (9 x 9 beams a colour), the maker's coverage angle (the red grid's full width), how
 *  fast the beams dim with their order, and the size a landing dot is drawn (its brightness: light.ts, from the
 *  diodes' mW). Figures (pattern laser): a
 *  figure's size, the angle between the two apertures' figures and between the diodes', points per figure, rays shown
 *  in haze, how fast a shape breathes. Estimates but for cover_deg. */
const LASER = { lift: 20 * Math.PI / 180, orders: 4, cover_deg: 93, falloff: 0.3, dot_m: 0.03,
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

/** The moving heads' shadow maps (their gobos) are redrawn every this many frames. */
const SHADOW_EVERY = 3;

function hitRoom(o: THREE.Vector3, d: THREE.Vector3, size: Vec3): THREE.Vector3 {
  let t = Infinity;
  const lo = [0, 0, 0], hi = size;
  for (const [k, ax] of (["x", "y", "z"] as const).entries()) {
    if (d[ax] > 1e-6) t = Math.min(t, (hi[k] - o[ax]) / d[ax]);
    else if (d[ax] < -1e-6) t = Math.min(t, (lo[k] - o[ax]) / d[ax]);
  }
  return o.clone().addScaledVector(d, t * 0.995);
}
/** The same, also saying which surface it is: its normal (into the room) in ``n``. Returns how much light that
 *  surface reflects (light.ts REFLECT). ``p``: where it lands. */
function hitFace(o: THREE.Vector3, d: THREE.Vector3, size: Vec3, p: THREE.Vector3, n: THREE.Vector3): number {
  let t = Infinity;
  n.set(0, 0, 1);
  for (const [k, ax] of (["x", "y", "z"] as const).entries()) {
    const tt = d[ax] > 1e-6 ? (size[k] - o[ax]) / d[ax] : d[ax] < -1e-6 ? -o[ax] / d[ax] : Infinity;
    if (tt < t) { t = tt; n.set(0, 0, 0); n[ax] = d[ax] > 0 ? -1 : 1; }
  }
  p.copy(o).addScaledVector(d, t * 0.995);
  return n.z > 0.5 ? REFLECT.floor : n.z < -0.5 ? REFLECT.ceiling : REFLECT.wall;
}
/** The cone of a light a pool throws back into the room (pool() below): nearly a half-sphere, strongest straight out. */
const POOL = { angle: 1.45, omega: coneOmega(1.45, 1) };

type ScreenObj = { feed: ScreenFeed; mesh: THREE.Mesh<THREE.PlaneGeometry, THREE.MeshBasicMaterial>; light: THREE.SpotLight;
  area: number; lm: THREE.Vector3; src: unknown; tex: THREE.Texture | null; sampledAt: number };

export interface RoomSimOptions { canvas: HTMLCanvasElement; room: string }

export class RoomSim {
  readonly renderer: THREE.WebGLRenderer;
  readonly scene = new THREE.Scene();
  readonly camera = new THREE.PerspectiveCamera(70, 1, 0.02, 80);
  private root = new THREE.Group();                 // room frame -> three.js
  private built = new THREE.Group();
  private parts: Part[] = [];
  /** The room's own part in its light (light.ts): the lumens every part reports each frame, the even fill they make. */
  readonly light = new RoomLight();
  private fill = new THREE.AmbientLight(0xffffff, 1);
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
  private w = 1; private h = 1;

  // The room's keep-out regions (space.yaml keep_out), drawn over the finished picture: they are a setup aid, not
  // light in the room, so they don't pass through the exposure and always show (through walls too).
  private keepScene = new THREE.Scene();
  private keepRoot = new THREE.Group();
  private keepBoxes = new THREE.Group();
  /** Something else draws over the picture too (the editor's outline and gizmo): draw the overlay every frame. */
  overlayOn = false;
  /** The overlay, for the editor (editor.ts): its root is in the room's frame (metres, z up). */
  get overlayRoot(): THREE.Group { return this.keepRoot; }
  get overlayScene(): THREE.Scene { return this.keepScene; }

  /** The keep-out boxes to draw: amber, red when a laser's burst crosses one, stronger when it is the one picked. */
  setKeepOut(boxes: { centre: Vec3; size: Vec3; crossed?: boolean; picked?: boolean }[]) {
    for (const c of [...this.keepBoxes.children]) {
      this.keepBoxes.remove(c);
      const m = c as THREE.Mesh;
      m.geometry?.dispose();
      (m.material as THREE.Material | undefined)?.dispose();
    }
    for (const b of boxes) {
      const g = new THREE.BoxGeometry(b.size[0], b.size[1], b.size[2]);
      const col = b.crossed ? 0xff4545 : 0xffb020;
      const fill = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ color: col, transparent: true, opacity: b.picked ? 0.28 : 0.12, depthWrite: false, depthTest: false }));
      const edge = new THREE.LineSegments(new THREE.EdgesGeometry(g), new THREE.LineBasicMaterial({ color: col, transparent: true, opacity: b.picked ? 1 : 0.7, depthTest: false }));
      fill.position.set(b.centre[0], b.centre[1], b.centre[2]);
      edge.position.copy(fill.position);
      this.keepBoxes.add(fill, edge);
    }
  }

  constructor(private o: RoomSimOptions) {
    this.renderer = new THREE.WebGLRenderer({ canvas: o.canvas, antialias: true });
    this.renderer.setPixelRatio(1);
    this.renderer.toneMapping = THREE.NoToneMapping;  // the picture is in cd/m2 until fog.ts exposes it, once
    this.renderer.setClearColor(0x000000, 0);         // outside the room: nothing (alpha 0: not part of the view's brightness)
    this.renderer.shadowMap.enabled = true;          // a spot's gobo is projected through its shadow camera
    this.renderer.shadowMap.type = THREE.BasicShadowMap;
    // Each moving head's shadow map is a whole extra drawing of the room; three.js redraws every one every frame, lit
    // or not. They are redrawn every SHADOW_EVERY frames instead (a gobo's pattern lags a head by a frame or two).
    this.renderer.shadowMap.autoUpdate = false;
    this.post = new HazePost(this.renderer);
    this.root.matrixAutoUpdate = false;              // (x, y, z) room -> (x, z, -y) three
    this.root.matrix.set(1, 0, 0, 0, 0, 0, 1, 0, 0, -1, 0, 0, 0, 0, 0, 1);
    this.root.updateMatrixWorld(true);
    this.scene.add(this.root);
    this.root.add(this.built);
    this.keepRoot.matrixAutoUpdate = false;          // the same room -> three frame
    this.keepRoot.matrix.copy(this.root.matrix);
    this.keepRoot.updateMatrixWorld(true);
    this.keepRoot.add(this.keepBoxes);
    this.keepScene.add(this.keepRoot);
    this.scene.add(this.fill);                       // the light bouncing round the room (frame())
    const oc = new OrbitControls(this.camera, o.canvas);
    oc.enableDamping = true;
    oc.dampingFactor = 0.12;
    oc.enableZoom = false;                           // the wheel is ours (wheelAt): towards what is under the cursor
    oc.screenSpacePanning = true;
    oc.minDistance = 0.15;
    oc.maxDistance = 30;
    oc.zoomSpeed = 1.2;
    oc.rotateSpeed = 0.7;
    oc.mouseButtons = { LEFT: THREE.MOUSE.ROTATE, MIDDLE: THREE.MOUSE.PAN, RIGHT: THREE.MOUSE.PAN };
    oc.touches = { ONE: THREE.TOUCH.ROTATE, TWO: THREE.TOUCH.DOLLY_PAN };
    oc.addEventListener("start", () => { this.anim = null; this.push.set(0, 0, 0); });   // a hand on the controls stops a glide
    this.controls = oc;
    o.canvas.addEventListener("wheel", (e) => {      // not passive: the page mustn't scroll under the view
      e.preventDefault();
      const r = o.canvas.getBoundingClientRect();
      const d = e.deltaMode === 1 ? e.deltaY * 33 : e.deltaMode === 2 ? e.deltaY * 400 : e.deltaY;
      this.wheelAt(d, ((e.clientX - r.left) / r.width) * 2 - 1, -(((e.clientY - r.top) / r.height) * 2 - 1));
    }, { passive: false });
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
    // every cone in the air reaches across the room (its mesh only marks where to work the beam out: beams.ts)
    const reach = Math.hypot(...m.size) * 1.05;
    this.built.traverse((o) => { if (o.userData?.airCone) o.scale.setScalar(reach); });
    const [X, Y, H] = m.size, eps = 0.01;
    this.post.boxMin.set(-eps, -eps, -Y - eps);
    this.post.boxMax.set(X + eps, H + eps, eps);
    this.light.setRoom(m.size);
    if (first) this.view("side", true);                          // across the beams: the most readable start
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
    // each surface reflects its share of the light landing on it (light.ts REFLECT), evenly in every direction
    const grey = (r: number) => new THREE.Color().setRGB(r, r, r);
    const wall = new THREE.MeshLambertMaterial({ color: grey(REFLECT.wall), side: THREE.FrontSide });
    const ceiling = new THREE.MeshLambertMaterial({ color: grey(REFLECT.ceiling) });
    const floor = new THREE.MeshLambertMaterial({ color: grey(REFLECT.floor), map: floorTiles(X, Y) });
    const plane = (w: number, h: number, mat: THREE.Material, at: Vec3, normal: Vec3, up: Vec3) => {
      const p = new THREE.Mesh(new THREE.PlaneGeometry(w, h), mat);
      const zN = v3(normal).normalize(), yU = v3(up).normalize(), xR = new THREE.Vector3().crossVectors(yU, zN);
      p.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(xR, yU, zN));
      p.position.set(...at);
      p.receiveShadow = true;
      this.built.add(p);
    };
    plane(X, Y, floor, [X / 2, Y / 2, 0], [0, 0, 1], [0, 1, 0]);
    plane(X, Y, ceiling, [X / 2, Y / 2, H], [0, 0, -1], [0, 1, 0]);
    plane(X, H, wall, [X / 2, 0, H / 2], [0, 1, 0], [0, 0, 1]);
    plane(X, H, wall, [X / 2, Y, H / 2], [0, -1, 0], [0, 0, 1]);
    plane(Y, H, wall, [0, Y / 2, H / 2], [1, 0, 0], [0, 0, 1]);
    plane(Y, H, wall, [X, Y / 2, H / 2], [-1, 0, 0], [0, 0, 1]);
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
    // a proper rotation: axis x front is down here, so the box's third axis is that (a mirrored basis, with up, turned
    // the body askew whenever the bar wasn't square to the room)
    body.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(axis, front, new THREE.Vector3().crossVectors(axis, front)));
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

  /** The pool a wash or a mover makes on a surface, as the light it throws back into the room (light.ts §2). */
  private pool(): THREE.SpotLight {
    const l = new THREE.SpotLight(0xffffff, 0, 0, POOL.angle, 1, 2);
    this.built.add(l, l.target);
    return l;
  }
  private poolAt = new THREE.Vector3();
  private poolN = new THREE.Vector3();
  /** Put a pool's light where the beam from ``o`` along ``d`` (room frame) lands, for ``lm`` lumens landing there.
   *  Returns the share of them the surface reflects. Kept half a metre from the neighbouring surfaces: the real pool
   *  is a patch, not a point, and a point right in a corner would burn it. */
  private setPool(l: THREE.SpotLight, o: THREE.Vector3, d: THREE.Vector3, lm: THREE.Vector3, size: Vec3): number {
    const p = this.poolAt, n = this.poolN, rho = hitFace(o, d, size, p, n);
    for (const [k, ax] of (["x", "y", "z"] as const).entries()) {
      if (n[ax] !== 0) p[ax] += n[ax] * 0.05;
      else p[ax] = Math.max(Math.min(0.5, size[k] / 2), Math.min(size[k] - Math.min(0.5, size[k] / 2), p[ax]));
    }
    l.position.copy(p);
    l.target.position.copy(p).add(n);
    l.color.setRGB(lm.x, lm.y, lm.z).multiplyScalar(rho / POOL.omega);
    l.intensity = 1;
    return rho;
  }

  // A par: the maker's 1,205 lux at 2 m with everything on, beam 22 / field 33 degrees.
  private par(f: Fixture, at: THREE.Vector3, front: THREE.Vector3, axis: THREE.Vector3, side: number): Part {
    // how it points: its own tilt and turn (the Lights page), else its bar layout's, else this view's guess
    const dir = f.aimDir ? v3(f.aimDir) : front.clone().addScaledVector(new THREE.Vector3(0, 0, 1), 0.45).addScaledVector(axis, side * 0.5).normalize();
    const ang = rad(PHOTO.par.field_deg / 2), omega = coneOmega(ang, 1), area = Math.PI * 0.055 ** 2;
    const l = face(new THREE.CircleGeometry(0.055, 32), PHOTO.par.beam_deg / 2, PHOTO.par.field_deg); aimZ(l, dir); l.position.copy(at); this.built.add(l);
    const light = new THREE.SpotLight(0xffffff, 0, 0, ang, 1, 2);
    light.position.copy(at); light.target.position.copy(at.clone().add(dir));
    this.built.add(light, light.target);
    const b = airCone(ang, 1); aimZ(b, dir); b.position.copy(at); this.built.add(b);
    const pool = this.pool(), cd = new THREE.Vector3(), lm = new THREE.Vector3();
    return {
      f,
      update: (c) => {
        parCd([0, 1, 2, 3, 4, 5].map((i) => c.ch(f, i)), cd).multiplyScalar(shutterGain(barStrobe(c.ch(f, 6)), c.t, hash(f.id.length)));
        setLight(light, cd); setFace(l, cd, area); setCone(b, cd);
        lm.copy(cd).multiplyScalar(omega);
        c.room.add(lm, this.setPool(pool, at, dir, lm, c.size));
      },
    };
  }

  // A derby: each colour's LEDs are split by its lens into separate narrow beams, each landing as a blob. The maker
  // gives no output for it: PHOTO.derby is an estimate. Three of each colour's beams are drawn, each with one real
  // beam's light; all of the derby's light counts towards the room's.
  private derby(f: Fixture, at: THREE.Vector3, front: THREE.Vector3, axis: THREE.Vector3): Part {
    const D = PHOTO.derby, half = rad(D.beam_deg / 2), omega = 2 * Math.PI * (1 - Math.cos(half));
    const ax = f.aimDir ? v3(f.aimDir) : front.clone().addScaledVector(new THREE.Vector3(0, 0, 1), 0.55).normalize();   // its tilt and turn
    const l = face(new THREE.CircleGeometry(0.05, 32), 40, 75); aimZ(l, ax); l.position.copy(at); this.built.add(l);
    const faceOmega = 2 * Math.PI * (1 - Math.cos(rad(131 / 2))), faceArea = Math.PI * 0.05 ** 2;   // the maker's coverage
    const e1 = axis.clone(), e2 = new THREE.Vector3().crossVectors(ax, e1).normalize();
    const tints = DERBY_RGB.map((c) => perLumen(c));
    const beams = Array.from({ length: 12 }, (_, i) => {
      const m = airCone(half, 0.5); m.position.copy(at); this.built.add(m);
      const d = blob(); this.built.add(d);
      return { m, d, ci: i % 4, i };
    });
    const cd = new THREE.Vector3(), lm = new THREE.Vector3(), hit = new THREE.Vector3(), n = new THREE.Vector3();
    let rot = 0;
    return {
      f,
      update: (c) => {
        const g = shutterGain(barStrobe(c.ch(f, 4)), c.t, hash(f.id.length + 3));
        const lv = [0, 1, 2, 3].map((i) => (c.ch(f, i) / 255) * g);
        lm.set(0, 0, 0);
        lv.forEach((a, i) => lm.addScaledVector(tints[i], a * D.lm[i]));
        c.room.add(lm);
        setFace(l, cd.copy(lm).multiplyScalar(1 / faceOmega), faceArea);
        rot += motor(c.ch(f, 5), MECH.derbyRps) * c.dt * Math.PI * 2;
        for (const bm of beams) {
          const one = (lv[bm.ci] * D.lm[bm.ci]) / D.beams;               // one beam's lumens
          const off = 0.35 + 0.55 * hash(bm.i * 3.1 + f.id.length), around = rot + bm.i * 4.8;
          const dir = ax.clone().multiplyScalar(Math.cos(off))
            .addScaledVector(e1, Math.sin(off) * Math.cos(around)).addScaledVector(e2, Math.sin(off) * Math.sin(around)).normalize();
          aimZ(bm.m, dir);
          setCone(bm.m, cd.copy(tints[bm.ci]).multiplyScalar(one / omega));
          // its blob: the beam's width where it lands, as bright as that much light on that surface
          const rho = hitFace(at, dir, c.size, hit, n), r = hit.distanceTo(at) * Math.tan(half) * 1.5 + 0.03;
          bm.d.position.copy(hit).addScaledVector(n, 0.004);
          bm.d.quaternion.setFromUnitVectors(Z, n);
          bm.d.scale.setScalar(2 * r);
          const k = (rho * one) / (Math.PI * DOT_FILL * Math.PI * r * r);
          bm.d.material.color.setRGB(tints[bm.ci].x * k, tints[bm.ci].y * k, tints[bm.ci].z * k);
          bm.d.visible = one > 0.01;
        }
      },
    };
  }

  // The strobes: four cool-white LEDs, the maker's 130 lux at 2 m together, beam 30 / field 62 degrees.
  private strobe(f: Fixture, at: THREE.Vector3, front: THREE.Vector3, axis: THREE.Vector3): Part {
    const ang = rad(PHOTO.strobe.field_deg / 2), omega = coneOmega(ang, 1), white = perLumen([238, 243, 255]);
    const panels = [-0.4, -0.13, 0.13, 0.4].map((o) => {
      const p = face(new THREE.PlaneGeometry(0.12, 0.03), PHOTO.strobe.beam_deg / 2, 80);
      p.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(axis, new THREE.Vector3(0, 0, 1), front));
      p.position.copy(at).addScaledVector(axis, o).add(new THREE.Vector3(0, 0, -0.055));
      this.built.add(p);
      return p;
    });
    const light = new THREE.SpotLight(0xffffff, 0, 0, ang, 1, 2);
    light.position.copy(at); light.target.position.copy(at.clone().add(front));
    this.built.add(light, light.target);
    const b = airCone(ang, 1); aimZ(b, front); b.position.copy(at); this.built.add(b);
    const cd = new THREE.Vector3(), one = new THREE.Vector3();
    return {
      f,
      update: (c) => {
        const g = shutterGain(barStrobe(c.ch(f, 4)), c.t, hash(f.id.length + 7));
        let sum = 0;
        panels.forEach((p, i) => {
          const a = (c.ch(f, i) / 255) * g; sum += a;
          setFace(p, one.copy(white).multiplyScalar((PHOTO.strobe.cd / 4) * a), 0.12 * 0.03);
        });
        cd.copy(white).multiplyScalar((PHOTO.strobe.cd / 4) * sum);
        setLight(light, cd); setCone(b, cd);
        c.room.add(one.copy(cd).multiplyScalar(omega));
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
    const beams = airLines(n, at), bg = beams.geometry;           // each beam's light in the air (beams.ts)
    this.built.add(beams);
    const dg = new THREE.BufferGeometry();
    dg.setAttribute("position", new THREE.BufferAttribute(new Float32Array(n * 3), 3));
    dg.setAttribute("color", new THREE.BufferAttribute(new Float32Array(n * 3), 3));
    const dots = new THREE.Points(dg, new THREE.PointsMaterial({ size: LASER.dot_m, map: dotSprite(), vertexColors: true, blending: THREE.AdditiveBlending, transparent: true, depthWrite: false, sizeAttenuation: true }));
    dots.frustumCulled = false;
    this.built.add(dots);
    const { w, u0, v0 } = this.laserAxes(f, at, front, axis);
    const step0 = Math.sin(LASER.cover_deg * Math.PI / 360) / M;       // red's step per order, as a sine
    // No beam outside the maker's coverage (93 degrees: 46.5 off the laser's axis): the grid's corners are cut off,
    // so the burst is round. The room model aims the laser by this edge (space.yaml: no beam past 45 degrees forward).
    const reach = Math.sin(LASER.cover_deg * Math.PI / 360) ** 2 * 1.001;
    const tints = names.map((nm) => perLumen(LASER_RGB[nm]));
    // Each diode's light (lumens, from its mW: light.ts) is shared among its beams, the outer orders getting less.
    const gainOf = (j: number) => 1 / (1 + LASER.falloff * (((j % side) - M) ** 2 + (Math.floor(j / side) - M) ** 2));
    const inReach = (nm: string, j: number) => (((j % side) - M) ** 2 + (Math.floor(j / side) - M) ** 2) * (step0 * LASER_NM[nm] / LASER_NM.red) ** 2 <= reach;
    const total = names.map((nm) => Array.from({ length: per }, (_, j) => (inReach(nm, j) ? gainOf(j) : 0)).reduce((a, b) => a + b, 0));
    // a dot is drawn LASER.dot_m across on screen at 90 degrees of view; this is the wall area that is (the real dot
    // is smaller and brighter: the same light)
    const dotArea = 0.551 * Math.PI * (LASER.dot_m * Math.tan(rad(this.camera.fov / 2)) / 2) ** 2;
    const dir = new THREE.Vector3(), u = new THREE.Vector3(), v = new THREE.Vector3(), end = new THREE.Vector3(), nrm = new THREE.Vector3();
    const lm = new THREE.Vector3();
    let rot = 0;
    return {
      f,
      update: (c) => {
        const cols = laserColours(c.ch(f, 0), false);
        const st = c.ch(f, 1);
        const on = !!cols && (!st || (c.t * lerp(0.5, 4, st / 255)) % 1 < 0.5);        // pulses: 4 a second at most
        rot += motor(c.ch(f, 2), MECH.laserRps) * c.dt * Math.PI * 2;                   // the grating turns
        dots.visible = on;
        beams.visible = on && c.air;
        if (!on || !cols) return;
        lm.set(0, 0, 0);
        names.forEach((nm, ci) => { if (cols.includes(nm)) lm.addScaledVector(tints[ci], laserLm(nm)); });
        c.room.add(lm);
        u.copy(u0).multiplyScalar(Math.cos(rot)).addScaledVector(v0, Math.sin(rot));
        v.copy(v0).multiplyScalar(Math.cos(rot)).addScaledVector(u0, -Math.sin(rot));
        const bp = bg.attributes.position as THREE.BufferAttribute, bc = bg.attributes.flux as THREE.BufferAttribute;
        const dp = dg.attributes.position as THREE.BufferAttribute, dc = dg.attributes.color as THREE.BufferAttribute;
        names.forEach((nm, ci) => {
          const lit = cols.includes(nm), step = step0 * LASER_NM[nm] / LASER_NM.red, t = tints[ci];
          for (let j = 0; j < per; j++) {
            const i = ci * per + j, om = (j % side) - M, on2 = Math.floor(j / side) - M;
            const sx = om * step, sy = on2 * step, s2 = sx * sx + sy * sy;
            if (!lit || s2 > reach) {                                                     // dark, or outside the maker's coverage
              bc.setXYZ(i * 2, 0, 0, 0); bc.setXYZ(i * 2 + 1, 0, 0, 0); dc.setXYZ(i, 0, 0, 0);
              continue;
            }
            dir.copy(w).multiplyScalar(Math.sqrt(1 - s2)).addScaledVector(u, sx).addScaledVector(v, sy);
            const rho = hitFace(at, dir, c.size, end, nrm);
            const a = (laserLm(nm) * gainOf(j)) / total[ci];                             // this beam's lumens
            bp.setXYZ(i * 2, at.x, at.y, at.z); bp.setXYZ(i * 2 + 1, end.x, end.y, end.z);
            bc.setXYZ(i * 2, t.x * a, t.y * a, t.z * a); bc.setXYZ(i * 2 + 1, t.x * a, t.y * a, t.z * a);
            dp.setXYZ(i, end.x, end.y, end.z);
            const d = (rho * a) / (Math.PI * dotArea);                                   // its dot: that light on that surface
            dc.setXYZ(i, t.x * d, t.y * d, t.z * d);
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
    const rays = airLines(slots * R, at), rg = rays.geometry;     // the swept beam in the air (beams.ts)
    this.built.add(rays);
    const { w, u0, v0 } = this.laserAxes(f, at, front, axis);
    const span = Math.tan(LASER.span_deg * Math.PI / 360) * 0.55;
    const tints = names.map((nm) => perLumen(LASER_RGB[nm]));
    const dir = new THREE.Vector3(), prev = new THREE.Vector3(), lm = new THREE.Vector3();
    let morph = hash(f.address ?? 7) * 10;
    return {
      f,
      update: (c) => {
        const cols = laserColours(c.ch(f, 0), true);
        lines.visible = !!cols;
        rays.visible = !!cols && c.air;
        if (!cols) return;
        lm.set(0, 0, 0);
        names.forEach((nm, ci) => { if (cols.includes(nm)) lm.addScaledVector(tints[ci], laserLm(nm)); });
        c.room.add(lm);
        morph += c.dt * LASER.drift;
        const q = f.part === "laser51" ? laserPattern(c.ch(f, 1)) * 2.39996 + 0.25 * Math.sin(morph * 0.5) : morph;
        const a2 = 0.55 * Math.sin(q * 0.61), a3 = 0.4 * Math.sin(q * 0.43 + 1.7), p2 = q * 0.37, p3 = q * 0.29 + 0.9;
        const fp = fg.attributes.position as THREE.BufferAttribute, fc = fg.attributes.color as THREE.BufferAttribute;
        const rp = rg.attributes.position as THREE.BufferAttribute, rc = rg.attributes.flux as THREE.BufferAttribute;
        for (let s = 0; s < slots; s++) {
          const ap = s < 3 ? -1 : 1, d = s % 3, lit = cols.includes(names[d]), t = tints[d];
          const off = Math.tan(ap * (LASER.pair_deg / 2 + (d - 1) * LASER.diode_deg) * Math.PI / 180);
          // half a diode's light in each aperture's figure: on the wall, spread along a line about a metre long
          // and a pixel wide (ESTIMATE: about 10 cd/m2 per lumen); in the air, shared among the rays drawn
          const half = lit ? laserLm(names[d]) / 2 : 0, k = 10 * half, ka = half / R;
          for (let i = 0; i <= N; i++) {
            const th = (i % N) / N * Math.PI * 2;
            const x = (Math.cos(th) + a2 * Math.cos(2 * th + p2) + a3 * Math.cos(3 * th + p3)) * span * ap;
            const y = (Math.sin(th) + a2 * Math.sin(2 * th - p2 * 1.3) + a3 * Math.sin(3 * th + p3 * 0.7)) * span;
            dir.copy(w).addScaledVector(u0, off + x).addScaledVector(v0, y).normalize();
            const end = lit ? hitRoom(at, dir, c.size) : at;
            if (i > 0) {
              const j = (s * N + i - 1) * 2;
              fp.setXYZ(j, prev.x, prev.y, prev.z); fp.setXYZ(j + 1, end.x, end.y, end.z);
              fc.setXYZ(j, t.x * k, t.y * k, t.z * k); fc.setXYZ(j + 1, t.x * k, t.y * k, t.z * k);
            }
            prev.copy(end);
            if (i < N && i % Math.floor(N / R) === 0) {
              const j = (s * R + Math.min(R - 1, Math.floor(i / Math.floor(N / R)))) * 2;
              rp.setXYZ(j, at.x, at.y, at.z); rp.setXYZ(j + 1, end.x, end.y, end.z);
              rc.setXYZ(j, t.x * ka, t.y * ka, t.z * ka); rc.setXYZ(j + 1, t.x * ka, t.y * ka, t.z * ka);
            }
          }
        }
        fp.needsUpdate = fc.needsUpdate = rp.needsUpdate = rc.needsUpdate = true;
      },
    };
  }

  // A moving head: the maker's 5,140 lux at 2 m in open white, beam 17 degrees. Its colour wheel and gobos only take
  // light away (a red filter passes about a fifth of the white's lumens, a gobo its open share).
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
    const ang = rad(PHOTO.spot.beam_deg / 2), pen = 0.25, omega = coneOmega(ang, pen), area = Math.PI * 0.038 ** 2;
    const hl = face(new THREE.CircleGeometry(0.038, 32), PHOTO.spot.beam_deg / 2, PHOTO.spot.beam_deg * 1.5); hl.position.z = 0.077; head.add(hl);
    const light = new THREE.SpotLight(0xffffff, 0, 0, ang, pen, 2);
    light.position.z = 0.08; light.target.position.z = 3; head.add(light, light.target);
    light.castShadow = true; light.shadow.mapSize.set(512, 512); light.shadow.camera.near = 0.1; light.shadow.camera.far = 25;
    light.map = GOBO_TEX[0];
    const b = airCone(ang, pen); b.position.z = 0.08; head.add(b);
    const pool = this.pool(), cd = new THREE.Vector3(), lm = new THREE.Vector3(), o = new THREE.Vector3(), d = new THREE.Vector3();
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
        linear(rgb, cd).multiplyScalar(PHOTO.spot.cd * a);
        setLight(light, cd); setFace(hl, cd, area); setCone(b, cd);
        // where it points now, in the room's frame: its pool there lights the room back
        head.updateWorldMatrix(true, false);
        this.root.worldToLocal(o.set(0, 0, 0.08).applyMatrix4(head.matrixWorld));
        this.root.worldToLocal(d.set(0, 0, 1.08).applyMatrix4(head.matrixWorld)).sub(o).normalize();
        lm.copy(cd).multiplyScalar(omega * GOBO_OPEN[g]);
        c.room.add(lm, this.setPool(pool, o, d, lm, c.size));
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
          : h.out > 0 ? (t % 0.6 < 0.3 ? 0xffbb22 : 0x332200) : 0xffbb22).multiplyScalar(400);   // an indicator LED, cd/m2
      },
      words: (c) => c.haze.words(c.t),
    };
  }

  // -- screens, lamps that have no wire, the glitterball ------------------------------------------------------------
  private buildScreen(f: Fixture) {
    const pl = f.plane!;
    const n = v3(pl.normal).normalize(), u = v3(pl.up).normalize(), r = new THREE.Vector3().crossVectors(u, n);
    // its picture in cd/m2, marked as a screen's (fog.ts SCREEN_ALPHA: shown as the eye on it sees it)
    const mesh = new THREE.Mesh(new THREE.PlaneGeometry(pl.size[0], pl.size[1]),
      new THREE.MeshBasicMaterial({ color: SCREEN_OFF.clone(), transparent: true, opacity: SCREEN_ALPHA, blending: THREE.NoBlending }));
    mesh.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(r, u, n));
    mesh.position.copy(v3(f.at!)).addScaledVector(n, 0.012);
    this.built.add(mesh);
    // the light it throws into the room: from its face, strongest straight out
    const light = new THREE.SpotLight(0xffffff, 0, 0, POOL.angle, 1, 2);
    light.position.copy(v3(f.at!)).addScaledVector(n, 0.3);
    light.target.position.copy(light.position).add(n);
    this.built.add(light, light.target);
    this.screens.set(f.id, { feed: new ScreenFeed(this.o.room, f.screen!), mesh, light, area: pl.size[0] * pl.size[1],
      lm: new THREE.Vector3(), src: null, tex: null, sampledAt: 0 });
  }

  private lampMeshes = new Map<string, { m: THREE.Mesh<THREE.SphereGeometry, THREE.MeshBasicMaterial>; l: THREE.PointLight; lm: THREE.Vector3 }>();
  /** A house light: a ceiling downlight on one dimmer channel, warm white, lighting the room below it. */
  private houseLight(f: Fixture): Part {
    const at = v3(f.at!);
    const down = new THREE.Vector3(0, 0, -1);
    const rim = new THREE.Mesh(new THREE.CylinderGeometry(0.1, 0.1, 0.03, 24), dark);
    aimY(rim, down); rim.position.copy(at); this.built.add(rim);
    const ang = rad(60), pen = 0.9, omega = coneOmega(ang, pen), warm = perLumen(HOUSE_RGB);
    const lamp = face(new THREE.CircleGeometry(0.08, 24), 30, 88);
    aimZ(lamp, down); lamp.position.copy(at).add(new THREE.Vector3(0, 0, -0.017)); this.built.add(lamp);
    const light = new THREE.SpotLight(0xffffff, 0, 0, ang, pen, 2);
    light.position.copy(at).add(new THREE.Vector3(0, 0, -0.05)); light.target.position.copy(at).add(new THREE.Vector3(0, 0, -1));
    this.built.add(light, light.target);
    const b = airCone(ang, pen); aimZ(b, down); b.position.copy(light.position); this.built.add(b);
    const cd = new THREE.Vector3(), lm = new THREE.Vector3();
    return {
      f,
      update: (c) => {
        const a = c.ch(f, 0) / 255, k = a * a;                 // a dimmer's curve: roughly square law
        lm.copy(warm).multiplyScalar(PHOTO.house_lm * k);      // PHOTO.house_lm lumens at full (an estimate)
        cd.copy(lm).multiplyScalar(1 / omega);
        setLight(light, cd); setFace(lamp, cd, Math.PI * 0.08 ** 2); setCone(b, cd);
        c.room.add(lm);
      },
      words: (c) => (c.ch(f, 0) ? `on ${pct(c.ch(f, 0))}` : "off"),
    };
  }

  private buildLamp(f: Fixture) {
    const m = new THREE.Mesh(new THREE.SphereGeometry(0.09, 16, 12), new THREE.MeshBasicMaterial({ color: 0x111111 }));
    m.position.copy(v3(f.at!)); this.built.add(m);
    const l = new THREE.PointLight(0xffffff, 0, 0, 2); l.position.copy(v3(f.at!)); this.built.add(l);
    this.lampMeshes.set(f.id, { m, l, lm: new THREE.Vector3() });
    this.refreshLamp(f);
  }
  private refreshLamps(m: RoomModel) { for (const f of m.fixtures) if (this.lampMeshes.has(f.id)) this.refreshLamp(f); }
  private refreshLamp(f: Fixture) {
    const lm = this.lampMeshes.get(f.id);
    if (!lm) return;
    const hex = f.lastRgb && /^#[0-9a-f]{6}$/i.test(f.lastRgb) ? f.lastRgb : "#000000";
    // PHOTO.lamp_lm lumens at full white, less in a colour (an estimate), in every direction; the bulb itself glows
    const c = new THREE.Color(hex);
    lm.lm.set(c.r, c.g, c.b).multiplyScalar(PHOTO.lamp_lm);
    lm.l.color.setRGB(lm.lm.x, lm.lm.y, lm.lm.z).multiplyScalar(1 / (4 * Math.PI)); lm.l.intensity = 1;
    lm.m.material.color.copy(lm.l.color).multiplyScalar(1 / (Math.PI * 0.09 ** 2));
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
    this.animStep(dt);
    this.pushStep();
    this.controls.update();
    this.keepCamera();
    if (++this.frameNo % SHADOW_EVERY === 0) this.renderer.shadowMap.needsUpdate = true;
    const m = this.model;
    if (!m) return;
    // "Show beam paths" (an aid): every beam is drawn as in a medium haze that takes nothing out of the light
    const air = this.showPaths || this.sigma > 1e-4;
    beamShared.on.value = air;
    beamShared.scatter.value = this.showPaths ? Math.max(this.sigma, 0.3) : this.sigma;
    beamShared.sigma.value = this.showPaths ? 0 : this.sigma;
    const ch = (f: Fixture, i: number) => {
      if (f.address == null || (this.universe != null && f.universe != null && f.universe !== this.universe)) return 0;
      const a = f.address - 1 + i;
      return a >= 0 && a < 512 ? this.dmx[a] : 0;
    };
    // every part reports the lumens it sends into the room; their bounce is the fill and what lights the haze
    this.light.begin();
    const c: Ctx = { ch, t: now / 1000, dt, air, haze: this.haze, size: m.size, room: this.light };
    for (const p of this.parts) p.update(c);
    for (const s of this.screens.values()) this.screenFrame(s, now);
    for (const lm of this.lampMeshes.values()) this.light.add(lm.lm);
    this.light.end();
    this.fill.color.setRGB(this.light.fill.x, this.light.fill.y, this.light.fill.z);
    this.post.render(this.scene, this.camera, this.sigma, this.light.mean, dt);
    if (this.keepBoxes.children.length || this.overlayOn) {   // the setup aids, over the picture
      const ac = this.renderer.autoClear;
      this.renderer.autoClear = false;
      this.renderer.setRenderTarget(null);
      this.renderer.render(this.keepScene, this.camera);
      this.renderer.autoClear = ac;
    }
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
      s.mesh.material.color.copy(s.tex ? SCREEN_ON : SCREEN_OFF);
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
        for (let i = 0; i < d.length; i += 4) { r += LIN[d[i]]; g += LIN[d[i + 1]]; b += LIN[d[i + 2]]; }
        // the picture's mean brightness x the screen's area: candela straight out, and its lumens into the room
        const k = (PHOTO.screen_nits * s.area) / (d.length / 4);
        s.lm.set(r * k, g * k, b * k);
      } catch { s.lm.set(0, 0, 0); }
      setLight(s.light, s.lm);
      s.lm.multiplyScalar(POOL.omega);
    }
    if (!src) { s.lm.set(0, 0, 0); s.light.intensity = 0; }
    this.light.add(s.lm);
  }

  /** Every part's words, for the side panel: [{unit, rows: [[part, words]]}]. */
  words(): { unit: string; rows: [string, string][] }[] {
    const m = this.model;
    if (!m) return [];
    const t = performance.now() / 1000;
    const ch = (f: Fixture, i: number) => (f.address == null ? 0 : this.dmx[f.address - 1 + i] ?? 0);
    const c: Ctx = { ch, t, dt: 0, air: true, haze: this.haze, size: m.size, room: this.light };
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
  // -- the camera (owner, 27 Nov: "RESEARCH AND ADOPT BEST PRACTICE FOR 3D INTERACTION IN BROWSER"). three.js's own
  // OrbitControls, set up as the browser's 3D tools agree (three.js, Sketchfab, PlayCanvas): left-drag orbits round
  // the pivot; right-drag, middle-drag or Ctrl / ⌘ / Shift + left-drag pans; the wheel or a pinch zooms towards the
  // cursor; one finger orbits and two pan and pinch on a touch screen; the motion is damped. Double-click focuses on
  // the place clicked (the background: back to the start), F frames the selection: those glide (animStep).
  readonly controls: OrbitControls;
  private frameNo = 0;
  private anim: { t: number; dur: number; t0: THREE.Vector3; t1: THREE.Vector3; p0: THREE.Vector3; p1: THREE.Vector3 } | null = null;

  /** Put the camera on a sphere round ``target`` (three's frame): ``th`` round the vertical, ``ph`` down from it. */
  private orbitPos(target: THREE.Vector3, th: number, ph: number, r: number): THREE.Vector3 {
    return new THREE.Vector3(target.x + r * Math.sin(ph) * Math.sin(th), target.y + r * Math.cos(ph), target.z + r * Math.sin(ph) * Math.cos(th));
  }
  /** Glide the camera and its pivot to a new place (``now``: jump). */
  private glide(target: THREE.Vector3, pos: THREE.Vector3, now = false, dur = 0.5) {
    if (now) {
      this.anim = null;
      this.controls.target.copy(target);
      this.camera.position.copy(pos);
      this.controls.update();
      return;
    }
    this.anim = { t: 0, dur, t0: this.controls.target.clone(), t1: target.clone(), p0: this.camera.position.clone(), p1: pos.clone() };
  }
  private animStep(dt: number) {
    const a = this.anim;
    if (!a) return;
    a.t = Math.min(1, a.t + dt / a.dur);
    const k = a.t * a.t * (3 - 2 * a.t);
    this.controls.target.lerpVectors(a.t0, a.t1, k);
    this.camera.position.lerpVectors(a.p0, a.p1, k);
    if (a.t >= 1) this.anim = null;
  }

  /** The preset views: from the front, from a side, from above, each over the whole room. */
  view(which: "front" | "side" | "above", now = false) {
    const m = this.model;
    if (!m) return;
    const f = v3(m.frontVec).setZ(0).normalize();
    const c = v3(m.centre);
    const depth = Math.abs(f.x) * m.size[0] + Math.abs(f.y) * m.size[1];
    const tgt = this.world(c.clone().addScaledVector(f, depth * 0.25).setZ(Math.min(2, m.size[2] * 0.5)));
    const wf = this.world(f).normalize();                     // the front, in three's frame
    const th0 = Math.atan2(-wf.x, -wf.z);                      // looking along the front: the camera behind the target
    const r = depth * 0.62;
    const [th, ph] = which === "front" ? [th0, 1.45] : which === "side" ? [th0 + 0.85, 1.15] : [th0, 0.8];
    this.glide(tgt, this.orbitPos(tgt, th, ph, r), now);
  }
  /** Back to the start (Home, or a double-click on nothing). */
  home() { this.view("side"); }

  /** Frame a place in the room (F, or picking a thing): the camera glides to look at it from the room's middle side,
   *  ``dist`` metres away, so what is being edited is in front of you. */
  focus(at: Vec3, dist = 1.6) {
    const m = this.model;
    const p = v3(at), c = m ? v3(m.centre) : new THREE.Vector3();
    const tgt = this.world(p);
    const from = this.world(c.clone().sub(p).setZ(0));
    const th = from.lengthSq() > 1e-6 ? Math.atan2(from.x, from.z) : 0;
    this.glide(tgt, this.orbitPos(tgt, th, 1.15, Math.max(0.3, dist)));
  }
  /** What a point of the view (nx, ny: -1..1) lands on in the room: a wall, the floor, a light, a screen. */
  surfaceAt(nx: number, ny: number): THREE.Vector3 | null {
    const ray = new THREE.Raycaster();
    ray.setFromCamera(new THREE.Vector2(nx, ny), this.camera);
    const hit = ray.intersectObjects(this.built.children, true).find((h) => (h.object as THREE.Mesh).isMesh && h.object.visible);
    return hit ? hit.point.clone() : null;
  }
  /** Double-click on a wall or the floor: the pivot goes to the spot clicked (orbiting turns round it), the camera
   *  keeping its direction and coming no nearer than 3 m; on nothing, back to the start. */
  goTo(nx: number, ny: number) {
    const p = this.surfaceAt(nx, ny);
    if (!p) { this.home(); return; }
    const dir = this.camera.position.clone().sub(this.controls.target).normalize();
    const r = Math.min(this.camera.position.distanceTo(p), 3.0);   // no closer than it was, at most 3 m off
    this.glide(p, p.clone().addScaledVector(dir, r));
  }
  /** The wheel (or a pinch): the camera moves along the line through the cursor towards (or away from) what is under
   *  it, a share of the way there each notch, so it slows as it arrives and never goes through; the pivot moves with
   *  it. Nothing under the cursor: by the distance to the pivot. Spread over a few frames (smooth). */
  wheelAt(delta: number, nx: number, ny: number) {
    this.anim = null;
    const ray = new THREE.Raycaster();
    ray.setFromCamera(new THREE.Vector2(nx, ny), this.camera);
    const hit = ray.intersectObjects(this.built.children, true).find((h) => (h.object as THREE.Mesh).isMesh && h.object.visible);
    const far = hit ? hit.distance : this.camera.position.distanceTo(this.controls.target);
    const notch = Math.max(-3, Math.min(3, -delta / 100));               // + in, - out (a mouse notch is about 100)
    let step = far * 0.22 * notch;
    if (step > 0) step = Math.min(step, Math.max(0, far - 0.15));      // stop 15 cm short of the surface
    else step = Math.max(step, -6);
    this.push.addScaledVector(ray.ray.direction, step);
  }
  private push = new THREE.Vector3();
  private pushStep() {
    if (this.push.lengthSq() < 1e-8) return;
    const v = this.push.clone().multiplyScalar(0.3);
    this.push.sub(v);
    this.camera.position.add(v);
    this.controls.target.add(v);
  }

  /** Keep the camera out of the floor always (owner, 27 Nov: "not sure it should be possible for me to move my pov
   *  through the floor"), and inside the room's walls and ceiling when ``keepInside`` (a switch on the page). */
  keepInside = false;
  private keepCamera() {
    const m = this.model;
    if (!m) return;
    const [X, Y, H] = m.size, e = 0.12;                    // three's frame: x east, y up, z = -north
    const p = this.camera.position, t = this.controls.target;
    let moved = false;
    const clamp = (v: THREE.Vector3, lo: [number, number, number], hi: [number, number, number]) => {
      const was = v.clone();
      v.set(Math.max(lo[0], Math.min(hi[0], v.x)), Math.max(lo[1], Math.min(hi[1], v.y)), Math.max(lo[2], Math.min(hi[2], v.z)));
      if (!was.equals(v)) moved = true;
    };
    if (this.keepInside) {
      clamp(p, [e, e, -Y + e], [X - e, H - e, -e]);
      clamp(t, [0, 0, -Y], [X, H, 0]);
    } else {
      clamp(p, [-Infinity, e, -Infinity], [Infinity, Infinity, Infinity]);
    }
    if (moved) this.camera.lookAt(t);
  }

  /** Where the camera is (to come back to after editing). */
  pose(): { target: THREE.Vector3; pos: THREE.Vector3 } {
    return { target: this.controls.target.clone(), pos: this.camera.position.clone() };
  }
  restore(p: { target: THREE.Vector3; pos: THREE.Vector3 }) { this.glide(p.target, p.pos); }

  dispose() {
    this.controls.dispose();
    this.clear();
    this.post.dispose();
    this.renderer.dispose();
  }
}

/** The floor, marked every half metre (to read the room's size by): part of the floor, lit as it is. */
function floorTiles(x: number, y: number): THREE.Texture {
  const c = document.createElement("canvas");
  c.width = c.height = 64;
  const g = c.getContext("2d")!;
  g.fillStyle = "#fff"; g.fillRect(0, 0, 64, 64);
  g.strokeStyle = "#c4c4c4"; g.lineWidth = 2; g.strokeRect(0, 0, 64, 64);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.repeat.set(x * 2, y * 2);
  t.anisotropy = 4;
  return t;
}
/** A screen's white (cd/m2 per unit of its picture), and a screen with no picture: a dark panel. */
const SCREEN_ON = new THREE.Color().setRGB(PHOTO.screen_nits, PHOTO.screen_nits, PHOTO.screen_nits);
const SCREEN_OFF = new THREE.Color().setRGB(0.4, 0.45, 0.6);
/** A picture's 0-255 as linear light. */
const LIN = Float32Array.from({ length: 256 }, (_, v) => { const x = v / 255; return x <= 0.04045 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); });
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
