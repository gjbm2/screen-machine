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
import gobo1 from "./gobos/gobo1.png";
import gobo2 from "./gobos/gobo2.png";
import gobo3 from "./gobos/gobo3.png";
import gobo4 from "./gobos/gobo4.png";
import gobo5 from "./gobos/gobo5.png";
import gobo6 from "./gobos/gobo6.png";
import gobo7 from "./gobos/gobo7.png";
import gobo8 from "./gobos/gobo8.png";
import gobo9 from "./gobos/gobo9.png";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { airCone, airLines, setCone } from "./beams";
import { beamShared, HazePost, SCREEN_ALPHA } from "./fog";
import {
  barStrobe, COLOUR_SLOTS, colourName, DERBY_RGB, hash, HazeState, laserColours, LASER_NM, laserPattern, LASER_RGB, lerp, mixPar, motor, motorWords, MECH,
  pct, shutterGain, SpotMotion, spotShutter,
  type PartKind,
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
// The spots' gobo wheel as the maker draws it (GigBAR MOVE + ILS manual, "Gobos": gobos 1-9; owner, 27 Nov: "how
// confident are you that we have correctly understood the way that gobos work"), cut from the manual's page (white
// passes light). Slot 0 is open: a soft-edged disc.
const GOBO_URLS = [gobo1, gobo2, gobo3, gobo4, gobo5, gobo6, gobo7, gobo8, gobo9];
/** The gobos' spacing on the wheel, in beam widths (the next gobo comes into the beam as one slides out). */
const GOBO_PITCH = 1.15;
/** Each gobo's name (the fixture type's wheel names them the same: gigbar-move-ils-spot.json). */
const GOBO_NAMES = ["no gobo", "ring of dots", "pinwheel", "swirl", "starburst", "four holes", "ovals", "linked rings", "broken ring", "flower"];
/** How much of the open beam each slot lets through (each picture's white share of its disc). */
const GOBO_OPEN: number[] = [1, 0.252, 0.344, 0.376, 0.224, 0.413, 0.157, 0.233, 0.153, 0.329];
const GOBO_TEX: THREE.Texture[] = (() => {
  const c = document.createElement("canvas");
  c.width = c.height = 128;
  const x = c.getContext("2d")!;
  x.fillStyle = "#000"; x.fillRect(0, 0, 128, 128);
  const g = x.createRadialGradient(64, 64, 46, 64, 64, 62);
  g.addColorStop(0, "#fff"); g.addColorStop(1, "#000");
  x.fillStyle = g; x.beginPath(); x.arc(64, 64, 62, 0, Math.PI * 2); x.fill();
  const open = new THREE.CanvasTexture(c);
  open.colorSpace = THREE.SRGBColorSpace;
  const loader = new THREE.TextureLoader();
  return [open, ...GOBO_URLS.map((u) => { const t = loader.load(u); t.colorSpace = THREE.SRGBColorSpace; return t; })];
})();
const dark = new THREE.MeshStandardMaterial({ color: 0x1b1d23, roughness: 0.6, metalness: 0.3 });
const darker = new THREE.MeshStandardMaterial({ color: 0x0e0f13, roughness: 0.7, metalness: 0.2 });
/** A mesh or group whose local +z is turned onto ``dir`` (room frame). */
function aimZ(o: THREE.Object3D, dir: THREE.Vector3) {
  o.quaternion.setFromUnitVectors(Z, dir.clone().normalize());
}

// ---- the parts' live objects -------------------------------------------------------------------------------------
/** air: is there haze to show a beam in. room: where each part reports the lumens it sends into the room. */
interface Ctx { ch: (f: Fixture, i: number) => number; t: number; dt: number; air: boolean; haze: HazeState; size: Vec3; room: RoomLight }
interface Part { f: Fixture; update(c: Ctx): void; words?(c: Ctx): string;
  /** Point a fixed light another way while it is being aimed (a slider or a handle being dragged): drawn at once,
   *  before the change is saved and the room is built again. */
  aim?(dir: THREE.Vector3): void }

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
/** The GigBAR's bar (m): length, height, depth (the maker's picture: 1.1 m; the section estimated). */
const BAR = { len: 1.1, h: 0.048, d: 0.06 };
/** Its four strobe lenses: where along the bar (m from its middle) and their radius. */
const STROBE_ALONG = [-0.49, -0.16, 0.16, 0.49], STROBE_R = 0.014;
export type Nav = "walk" | "orbit" | "plan" | "elev";
/** Which wall an elevation looks at: the one people face (front), the one behind them (back), or a side wall (left /
 *  right as people face the front). */
export type Elev = "front" | "back" | "left" | "right";
const FLAT_FOV_DEG = 3;     // the plan's and elevations' long lens: as good as no perspective
const EYE_M = 1.6;          // where you stand, walking: eye height (m)
const WALL_GAP = 0.25;      // and how near a wall
/** A mirror ball: radius (m), facets drawn, turns a second, the share of light its mirrors throw back, a facet's size
 *  and how much a spot spreads per metre (ESTIMATES: a 40 cm ball on a slow motor). */
const BALL = { r: 0.2, facets: 220, rps: 0.1, reflect: 0.8, facet_m: 0.02, spread: 0.008, dot_m: 0.06 };
/** A pinspot (ESTIMATE: a 10 W LED in a 6-degree lens): its beam and on-axis candela. */
const PINSPOT = { beam_deg: 6, cd: 3000, flash_hz: 8, cycle_s: 1.5 };
/** A remote light's colour words as a colour to draw (the show's scenes name them as the remote does). */
const SHOW_COLOURS: Record<string, [number, number, number]> = { white: [255, 250, 240], red: [255, 40, 30], green: [40, 255, 60],
  blue: [50, 80, 255], yellow: [255, 220, 40], purple: [170, 60, 255], cyan: [40, 230, 255] };
/** The colour cycle ("fade") through the pinspots' remote's colours. */
const PINSPOT_CYCLE: [number, number, number][] = [[255, 40, 30], [40, 255, 60], [50, 80, 255], [255, 220, 40], [170, 60, 255], [40, 230, 255], [255, 250, 240]];
/** Editing a light: how bright the others are drawn (their level channels x this). */
const SOLO_DIM = 0.12;
/** A level channel of a light that is not being edited, drawn dimmed: a par's and a derby's colours, the strobe LEDs, a
 *  head's dimmer, a house light; a laser's colour channel (not a level) goes off; anything else (where it points, its
 *  wheels, its motor) as it is. */
function dimmed(part: PartKind | null, i: number, v: number): number {
  const level = part === "par" ? i <= 5 : part === "derby" ? i <= 3 : part === "flash" ? i <= 3 : part === "flashled" ? i === 0
    : part === "spot" ? i === 7 : part === "dimmer" ? i === 0 : false;
  if (part === "laser" || part === "laser50" || part === "laser51") return i === 0 ? 0 : v;
  return level ? Math.round(v * SOLO_DIM) : v;
}
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

  /** The keep-out boxes to draw: amber, red when a laser's burst crosses one; the one picked filled, the rest outlined. */
  setKeepOut(boxes: { centre: Vec3; size: Vec3; crossed?: boolean; picked?: boolean }[]) {
    for (const c of [...this.keepBoxes.children]) {
      this.keepBoxes.remove(c);
      const m = c as THREE.Mesh;
      m.geometry?.dispose();
      (m.material as THREE.Material | undefined)?.dispose();
    }
    for (const b of boxes) {
      const g = new THREE.BoxGeometry(b.size[0], b.size[1], b.size[2]);
      // only the one picked glows; the rest are a faint outline (owner, 28 Nov: "keep out areas shouldn't glow when
      // not selected"), red where a laser's burst crosses one
      const col = b.crossed ? 0xff4545 : 0xffb020;
      const edge = new THREE.LineSegments(new THREE.EdgesGeometry(g), new THREE.LineBasicMaterial({
        color: col, transparent: true, opacity: b.picked ? 1 : b.crossed ? 0.6 : 0.22, depthTest: false }));
      edge.position.set(b.centre[0], b.centre[1], b.centre[2]);
      this.keepBoxes.add(edge);
      if (b.picked) {
        const fill = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ color: col, transparent: true, opacity: 0.22, depthWrite: false, depthTest: false }));
        fill.position.copy(edge.position);
        this.keepBoxes.add(fill);
      }
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
    oc.enabled = false;                              // walking is the start (setNav)
    this.walkPointer(o.canvas);
    const ring = new THREE.Mesh(new THREE.RingGeometry(0.16, 0.2, 40), new THREE.MeshBasicMaterial({ color: 0xffffff,
      transparent: true, opacity: 0.85, depthTest: false, depthWrite: false, side: THREE.DoubleSide }));
    const disc = new THREE.Mesh(new THREE.CircleGeometry(0.16, 40), new THREE.MeshBasicMaterial({ color: 0xffffff,
      transparent: true, opacity: 0.18, depthTest: false, depthWrite: false, side: THREE.DoubleSide }));
    this.marker.add(ring, disc);
    this.marker.visible = false;
    this.keepRoot.add(this.marker);
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
    for (const f of m.fixtures) if (f.part === "dimmer" && f.at) this.parts.push(this.owned(f.id, () => this.houseLight(f)));
    for (const [bar, fs] of byBar) {
      // a bar (in the room model) or several parts of one unit: the bar; a light on its own: its own mount
      if (fs.length === 1 && !m.bars.some((b) => b.id === bar)) this.buildLone(fs[0], m);
      else this.buildUnit(fs, m);
    }
    for (const f of m.fixtures) {
      if (f.device && f.at) this.buildDevice(f);
      else if (f.kind === "screen" && f.screen && f.plane && f.at) this.buildScreen(f);
      else if (!f.part && f.kind === "lamp" && f.at) this.buildLamp(f);
    }
    for (const p of m.points) if (p.device === "mirror_ball") this.buildBall(v3(p.at));
    // every cone in the air reaches across the room (its mesh only marks where to work the beam out: beams.ts)
    const reach = Math.hypot(...m.size) * 1.05;
    this.built.traverse((o) => { if (o.userData?.airCone) o.scale.setScalar(reach); });
    this.cones = [];
    this.built.traverse((o) => { if (o.userData?.airCone || o.userData?.airLines) this.cones.push(o); });
    const [X, Y, H] = m.size, eps = 0.01;
    this.post.boxMin.set(-eps, -eps, -Y - eps);
    this.post.boxMax.set(X + eps, H + eps, eps);
    this.light.setRoom(m.size);
    if (first) {                                                 // walking: in at the back; orbiting: across the beams
      if (this.nav === "walk") this.standAt("back", true);
      else if (this.flat) { this.lens(true); this.flatFit(true); }
      else this.view("side", true);
    }
  }

  private clear() {
    for (const s of this.screens.values()) s.feed.close();
    this.screens.clear();
    this.parts = [];
    this.afterParts = [];
    this.housings.clear();
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
  // As the maker's datasheet picture of the GigBAR MOVE + ILS shows it (docs/GIGBAR_RIG.md §1.1; the unit 1100 x 144
  // x 449 mm): a flat bar 1.1 m long with a tripod sleeve under its middle; the two moving heads hang under its ends,
  // the two pars hang under it in U-yokes, the two derbies (ribbed drums) stand on it in U-yokes, the laser's box sits
  // on it in the middle in its own bracket, and the four strobes are round lenses in the bar's face. The pars, derbies
  // and laser tilt about the bar's length only (a U-yoke has one axis). Places come from the room model (the layout);
  // the housings are drawn here in the bar's frame: x along it (to the right as you face it), y up, z forward.
  private buildUnit(fs: Fixture[], m: RoomModel) {
    const placed = fs.filter((f) => f.at);
    if (!placed.length) return;
    const up = new THREE.Vector3(0, 0, 1);
    const bar = m.bars.find((b) => b.id === placed[0].bar);
    let front: THREE.Vector3, mid: THREE.Vector3;
    if (bar) {
      front = v3(bar.front).setZ(0);
      mid = v3(bar.at);
    } else {                                                     // a unit with no bar in the room model: its parts' middle
      const spot = placed.find((f) => f.part === "spot" && f.pose);
      const c = placed.reduce((acc, f) => acc.add(v3(f.at!)), new THREE.Vector3()).multiplyScalar(1 / placed.length);
      front = spot ? new THREE.Vector3(1, 0, 0).applyQuaternion(rotQ(spot.pose!.rot)) : v3(m.centre).sub(c);
      front.z = 0;
      mid = c;
    }
    if (front.lengthSq() < 1e-6) front = new THREE.Vector3(0, 1, 0);
    front.normalize();
    const axis = new THREE.Vector3(-front.y, front.x, 0);       // space.py's: + is to the right as you face the bar
    // the bar's frame (right-handed: axis x up = front)
    const unit = new THREE.Group();
    unit.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(axis, up, front));
    unit.position.copy(mid);
    this.built.add(unit);
    const local = (p: THREE.Vector3) => { const d = p.clone().sub(mid); return new THREE.Vector3(d.dot(axis), d.dot(up), d.dot(front)); };
    const box = (w: number, h: number, d: number, mat: THREE.Material, at: [number, number, number], parent: THREE.Object3D = unit) => {
      const o = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat); o.position.set(...at); o.castShadow = false; parent.add(o); return o;
    };
    const cyl = (r: number, len: number, mat: THREE.Material, at: [number, number, number], along: "x" | "y" | "z", parent: THREE.Object3D = unit, seg = 24) => {
      const o = new THREE.Mesh(new THREE.CylinderGeometry(r, r, len, seg), mat); o.position.set(...at);
      if (along === "x") o.rotation.z = Math.PI / 2; else if (along === "z") o.rotation.x = Math.PI / 2;
      parent.add(o); return o;
    };
    const H = BAR.h / 2;
    // the bar, its end caps, the knobs that clamp the brackets, the tripod sleeve and its gusset
    box(BAR.len, BAR.h, BAR.d, darker, [0, 0, 0]);
    for (const s of [-1, 1]) box(0.012, BAR.h + 0.008, BAR.d + 0.008, dark, [s * (BAR.len / 2 + 0.006), 0, 0]);
    cyl(0.026, 0.13, dark, [0, -H - 0.065, 0], "y");
    const g = new THREE.Shape([new THREE.Vector2(-0.075, -H), new THREE.Vector2(0.075, -H), new THREE.Vector2(0, -H - 0.09)]);
    const gusset = new THREE.Mesh(new THREE.ShapeGeometry(g), new THREE.MeshStandardMaterial({ color: 0x1b1d23, roughness: 0.6, metalness: 0.3, side: THREE.DoubleSide }));
    unit.add(gusset);
    // the stand: a pole down from the sleeve, three legs
    const poleTop = mid.z - H - 0.13;
    const pole = new THREE.Mesh(new THREE.CylinderGeometry(0.0175, 0.02, Math.max(0.1, poleTop), 12), dark);
    aimY(pole, up); pole.position.set(mid.x, mid.y, poleTop / 2);
    this.built.add(pole);
    for (let i = 0; i < 3; i++) {
      const a = (i * Math.PI * 2) / 3 + Math.PI / 2;
      const foot = new THREE.Vector3(mid.x + Math.cos(a) * 0.55, mid.y + Math.sin(a) * 0.55, 0), hub = new THREE.Vector3(mid.x, mid.y, 0.75);
      const d = hub.clone().sub(foot);
      const leg = new THREE.Mesh(new THREE.CylinderGeometry(0.012, 0.014, d.length(), 8), dark);
      aimY(leg, d); leg.position.copy(foot).addScaledVector(d, 0.5);
      this.built.add(leg);
    }
    /** A U-yoke from the bar's top (``side`` +1) or bottom (-1) to a pivot, ``half`` wide, with its clamp knobs. */
    const yoke = (pv: THREE.Vector3, half: number, side: 1 | -1) => {
      const from = side * H, len = Math.abs(pv.y - from);
      for (const s of [-1, 1]) {
        box(0.008, len, 0.022, dark, [pv.x + s * half, (pv.y + from) / 2, pv.z]);
        cyl(0.013, 0.016, dark, [pv.x + s * (half + 0.01), pv.y, pv.z], "x");
      }
      box(2 * half, 0.008, 0.022, dark, [pv.x, from + side * 0.004, pv.z]);
      cyl(0.011, 0.02, dark, [pv.x, from + side * 0.01, 0], "y");     // the clamp knob on the bar
    };
    /** A housing that tilts about the bar's length at a pivot, pointing along ``dir`` (room frame). */
    const tilting = (pv: THREE.Vector3, dir: THREE.Vector3, id?: string) => {
      const t = new THREE.Group(); t.position.copy(pv);
      t.rotation.x = -Math.atan2(dir.dot(up), dir.dot(front));     // +z (forward) turned up to the light's elevation
      if (id) this.housings.set(id, (d) => { t.rotation.x = -Math.atan2(d.dot(up), d.dot(front)); });
      unit.add(t); return t;
    };
    for (const f of placed) {
      const at = v3(f.at!), pv = local(at);
      if (f.part === "par") {
        const dir = this.fixedDir(f, front, axis, pv.x < 0 ? -1 : 1, 0.45);
        yoke(pv, 0.085, -1);
        const t = tilting(pv, dir, f.id);
        cyl(0.074, 0.075, darker, [0, 0, -0.005], "z", t, 32);
        cyl(0.06, 0.02, dark, [0, 0, -0.05], "z", t, 24);          // its back, with the fins
        const rim = new THREE.Mesh(new THREE.TorusGeometry(0.068, 0.006, 6, 32), dark); rim.position.z = 0.033; t.add(rim);
        this.parts.push(this.owned(f.id, () => this.par(f, at.clone().addScaledVector(dir, 0.034), dir)));
      } else if (f.part === "derby") {
        const dir = this.fixedDir(f, front, axis, 0, 0.55);
        yoke(pv, 0.1, 1);
        const t = tilting(pv, dir, f.id);
        cyl(0.05, 0.17, darker, [0, 0, 0], "x", t, 28);
        for (const x of [-0.06, -0.02, 0.02, 0.06]) {
          const rib = new THREE.Mesh(new THREE.TorusGeometry(0.051, 0.004, 6, 28), dark); rib.rotation.y = Math.PI / 2; rib.position.x = x; t.add(rib);
        }
        for (const s of [-1, 1]) cyl(0.052, 0.006, dark, [s * 0.088, 0, 0], "x", t, 28);   // its end plates
        this.parts.push(this.owned(f.id, () => this.derby(f, at.clone().addScaledVector(dir, 0.051), dir, axis)));
      } else if (f.part === "flash") {
        this.parts.push(this.owned(f.id, () => this.strobe(f, mid.clone().addScaledVector(front, BAR.d / 2 + 0.002), front, axis)));
      } else if (f.part === "flashled") {
        // the four LEDs patched one by one: drawn once, from the first, whose address starts the bar's five
        // channels (four dimmers, then the shared strobe), as the one-light patch was
        const first = placed.filter((x) => x.part === "flashled").reduce((a, b) => ((a.address ?? 1e9) <= (b.address ?? 1e9) ? a : b));
        if (f === first) this.parts.push(this.owned(f.id, () => this.strobe(f, mid.clone().addScaledVector(front, BAR.d / 2 + 0.002), front, axis)));
      } else if (f.part === "laser" || f.part === "laser50" || f.part === "laser51") {
        const w = this.laserAxes(f, at, front, axis).w;
        yoke(pv, 0.104, 1);
        const t = tilting(pv, w, f.id);
        box(0.19, 0.088, 0.12, darker, [0, 0, 0], t);
        box(0.1, 0.05, 0.004, new THREE.MeshStandardMaterial({ color: 0x2a3140, roughness: 0.15, metalness: 0.6 }), [0, -0.008, 0.061], t);
        for (const x of [-0.02, 0.02]) {
          const ap = new THREE.Mesh(new THREE.CircleGeometry(0.011, 20), new THREE.MeshBasicMaterial({ color: 0x0a0c10 }));
          ap.position.set(x, -0.008, 0.0635); t.add(ap);
        }
        this.parts.push(this.owned(f.id, () => this.laser(f, at.clone().addScaledVector(w, 0.064), front, axis)));
      } else if (f.part === "spot") this.parts.push(this.owned(f.id, () => this.spot(f, front)));
      else if (f.part === "hazer") this.parts.push(this.owned(f.id, () => this.hazer(f)));
    }
    unit.traverse((o) => { if ((o as THREE.Mesh).isMesh) (o as THREE.Mesh).receiveShadow = true; });
  }

  /** How a fixed light on a bar points: its own tilt and turn (the Lights page), else its bar layout's (both in
   *  ``aimDir``), else this view's guess (``lift`` up from the bar's front, ``side`` of the bar's middle). */
  private fixedDir(f: Fixture, front: THREE.Vector3, axis: THREE.Vector3, side: number, lift: number): THREE.Vector3 {
    return f.aimDir ? v3(f.aimDir).normalize()
      : front.clone().addScaledVector(new THREE.Vector3(0, 0, 1), lift).addScaledVector(axis, side * 0.5).normalize();
  }

  /** Build one light's part, its beams in the air marked as its own (userData.fid). */
  private owned<T>(fid: string, make: () => T): T {
    const n0 = this.built.children.length;
    const out = make();
    for (const o of this.built.children.slice(n0)) o.traverse((x) => { if (x.userData?.airCone || x.userData?.airLines) x.userData.fid = fid; });
    return out;
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
  /** ``at``: its lens (the front of its can); ``dir``: which way it points (fixedDir). */
  private par(f: Fixture, at: THREE.Vector3, dir: THREE.Vector3): Part {
    const ang = rad(PHOTO.par.field_deg / 2), omega = coneOmega(ang, 1), area = Math.PI * 0.055 ** 2;
    const l = face(new THREE.CircleGeometry(0.055, 32), PHOTO.par.beam_deg / 2, PHOTO.par.field_deg); aimZ(l, dir); l.position.copy(at); this.built.add(l);
    const light = new THREE.SpotLight(0xffffff, 0, 0, ang, 1, 2);
    light.position.copy(at); light.target.position.copy(at.clone().add(dir));
    this.built.add(light, light.target);
    const b = airCone(ang, 1); aimZ(b, dir); b.position.copy(at); this.built.add(b);
    const pool = this.pool(), cd = new THREE.Vector3(), lm = new THREE.Vector3();
    const pivot = at.clone().addScaledVector(dir, -0.034);
    return {
      f,
      aim: (d) => {
        dir.copy(d).normalize(); at.copy(pivot).addScaledVector(dir, 0.034);
        aimZ(l, dir); l.position.copy(at); aimZ(b, dir); b.position.copy(at);
        light.position.copy(at); light.target.position.copy(at).add(dir);
      },
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
  /** ``at``: the front of its drum; ``ax``: which way it points (fixedDir); ``axis``: the bar's length. */
  private derby(f: Fixture, at: THREE.Vector3, ax: THREE.Vector3, axis: THREE.Vector3): Part {
    const D = PHOTO.derby, half = rad(D.beam_deg / 2), omega = 2 * Math.PI * (1 - Math.cos(half));
    // its lens: the drum's front, a strip along the bar
    const l = face(new THREE.PlaneGeometry(0.15, 0.055), 40, 75);
    l.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(axis, new THREE.Vector3().crossVectors(ax, axis), ax));
    l.position.copy(at); this.built.add(l);
    const faceOmega = 2 * Math.PI * (1 - Math.cos(rad(131 / 2))), faceArea = 0.15 * 0.055;   // the maker's coverage
    const e1 = axis.clone(), e2 = new THREE.Vector3().crossVectors(ax, e1).normalize();
    const tints = DERBY_RGB.map((c) => perLumen(c));
    const beams = Array.from({ length: 12 }, (_, i) => {
      const m = airCone(half, 0.5); m.position.copy(at); this.built.add(m);
      const d = blob(); this.built.add(d);
      return { m, d, ci: i % 4, i };
    });
    const cd = new THREE.Vector3(), lm = new THREE.Vector3(), hit = new THREE.Vector3(), n = new THREE.Vector3();
    let rot = 0;
    const pivot = at.clone().addScaledVector(ax, -0.051);
    return {
      f,
      aim: (d) => {
        ax.copy(d).normalize(); at.copy(pivot).addScaledVector(ax, 0.051);
        e2.crossVectors(ax, e1).normalize();
        l.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(axis, new THREE.Vector3().crossVectors(ax, axis), ax));
        l.position.copy(at);
        for (const bm of beams) bm.m.position.copy(at);
      },
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
    // four round lenses in the bar's face (``at``: its middle), where the maker's picture has them
    const panels = STROBE_ALONG.map((o) => {
      const p = face(new THREE.CircleGeometry(STROBE_R, 24), PHOTO.strobe.beam_deg / 2, 80);
      p.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(axis, new THREE.Vector3(0, 0, 1), front));
      p.position.copy(at).addScaledVector(axis, o);
      const rim = new THREE.Mesh(new THREE.TorusGeometry(STROBE_R + 0.003, 0.003, 6, 24), dark);
      rim.quaternion.copy(p.quaternion); rim.position.copy(p.position);
      this.built.add(p, rim);
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
          setFace(p, one.copy(white).multiplyScalar((PHOTO.strobe.cd / 4) * a), Math.PI * STROBE_R ** 2);
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
    let { w, u0, v0 } = this.laserAxes(f, at, front, axis);
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
      aim: (d: THREE.Vector3) => {                   // tilted another way: its axes again
        w = d.clone().normalize();
        u0 = (Math.abs(w.dot(axis)) < 0.95 ? axis.clone() : front.clone()).addScaledVector(w, -w.dot(axis)).normalize();
        v0 = new THREE.Vector3().crossVectors(w, u0).normalize();
      },
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
    let { w, u0, v0 } = this.laserAxes(f, at, front, axis);
    const span = Math.tan(LASER.span_deg * Math.PI / 360) * 0.55;
    const tints = names.map((nm) => perLumen(LASER_RGB[nm]));
    const dir = new THREE.Vector3(), prev = new THREE.Vector3(), lm = new THREE.Vector3();
    let morph = hash(f.address ?? 7) * 10;
    return {
      f,
      aim: (d: THREE.Vector3) => {                   // tilted another way: its axes again
        w = d.clone().normalize();
        u0 = (Math.abs(w.dot(axis)) < 0.95 ? axis.clone() : front.clone()).addScaledVector(w, -w.dot(axis)).normalize();
        v0 = new THREE.Vector3().crossVectors(w, u0).normalize();
      },
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
    // its base against the bar (or the floor), the yoke's two arms from it, the head between them (the maker's
    // picture: about 0.21 m across the yoke); the pose's z is the pan axis, away from the base
    const baseBox = new THREE.Mesh(new THREE.BoxGeometry(0.13, 0.12, 0.032), dark);
    baseBox.position.z = -0.09; base.add(baseBox);
    const foot = new THREE.Mesh(new THREE.CylinderGeometry(0.05, 0.055, 0.014, 24), darker);
    foot.rotation.x = Math.PI / 2; foot.position.z = -0.068; yoke.add(foot);
    for (const s of [-1, 1]) {
      const arm = new THREE.Mesh(new THREE.BoxGeometry(0.06, 0.014, 0.1), dark);
      arm.position.set(0, s * 0.072, -0.02); yoke.add(arm);
      const hub = new THREE.Mesh(new THREE.CylinderGeometry(0.022, 0.022, 0.01, 16), darker);
      hub.position.set(0, s * 0.081, 0); yoke.add(hub);
    }
    const head = new THREE.Group(); yoke.add(head);
    const can = new THREE.Mesh(new THREE.BoxGeometry(0.1, 0.12, 0.13), darker);
    can.position.z = 0.008; head.add(can);
    const bezel = new THREE.Mesh(new THREE.CylinderGeometry(0.043, 0.046, 0.012, 28), dark);
    bezel.rotation.x = Math.PI / 2; bezel.position.z = 0.074; head.add(bezel);
    const ang = rad(PHOTO.spot.beam_deg / 2), pen = 0.25, omega = coneOmega(ang, pen), area = Math.PI * 0.038 ** 2;
    const hl = face(new THREE.CircleGeometry(0.038, 32), PHOTO.spot.beam_deg / 2, PHOTO.spot.beam_deg * 1.5); hl.position.z = 0.0805; head.add(hl);
    const light = new THREE.SpotLight(0xffffff, 0, 0, ang, pen, 2);
    light.position.z = 0.08; light.target.position.z = 3; head.add(light, light.target);
    light.castShadow = true; light.shadow.mapSize.set(512, 512); light.shadow.camera.near = 0.1; light.shadow.camera.far = 25;
    light.map = GOBO_TEX[0];
    const b = airCone(ang, pen); b.position.z = 0.08; head.add(b);
    const pool = this.pool(), cd = new THREE.Vector3(), lm = new THREE.Vector3(), o = new THREE.Vector3(), d = new THREE.Vector3();
    const mo = new SpotMotion();
    // The wheel between slots (shaking, scrolling, travelling): this head's own picture of the aperture, the two
    // gobos either side of it slid across by how far the wheel has turned (a shake is a rock either way of one).
    let own: { x: CanvasRenderingContext2D; tex: THREE.CanvasTexture; pos: number } | null = null;
    const goboMap = (pos: number): THREE.Texture => {
      const i = Math.floor(pos), fr = pos - i;
      if (fr < 0.01 || fr > 0.99) return GOBO_TEX[Math.round(pos) % 10];
      if (!own) {
        const cv = document.createElement("canvas");
        cv.width = cv.height = 128;
        const tex = new THREE.CanvasTexture(cv);
        tex.colorSpace = THREE.SRGBColorSpace;
        own = { x: cv.getContext("2d")!, tex, pos: -1 };
      }
      if (Math.abs(own.pos - pos) > 0.002) {
        const x = own.x, W = 128, pitch = W * GOBO_PITCH;
        x.fillStyle = "#000"; x.fillRect(0, 0, W, W);
        for (const [k, dx] of [[i, -fr * pitch], [i + 1, (1 - fr) * pitch]] as const) {
          const img = GOBO_TEX[((k % 10) + 10) % 10].image as CanvasImageSource | undefined;
          if (img) x.drawImage(img, dx, 0, W, W);
        }
        own.tex.needsUpdate = true;
        own.pos = pos;
      }
      return own.tex;
    };
    const off = THREE.MathUtils.degToRad(pose.tilt_offset_deg || 0);
    return {
      f,
      update: (c) => {
        mo.step((i) => c.ch(f, i), c.dt, f.panRange, f.tiltRange);
        yoke.rotation.z = THREE.MathUtils.degToRad(pose.pan_sign * mo.pan);           // about the pose's z (pan axis)
        head.rotation.y = THREE.MathUtils.degToRad(pose.tilt_sign * mo.tilt) + off;   // tips z towards x (tilt)
        const dim = c.ch(f, 7) / 255, a = dim * shutterGain(spotShutter(c.ch(f, 8)), c.t, hash(f.id.length + 9));
        const rgb = mo.colour(), g = mo.goboSlot(c.t);
        const map = goboMap(mo.goboPos(c.t));                  // a gobo at rest: the shared picture; else this head's
        if (light.map !== map) light.map = map;
        linear(rgb, cd).multiplyScalar(PHOTO.spot.cd * a);
        setLight(light, cd); setFace(hl, cd, area); setCone(b, cd);
        // where it points now, in the room's frame: its pool there lights the room back
        head.updateWorldMatrix(true, false);
        this.root.worldToLocal(o.set(0, 0, 0.08).applyMatrix4(head.matrixWorld));
        this.root.worldToLocal(d.set(0, 0, 1.08).applyMatrix4(head.matrixWorld)).sub(o).normalize();
        lm.copy(cd).multiplyScalar(omega * GOBO_OPEN[g]);
        c.room.add(lm, this.setPool(pool, o, d, lm, c.size));
        if (cd.lengthSq() > 1e-6) this.beamsNow.push({ o: o.clone(), d: d.clone(), cd: cd.clone(), half: ang });
      },
      words: (c) => {
        const dim = c.ch(f, 7), sh = spotShutter(c.ch(f, 8));
        const name = COLOUR_SLOTS[Math.round(mo.wheel) % 10][0], gobo = Math.round(mo.gobo) % 10;
        const moving = Math.abs(mo.pan - mo.targetPan) > 1 || Math.abs(mo.tilt - mo.targetTilt) > 1 ? " · moving" : "";
        const aim = `pan ${mo.targetPan.toFixed(0)}°, tilt ${mo.targetTilt.toFixed(0)}°${moving}`;
        return dim === 0 || sh.mode === "closed" ? `dark · ${aim}` : `${name} ${Math.round(dim / 2.55)}% · ${gobo ? `gobo ${gobo} (${GOBO_NAMES[gobo]})` : "no gobo"} · ${aim}`;
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
    lm.lm.set(c.r, c.g, c.b).multiplyScalar(PHOTO.lamp_lm * (this.solo && !this.solo.has(f.id) ? SOLO_DIM : 1));
    lm.l.color.setRGB(lm.lm.x, lm.lm.y, lm.lm.z).multiplyScalar(1 / (4 * Math.PI)); lm.l.intensity = 1;
    lm.m.material.color.copy(lm.l.color).multiplyScalar(1 / (Math.PI * 0.09 ** 2));
  }

  /** A light on its own (not a part of a bar): a stand from the floor to it, or a short drop from the ceiling above
   *  it, and the light itself, facing the room's middle. */
  private buildLone(f: Fixture, m: RoomModel) {
    const at = v3(f.at!), up = new THREE.Vector3(0, 0, 1), H = m.size[2];
    const front = v3(m.centre).sub(at).setZ(0);
    if (front.lengthSq() < 1e-6) front.set(0, 1, 0);
    front.normalize();
    const axis = new THREE.Vector3(-front.y, front.x, 0);
    if (f.part !== "hazer") {
      const fromCeiling = at.z > H * 0.6;
      const len = fromCeiling ? Math.max(0.05, H - at.z - 0.1) : Math.max(0.05, at.z - 0.15);
      const rod = new THREE.Mesh(new THREE.CylinderGeometry(0.012, 0.012, len, 8), dark);
      aimY(rod, up); rod.position.set(at.x, at.y, fromCeiling ? at.z + 0.1 + len / 2 : len / 2); this.built.add(rod);
      if (!fromCeiling) {
        const foot = new THREE.Mesh(new THREE.CylinderGeometry(0.16, 0.18, 0.02, 20), dark);
        aimY(foot, up); foot.position.set(at.x, at.y, 0.01); this.built.add(foot);
      }
    }
    const dir = f.aim ? v3(f.aim).sub(at).normalize() : this.fixedDir(f, front, axis, 0, 0.35);   // its point, if given one
    if (f.part === "par") this.parts.push(this.owned(f.id, () => this.par(f, at.clone().addScaledVector(dir, 0.04), dir)));
    else if (f.part === "derby") this.parts.push(this.owned(f.id, () => this.derby(f, at.clone().addScaledVector(dir, 0.05), dir, axis)));
    else if (f.part === "spot") this.parts.push(this.owned(f.id, () => this.spot(f, front)));
    else if (f.part === "laser" || f.part === "laser50" || f.part === "laser51") this.parts.push(this.owned(f.id, () => this.laser(f, at, front, axis)));
    else if (f.part === "flash" || f.part === "flashled") this.parts.push(this.owned(f.id, () => this.strobe(f, at, front, axis)));
    else if (f.part === "hazer") {
      if (f.planned) {                                           // a planned hazer: its box only (it hazes nothing)
        const box = new THREE.Mesh(new THREE.BoxGeometry(0.32, 0.15, 0.39), darker); box.position.copy(at); this.built.add(box);
      } else this.parts.push(this.owned(f.id, () => this.hazer(f)));
    }
    const body = new THREE.Mesh(new THREE.CylinderGeometry(0.07, 0.08, 0.12, 20), darker);   // the light's own body
    aimY(body, dir); body.position.copy(at).addScaledVector(dir, -0.03);
    if (f.part !== "hazer" && f.part !== "spot") this.built.add(body);
  }

  /** A room device that isn't a light: a microphone on its stand, a light sensor, a mirror ball, a lamp, a screen. */
  private buildDevice(f: Fixture) {
    const at = v3(f.at!), up = new THREE.Vector3(0, 0, 1);
    switch (f.device) {
      case "mirror-ball": this.buildBall(at, f.id); break;
      case "hue-lamp": this.buildLamp({ ...f, kind: "lamp" }); break;
      case "mic": {
        const stand = new THREE.Mesh(new THREE.CylinderGeometry(0.008, 0.008, Math.max(0.1, at.z - 0.05), 8), dark);
        aimY(stand, up); stand.position.set(at.x, at.y, Math.max(0.1, at.z - 0.05) / 2); this.built.add(stand);
        const base = new THREE.Mesh(new THREE.CylinderGeometry(0.12, 0.12, 0.015, 20), dark);
        aimY(base, up); base.position.set(at.x, at.y, 0.008); this.built.add(base);
        const head = new THREE.Mesh(new THREE.SphereGeometry(0.03, 16, 12), new THREE.MeshStandardMaterial({ color: 0x6b7280, metalness: 0.6, roughness: 0.4 }));
        head.position.copy(at); this.built.add(head);
        break;
      }
      case "light-sensor": {
        const puck = new THREE.Mesh(new THREE.CylinderGeometry(0.04, 0.04, 0.02, 20), new THREE.MeshStandardMaterial({ color: 0xe5e7eb, roughness: 0.6 }));
        aimY(puck, up); puck.position.copy(at); this.built.add(puck);
        const eye = new THREE.Mesh(new THREE.CircleGeometry(0.012, 16), new THREE.MeshBasicMaterial({ color: 0x1f2937 }));
        eye.position.copy(at).add(new THREE.Vector3(0, 0, 0.011)); this.built.add(eye);
        break;
      }
      case "screen": {
        const pl = f.plane ?? { normal: [0, -1, 0] as Vec3, up: [0, 0, 1] as Vec3, size: [1.2, 0.7] as [number, number] };
        const panel = new THREE.Mesh(new THREE.PlaneGeometry(pl.size[0], pl.size[1]), new THREE.MeshStandardMaterial({ color: 0x111318, roughness: 0.3 }));
        const zN = v3(pl.normal).normalize(), yU = v3(pl.up).normalize(), xR = new THREE.Vector3().crossVectors(yU, zN);
        panel.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(xR, yU, zN));
        panel.position.copy(at).addScaledVector(zN, 0.02); this.built.add(panel);
        const frame = new THREE.Mesh(new THREE.BoxGeometry(pl.size[0] + 0.04, pl.size[1] + 0.04, 0.03), dark);
        frame.quaternion.copy(panel.quaternion); frame.position.copy(at).addScaledVector(zN, 0.005); this.built.add(frame);
        break;
      }
      default:
        if (/glitter|mirror/.test(f.device ?? "")) { this.buildBall(at, f.id); break; }   // a remote-worked mirror ball
        if (/pinspot/.test(f.device ?? "") && this.model) { this.buildPinspot(f, this.model); break; }
        if ((f.device ?? "").startsWith("rf-")) {                                       // another remote-worked thing
          const can = new THREE.Mesh(new THREE.CylinderGeometry(0.05, 0.06, 0.12, 16), darker);
          aimY(can, up); can.position.copy(at); this.built.add(can);
        }
        break;
    }
  }

  // A mirror ball (owner, 27 Nov: "your simulation needs to accommodate them if pointed at a glitterball (and other
  // things) ... does it?"): it turns, and every narrow beam that lands on it this frame (a pinspot, a moving head) is
  // thrown back off its facets as spots on the walls, ceiling and floor, each spot as bright as its share of the light
  // the ball catches (the ball's cross-section seen from the light, BALL.reflect of it back, among the facets facing
  // the light), turning with the ball.
  private buildBall(at: THREE.Vector3, id?: string) {
    const ball = new THREE.Mesh(new THREE.SphereGeometry(BALL.r, 24, 16),
      new THREE.MeshStandardMaterial({ color: 0xcfd4dc, metalness: 1, roughness: 0.15, flatShading: true }));
    ball.position.copy(at); this.built.add(ball);
    const K = BALL.facets, normals: THREE.Vector3[] = [];
    for (let i = 0; i < K; i++) {                          // facets spread evenly over the ball (a Fibonacci sphere)
      const z = 1 - (2 * (i + 0.5)) / K, r = Math.sqrt(1 - z * z), a = i * Math.PI * (3 - Math.sqrt(5));
      normals.push(new THREE.Vector3(Math.cos(a) * r, Math.sin(a) * r, z));
    }
    const MAXB = 6, n = K * MAXB;
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(n * 3), 3));
    g.setAttribute("color", new THREE.BufferAttribute(new Float32Array(n * 3), 3));
    const dots = new THREE.Points(g, new THREE.PointsMaterial({ size: BALL.dot_m, map: dotSprite(), vertexColors: true,
      blending: THREE.AdditiveBlending, transparent: true, depthWrite: false, sizeAttenuation: true }));
    dots.frustumCulled = false; this.built.add(dots);
    const nr = new THREE.Vector3(), rd = new THREE.Vector3(), hit = new THREE.Vector3(), hn = new THREE.Vector3(), din = new THREE.Vector3();
    const toBall = new THREE.Vector3(), lm = new THREE.Vector3();
    let spin = 0, tick = 0;
    const from = new THREE.Vector3(), per = new THREE.Vector3();
    this.afterParts.push({ f: { id: "mirror_ball" } as Fixture, update: (c) => {   // after every light: their beams are in
      const st = id ? this.devState(id) : undefined;
      if (!id || st?.spin !== false) spin += c.dt * BALL.rps * Math.PI * 2;   // its motor: as the mode has it (still: off)
      ball.rotation.z = spin;
      if (++tick % 2) return;                                  // the spots every other frame (a slow ball: no one sees it)
      const pos = g.attributes.position as THREE.BufferAttribute, col = g.attributes.color as THREE.BufferAttribute;
      let k = 0;
      const cos = Math.cos(spin), sin = Math.sin(spin);
      for (const b of this.beamsNow.slice(0, MAXB)) {
        toBall.copy(at).sub(b.o);
        const D = toBall.length();
        if (D < BALL.r * 1.5) continue;
        const along = toBall.dot(b.d);
        if (along <= 0) continue;
        const miss = Math.sqrt(Math.max(0, D * D - along * along));         // how far the beam's axis passes from it
        if (miss > BALL.r + along * Math.tan(b.half)) continue;              // the ball isn't in this beam
        din.copy(toBall).normalize();
        // the light the ball catches: the beam's candela x the ball's solid angle seen from the light
        const omega = (Math.PI * BALL.r * BALL.r) / (D * D);
        lm.copy(b.cd).multiplyScalar(omega * BALL.reflect);
        c.room.add(lm);
        let facing = 0;
        for (const n0 of normals) if (-(n0.x * cos - n0.y * sin) * din.x - (n0.x * sin + n0.y * cos) * din.y - n0.z * din.z > 0) facing++;
        if (!facing) continue;
        per.copy(lm).multiplyScalar(1 / facing);                             // each facing facet's share (lumens)
        for (const n0 of normals) {
          nr.set(n0.x * cos - n0.y * sin, n0.x * sin + n0.y * cos, n0.z);
          const dn = din.dot(nr);
          if (dn >= 0) continue;                                             // facing away from the light
          rd.copy(din).addScaledVector(nr, -2 * dn);                         // the reflection
          const rho = hitFace(from.copy(at).addScaledVector(nr, BALL.r), rd, c.size, hit, hn);
          const L = hit.distanceTo(at), rad = BALL.facet_m + L * BALL.spread;
          const lum = (rho / Math.PI) / (Math.PI * rad * rad);              // cd/m2 per lumen landing there
          pos.setXYZ(k, hit.x + hn.x * 0.005, hit.y + hn.y * 0.005, hit.z + hn.z * 0.005);
          col.setXYZ(k, per.x * lum, per.y * lum, per.z * lum);
          k++;
        }
      }
      g.setDrawRange(0, k);
      dots.visible = k > 0;
      pos.needsUpdate = true; col.needsUpdate = true;
    } });
  }

  /** A pinspot ("glitter spot"): a small can throwing a narrow white beam at what it points at (its aim, else the
   *  nearest mirror ball). No wire tells its state (a radio remote works it): drawn on, unless it is being edited Off. */
  private buildPinspot(f: Fixture, m: RoomModel) {
    const at = v3(f.at!);
    const ball = m.fixtures.find((x) => /mirror|glitter/.test(x.device ?? "") && x.at);
    const pt = m.points.find((p) => p.device === "mirror_ball");
    const tgt = f.aim ? v3(f.aim) : ball ? v3(ball.at!) : pt ? v3(pt.at) : v3(m.centre);
    const d = tgt.clone().sub(at);
    if (d.lengthSq() < 1e-6) d.set(0, 0, -1);
    d.normalize();
    // a can about the size of a real one (10 cm across, 16 long), on a bracket up to the ceiling (or down to the floor)
    const can = new THREE.Mesh(new THREE.CylinderGeometry(0.05, 0.06, 0.16, 20), darker);
    aimY(can, d); can.position.copy(at).addScaledVector(d, -0.05); this.built.add(can);
    const H = m.size[2], up = at.z > H / 2;
    const rodLen = Math.max(0.03, up ? H - at.z : at.z);
    const rod = new THREE.Mesh(new THREE.CylinderGeometry(0.008, 0.008, rodLen, 8), dark);
    rod.position.set(at.x, at.y, up ? at.z + rodLen / 2 : rodLen / 2); rod.rotation.x = Math.PI / 2; this.built.add(rod);
    const plate = new THREE.Mesh(new THREE.CylinderGeometry(0.05, 0.05, 0.01, 16), dark);
    plate.position.set(at.x, at.y, up ? H - 0.005 : 0.005); plate.rotation.x = Math.PI / 2; this.built.add(plate);
    const ang = rad(PINSPOT.beam_deg / 2), lensAt = at.clone().addScaledVector(d, 0.03);
    const l = face(new THREE.CircleGeometry(0.035, 20), PINSPOT.beam_deg / 2, 30); aimZ(l, d); l.position.copy(lensAt); this.built.add(l);
    const b = airCone(ang, 1); aimZ(b, d); b.position.copy(lensAt); this.built.add(b);
    b.userData.fid = f.id;
    const cd = new THREE.Vector3(), white = perLumen([255, 250, 240]);
    this.parts.push({ f, update: (c) => {
      const st = this.devState(f.id);
      const edited = this.solo?.has(f.id) ? this.editShow : null;      // While editing: on / off / as the mode has it
      const on = edited === "on" ? true : edited === "off" ? false : !!st?.on;
      // its colour (the colour cycle: the remote's seven in turn), its level (8 steps), its flash (a strobe: the remote
      // gives no rate, PINSPOT.flash_hz is an estimate)
      const rgb = st?.cycle ? PINSPOT_CYCLE[Math.floor(c.t / PINSPOT.cycle_s) % PINSPOT_CYCLE.length] : st?.rgb;
      const tint = rgb ? perLumen(rgb) : white;
      const flash = st?.flash && edited !== "on" ? ((c.t * PINSPOT.flash_hz) % 1 < 0.2 ? 1 : 0) : 1;
      cd.copy(tint).multiplyScalar(on ? PINSPOT.cd * (st?.level ?? 1) * flash : 0);
      setFace(l, cd, Math.PI * 0.035 ** 2); setCone(b, cd);
      if (on) { this.beamsNow.push({ o: lensAt, d, cd: cd.clone(), half: ang }); c.room.add(cd.clone().multiplyScalar(coneOmega(ang, 1))); }
    } });
  }

  // -- each frame --------------------------------------------------------------------------------------------------
  private last = performance.now();
  frame(now: number) {
    const dt = Math.min(0.1, (now - this.last) / 1000);
    this.last = now;
    if (this.lensTo && !this.anim) {                           // a new lens with no glide to ease it: at once
      this.setLens(this.lensTo.fov, this.lensTo.up, this.camera.position.distanceTo(this.controls.target));
      this.lensTo = null;
    }
    this.animStep(dt);
    this.pushStep();
    if (this.anim?.c) { /* a change of lens: animStep turns the camera itself */ }
    else if (this.nav === "walk") this.walkStep(dt);
    else if (this.flat || this.anim) this.camera.lookAt(this.controls.target);   // (orbit's update would clamp a glide)
    else this.controls.update();
    this.keepCamera();
    if (++this.frameNo % SHADOW_EVERY === 0) this.renderer.shadowMap.needsUpdate = true;
    const m = this.model;
    if (!m) return;
    // "Show beam paths" (an aid): every beam is drawn as in a medium haze that takes nothing out of the light
    const sig = this.flat ? 0 : this.sigma;                     // a plan or an elevation is a drawing: no air in it
    // editing a light: its beam's path shows whatever the haze (owner, 27 Nov: "when I'm pointing a pinspot and/or
    // editing it, why can't I see its haze cone"); the others are dimmed then, so theirs hardly show
    const paths = this.showPaths || !!this.solo;
    // on only for the light being edited (no haze, no "Show beam paths"): its beams alone go through the air pass,
    // never every light's (owner, 27 Nov: "why has simulation performance suddenly gone to shit")
    const soloOnly = !!this.solo && !this.showPaths && sig <= 1e-4;
    if (soloOnly || this.conesSolo) {
      for (const o of this.cones) o.visible = !soloOnly || this.solo!.has(o.userData.fid);
      this.conesSolo = soloOnly;
    }
    const air = paths || sig > 1e-4;
    beamShared.on.value = air;
    beamShared.scatter.value = paths ? Math.max(sig, 0.3) : sig;
    beamShared.sigma.value = paths ? 0 : sig;
    const hl = this.hl, vhl = this.vhl, solo = this.solo;
    const ch = (f: Fixture, i: number) => {
      const v = this.chRaw(f, i, hl, vhl);
      return solo && !solo.has(f.id) ? dimmed(f.part, i, v) : v;          // editing a light: the others stand back
    };
    // every part reports the lumens it sends into the room; their bounce is the fill and what lights the haze
    this.light.begin();
    const c: Ctx = { ch, t: now / 1000, dt, air, haze: this.haze, size: m.size, room: this.light };
    this.beamsNow.length = 0;                                  // the narrow beams this frame (a mirror ball catches them)
    for (const p of this.parts) p.update(c);
    for (const p of this.afterParts) p.update(c);
    for (const s of this.screens.values()) this.screenFrame(s, now);
    for (const lm of this.lampMeshes.values()) this.light.add(lm.lm);
    this.light.end();
    this.fill.color.setRGB(this.light.fill.x, this.light.fill.y, this.light.fill.z);
    this.post.render(this.scene, this.camera, sig, this.light.mean, dt);
    if (this.keepBoxes.children.length || this.overlayOn || this.marker.visible) {   // the setup aids, over the picture
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
    const ch = (f: Fixture, i: number) => (f.address == null ? 0 : f.planned ? this.vhl.get(f.address - 1 + i) ?? 0
      : this.hl.get(f.address - 1 + i) ?? this.dmx[f.address - 1 + i] ?? 0);
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
        } else if (f.part === "flash" || f.part === "flashled") {
          const lv = [0, 1, 2, 3].map((i) => ch(f, i));             // the four LEDs, left to right
          w = Math.max(...lv) ? `white ${lv.map((v) => (v ? `${Math.round(v / 2.55)}%` : "·")).join(" ")}${barStrobe(ch(f, 4)).mode !== "open" ? " · strobing" : ""}` : "off";
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
  // A glide from one view to the next eases the lens too (owner, 27 Nov: "if I flip between plan & 3D why does it
  // re-build the view ... why not just transition smoothly from one to the other (e.g. perspective changes and ease-in
  // / ease-out move)"): the field of view and how much of the room is framed change together (a dolly zoom), the
  // camera swings from one direction to the other, its up turns, all eased in and out.
  private anim: { t: number; dur: number; t0: THREE.Vector3; t1: THREE.Vector3; tEnd: THREE.Vector3; p0: THREE.Vector3; p1: THREE.Vector3;
    f0: number; f1: number; s0: number; s1: number; d0: THREE.Vector3; d1: THREE.Vector3; u0: THREE.Vector3; u1: THREE.Vector3;
    c?: THREE.Vector3; n0?: [number, number]; n1?: [number, number]; r0?: THREE.Quaternion; r1?: THREE.Quaternion } | null = null;
  /** A camera at ``eye`` turned so ``at`` lands at ``n`` on screen (-1..1) with a lens ``fov`` wide, ``up`` up. */
  private static framing(eye: THREE.Vector3, at: THREE.Vector3, up: THREE.Vector3, n: [number, number], fov: number, aspect: number): THREE.Quaternion {
    const tn = Math.tan(THREE.MathUtils.degToRad(fov) / 2);
    const v = new THREE.Vector3(n[0] * tn * aspect, n[1] * tn, -1).normalize();
    return new THREE.Quaternion().setFromRotationMatrix(new THREE.Matrix4().lookAt(eye, at, up))
      .multiply(new THREE.Quaternion().setFromUnitVectors(v, new THREE.Vector3(0, 0, -1)));
  }
  /** OrbitControls' leftover momentum (its damping): cleared when a glide takes the camera and when it hands it
   *  back, or the last drag's spin carried on after the glide arrived (a jerk at its end). */
  private stillControls() {
    const oc = this.controls as unknown as { _sphericalDelta?: { set(a: number, b: number, c: number): void }; _panOffset?: THREE.Vector3; _scale?: number };
    oc._sphericalDelta?.set(0, 0, 0); oc._panOffset?.set(0, 0, 0);
    if (typeof oc._scale === "number") oc._scale = 1;
  }
  /** The lens the next glide arrives at (lens()); applied at once with no glide. */
  private lensTo: { fov: number; up: THREE.Vector3 } | null = null;

  /** Put the camera on a sphere round ``target`` (three's frame): ``th`` round the vertical, ``ph`` down from it. */
  private orbitPos(target: THREE.Vector3, th: number, ph: number, r: number): THREE.Vector3 {
    return new THREE.Vector3(target.x + r * Math.sin(ph) * Math.sin(th), target.y + r * Math.cos(ph), target.z + r * Math.sin(ph) * Math.cos(th));
  }
  /** Glide the camera and its pivot to a new place (``now``: jump). */
  private glide(target: THREE.Vector3, pos: THREE.Vector3, now = false, dur = 0.5) {
    const lt = this.lensTo;
    this.lensTo = null;
    if (now) {
      if (lt) this.setLens(lt.fov, lt.up, pos.distanceTo(target));
      this.anim = null;
      this.controls.target.copy(target);
      this.camera.position.copy(pos);
      if (this.nav === "orbit") this.controls.update();   // (it would pull the plan's far camera in to maxDistance)
      else this.camera.lookAt(target);
      return;
    }
    const c = this.camera, p0 = c.position.clone();
    let t0 = this.controls.target.clone(), t1 = target.clone();
    // a glide replacing one under way still arrives at that one's lens
    const was = this.anim;
    const f0 = c.fov, f1 = lt?.fov ?? was?.f1 ?? f0, half = (f: number) => Math.tan(THREE.MathUtils.degToRad(f) / 2);
    if (Math.abs(f1 - f0) > 1) {
      // A change of lens (into or out of the plan or an elevation; owner, 27 Nov: "cycling between 3D view and plan
      // view still a complete ... mess"): the camera swings round ONE point in the room, the flat view's centre (where
      // the plan or elevation looks), so the room stays in the picture all the way; the lens and the distance change
      // together so the room keeps its size on screen; and the camera's turn is one smooth rotation from exactly how
      // it looks at the start to exactly how it looks at the end (no aiming at a moving point, no roll from an up
      // vector being blended). A pivot pushed out along each line of sight (as far as 160 m, under the floor) swung
      // the room across the screen.
      const c0 = (f1 < f0 ? target : t0).clone();               // the flat end's centre
      const u1 = (lt?.up ?? was?.u1 ?? c.up).clone();
      // Where the centre is ON SCREEN at each end (-1..1 across and up). In between, that point slides straight from
      // one to the other, and the camera turns each frame so the centre lands there with the lens of that frame.
      // (Blending the turn as an angle doesn't hold: half way the lens is about 14 degrees wide, so a 15-degree turn
      // put the room off the screen.)
      const onScreen = (cam: THREE.PerspectiveCamera): [number, number] => {
        cam.updateMatrixWorld(); cam.updateProjectionMatrix();
        const v = c0.clone().applyMatrix4(cam.matrixWorldInverse);
        if (v.z > -1e-3) return [0, 0];                         // behind it: the middle (no better place)
        const n = c0.clone().project(cam);
        return [Math.max(-1.5, Math.min(1.5, n.x)), Math.max(-1.5, Math.min(1.5, n.y))];
      };
      const end = c.clone(); end.fov = f1; end.position.copy(pos); end.up.copy(u1); end.lookAt(target);
      const n0 = onScreen(c), n1 = onScreen(end);
      // what framing the centre leaves out at each end (the turn about the line to it: how the picture is rolled): the
      // real start and end orientations, less the framing's; blended on the way, so the glide starts exactly as the
      // view was and lands exactly as the new one is (no snap in its last frame)
      const fr = RoomSim.framing;
      const rot0 = fr(p0, c0, c.up, n0, f0, c.aspect).invert().multiply(c.quaternion.clone());
      const rot1 = fr(pos, c0, u1, n1, f1, c.aspect).invert().multiply(end.quaternion.clone());
      const r0 = Math.max(1e-3, p0.distanceTo(c0)), r1 = Math.max(1e-3, pos.distanceTo(c0));
      this.stillControls();
      this.anim = { t: 0, dur: Math.max(dur, 0.9), t0, t1, tEnd: target.clone(), p0, p1: pos.clone(), f0, f1,
        s0: 2 * r0 * half(f0), s1: 2 * r1 * half(f1), d0: p0.clone().sub(c0).normalize(), d1: pos.clone().sub(c0).normalize(),
        u0: c.up.clone(), u1, c: c0, n0, n1, r0: rot0, r1: rot1 };
      return;
    }
    const dist0 = Math.max(1e-3, p0.distanceTo(t0)), dist1 = Math.max(1e-3, pos.distanceTo(t1));
    this.anim = { t: 0, dur: Math.abs(f1 - f0) > 1 ? Math.max(dur, 1.0) : dur, t0, t1, tEnd: target.clone(), p0, p1: pos.clone(),
      f0, f1, s0: 2 * dist0 * half(f0), s1: 2 * dist1 * half(f1),
      d0: p0.clone().sub(t0).normalize(), d1: pos.clone().sub(t1).normalize(), u0: c.up.clone(), u1: (lt?.up ?? was?.u1 ?? c.up).clone() };
  }
  /** Set the lens: its field of view and up, and near / far planes that suit a camera ``dist`` from what it frames. */
  private setLens(fov: number, up: THREE.Vector3, dist: number) {
    const c = this.camera;
    c.fov = fov; c.up.copy(up);
    c.near = Math.max(0.02, Math.min(2, dist * 0.004)); c.far = Math.max(80, dist + 60);
    c.updateProjectionMatrix();
  }
  private animStep(dt: number) {
    const a = this.anim;
    if (!a) return;
    a.t = Math.min(1, a.t + dt / a.dur);
    const k = a.t * a.t * (3 - 2 * a.t);
    this.controls.target.lerpVectors(a.t0, a.t1, k);
    if (Math.abs(a.f1 - a.f0) < 0.01) {
      this.camera.position.lerpVectors(a.p0, a.p1, k);       // the same lens: a plain glide
    } else if (a.c && a.n0 && a.n1) {
      // a new lens (glide()): round the flat view's centre, the lens log-eased, the framed size eased, the turn slerped
      const fov = Math.exp(Math.log(a.f0) + (Math.log(a.f1) - Math.log(a.f0)) * k);
      const size = Math.exp(Math.log(a.s0) + (Math.log(a.s1) - Math.log(a.s0)) * k);
      const dist = size / (2 * Math.tan(THREE.MathUtils.degToRad(fov) / 2));
      const dir = a.d0.clone().applyQuaternion(new THREE.Quaternion().slerpQuaternions(new THREE.Quaternion(),
        new THREE.Quaternion().setFromUnitVectors(a.d0, a.d1), k));
      const cam = this.camera;
      cam.position.copy(a.c).addScaledVector(dir, dist);
      const up = a.u0.clone().lerp(a.u1, k);
      if (up.lengthSq() < 1e-6) up.copy(a.u1);
      // look at the centre, then turn so it sits at this frame's place on screen, for this frame's lens
      const n: [number, number] = [a.n0[0] + (a.n1[0] - a.n0[0]) * k, a.n0[1] + (a.n1[1] - a.n0[1]) * k];
      cam.quaternion.copy(RoomSim.framing(cam.position, a.c, up.normalize(), n, fov, cam.aspect));
      if (a.r0 && a.r1) cam.quaternion.multiply(new THREE.Quaternion().slerpQuaternions(a.r0, a.r1, k));
      this.camera.fov = fov;
      this.camera.near = Math.max(0.02, Math.min(2, dist * 0.004)); this.camera.far = Math.max(80, dist * 2 + 60);
      this.camera.updateProjectionMatrix();
    } else {
      // a new lens: the view widens or narrows (log-eased) while the framed size eases, so the room never jumps; the
      // camera swings round from one direction to the other at the distance that keeps that framing
      const fov = Math.exp(Math.log(a.f0) + (Math.log(a.f1) - Math.log(a.f0)) * k);
      const size = Math.exp(Math.log(a.s0) + (Math.log(a.s1) - Math.log(a.s0)) * k);
      const dist = size / (2 * Math.tan(THREE.MathUtils.degToRad(fov) / 2));
      const dir = a.d0.clone().applyQuaternion(new THREE.Quaternion().slerpQuaternions(new THREE.Quaternion(),
        new THREE.Quaternion().setFromUnitVectors(a.d0, a.d1), k));
      this.camera.position.copy(this.controls.target).addScaledVector(dir, dist);
      this.setLens(fov, a.u0.clone().lerp(a.u1, k).normalize(), dist);
    }
    if (a.t >= 1) {
      this.camera.position.copy(a.p1);
      this.controls.target.copy(a.tEnd);
      this.setLens(a.f1, a.u1, a.p1.distanceTo(a.tEnd));
      this.anim = null;
      this.stillControls();
      if (this.nav === "orbit") this.controls.update();       // OrbitControls takes up the pose it arrived at
      else this.camera.lookAt(a.tEnd);
    }
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
    this.setNav("orbit", false);
    this.glide(tgt, this.orbitPos(tgt, th, ph, r), now);
  }
  /** Back to the start (Home, or a double-click on nothing): walking, at the back of the room; orbiting, the side. */
  home() { if (this.nav === "walk") this.standAt("back"); else if (this.flat) this.flatFit(); else this.view("side"); }

  /** Frame a place in the room (F, or picking a thing): the camera glides to look at it from the room's middle side,
   *  ``dist`` metres away, so what is being edited is in front of you. */
  focus(at: Vec3, dist = 1.6) {
    const m = this.model;
    const p = v3(at), c = m ? v3(m.centre) : new THREE.Vector3();
    const tgt = this.world(p);
    const from = this.world(c.clone().sub(p).setZ(0));
    const th = from.lengthSq() > 1e-6 ? Math.atan2(from.x, from.z) : 0;
    if (this.flat) {                                          // a flat view: slide it to the middle, at the same scale
      const d = this.camera.position.distanceTo(this.controls.target), look = this.flatLook();
      this.glide(tgt, tgt.clone().addScaledVector(look, -d));
      return;
    }
    this.setNav("orbit", false);                              // one thing, looked at from all round it
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
    if (this.flat) { this.flatZoom(delta, nx, ny); return; }
    const ray = new THREE.Raycaster();
    ray.setFromCamera(new THREE.Vector2(nx, ny), this.camera);
    const hit = ray.intersectObjects(this.built.children, true).find((h) => (h.object as THREE.Mesh).isMesh && h.object.visible);
    const notch = Math.max(-3, Math.min(3, -delta / 100));               // + in, - out (a mouse notch is about 100)
    if (this.nav === "walk") {
      // walking: a step along the floor towards what is under the cursor (or back), at the same height
      const d = ray.ray.direction.clone().setY(0);
      if (d.lengthSq() < 1e-6) d.copy(this.lookDir()).setY(0);
      d.normalize();
      const flat = hit ? Math.hypot(hit.point.x - this.camera.position.x, hit.point.z - this.camera.position.z) : 4;
      let st = Math.max(0.25, Math.min(1.2, flat * 0.2)) * notch;
      if (st > 0) st = Math.min(st, Math.max(0, flat - 0.5));          // stop half a metre short
      this.push.addScaledVector(d, st);
      return;
    }
    const far = hit ? hit.distance : this.camera.position.distanceTo(this.controls.target);
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

  // -- the selection lights up (owner, 27 Nov: "If I select a component, it should (at least temporarily) light up.
  // Otherwise I can't see what its beam / cone looks like"). While a light is selected (or a bar: all its parts),
  // this view drives it itself: bright open white (a par's and a derby's white, a head's open white, a strobe LED
  // full, the laser's three diodes), its strobe off; where it points and how it turns are left to the wire. Drawn
  // here only: nothing is sent to a light. The rest go on showing what the preview or the wire says.
  /** This frame's narrow beams (moving heads, pinspots): where from, which way, their candela, their half angle. */
  private afterParts: Part[] = [];
  /** Every beam drawn in the air (cones and laser lines), with the light it belongs to (userData.fid). */
  private cones: THREE.Object3D[] = [];
  private conesSolo = false;
  /** The remote-worked things as the previewed mode has them, by id (the page's): unknown -> off (a pinspot), still. */
  deviceStates: Record<string, { on: boolean; spin?: boolean; rgb?: [number, number, number]; level?: number; cycle?: boolean; flash?: boolean; byShow?: boolean }> = {};
  /** The show's scene for the remote-worked things now (the preview's ``a``: pan_spin, intensity, colour_slot, shutter). */
  showAppliances: { pan_spin?: number | { speed?: number }; intensity?: number; colour_slot?: string; shutter?: string; label?: string } | null = null;
  /** A remote-worked thing's state now: its own, or (the show runs it) the show's scene's. */
  private devState(id: string) {
    const st = this.deviceStates[id];
    if (!st?.byShow) return st;
    const a = this.showAppliances;
    if (!a) return { on: false, spin: false };
    const ps = a.pan_spin, speed = typeof ps === "number" ? ps : Number(ps?.speed ?? 0);
    const lvl = Number(a.intensity ?? 0), col = a.colour_slot ?? "white";
    return { on: lvl > 0, spin: Math.abs(speed) > 0, level: Math.max(1 / 8, Math.min(1, lvl)), cycle: col === "fade",
      rgb: SHOW_COLOURS[col] ?? [255, 250, 240], flash: a.shutter === "strobe", byShow: true };
  }                            // what reads the frame's beams (mirror balls)
  private beamsNow: { o: THREE.Vector3; d: THREE.Vector3; cd: THREE.Vector3; half: number }[] = [];
  private hl = new Map<number, number>();                     // DMX slot (0-based) -> the value drawn instead
  private vhl = new Map<number, number>();                    // the same for planned lights' virtual slots
  /** The preview's planned lights: each one's channels by its id (preview.py's frames' ``p``); null: none sent. */
  plannedDmx: Map<string, Uint8Array> | null = null;
  // -- aiming as it is dragged (owner, 27 Nov: "scrubbing a tilt or whatever should update immediately not only on
  // mouseup"): the light, its beam and its housing turn as the slider or handle moves; the room is built again with
  // the saved aim once the change is kept
  private housings = new Map<string, (d: THREE.Vector3) => void>();
  liveAim(id: string, dir: Vec3 | null) {
    const p = this.parts.find((x) => x.f.id === id);
    const d = dir ? v3(dir) : p?.f.aimDir ? v3(p.f.aimDir) : null;     // null: back to how the room has it
    if (!p || !d || d.lengthSq() < 1e-9) return;
    d.normalize();
    p.aim?.(d);
    this.housings.get(id)?.(d);
  }

  /** A light's channel as the wire (or the preview, or the selection lighting it up) has it. */
  private chRaw(f: Fixture, i: number, hl: Map<number, number>, vhl: Map<number, number>): number {
    if (f.planned) {                                                                 // planned: nothing on the wire
      const own = f.address == null ? undefined : vhl.get(f.address - 1 + i);
      return own ?? this.plannedDmx?.get(f.id)?.[i] ?? 0;                             // the preview's, when it runs them
    }
    if (f.address == null || (this.universe != null && f.universe != null && f.universe !== this.universe)) return 0;
    const a = f.address - 1 + i;
    return a >= 0 && a < 512 ? hl.get(a) ?? this.dmx[a] : 0;
  }

  /** The lights being edited (owner, 27 Nov: "when I'm editing any specific light, others should dim and that one
   *  should show so I can see what I'm doing"): every other light's level channels are drawn at SOLO_DIM of what the
   *  wire or the preview says (a laser, whose colour channel can't be dimmed: off); null: nothing being edited. */
  private solo: Set<string> | null = null;
  /** How the light being edited is drawn: on (bright, as it is by default), off, or as the scene has it. */
  editShow: "on" | "off" | "show" = "on";
  highlight(key: string | null, show: "on" | "off" | "show" = this.editShow) {
    this.editShow = show;
    this.hl = new Map();
    this.vhl = new Map();
    const m = this.model;
    const wasSolo = this.solo;
    this.solo = null;
    if (!key || !m) { if (wasSolo && m) this.refreshLamps(m); return; }
    const id = key.slice(key.indexOf(":") + 1), kind = key.slice(0, key.indexOf(":"));
    const fs = kind === "bar" ? m.fixtures.filter((f) => f.bar === id && f.part) : m.fixtures.filter((f) => f.id === id);
    const lights = fs.filter((f) => f.part || f.kind === "lamp" || f.device === "hue-lamp" || /pinspot/.test(f.device ?? ""));
    this.solo = lights.length ? new Set(lights.map((f) => f.id)) : null;
    if (wasSolo || this.solo) this.refreshLamps(m);
    for (const f of fs) {
      if (f.address == null || show === "show") continue;     // as the scene: its own channels, the others still dimmed
      const into = f.planned ? this.vhl : this.hl;
      if (show === "off") {                                  // off: its level channels at 0, the rest as they are
        for (let i = 0; i < 16; i++) if (dimmed(f.part, i, 255) !== 255) into.set(f.address - 1 + i, 0);
        continue;
      }
      const set = (vals: Record<number, number>) => { for (const [i, v] of Object.entries(vals)) into.set(f.address! - 1 + Number(i), v); };
      switch (f.part) {
        case "par": set({ 0: 0, 1: 0, 2: 0, 3: 0, 4: 255, 5: 0, 6: 0 }); break;        // white, no strobe
        case "derby": set({ 0: 0, 1: 0, 2: 0, 3: 255, 4: 0 }); break;               // white, no strobe (it keeps turning)
        case "flash": set({ 0: 255, 1: 255, 2: 255, 3: 255, 4: 0 }); break;
        case "flashled": set({ 0: 255 }); break;
        case "spot": set({ 5: 0, 6: 0, 7: 255, 8: 255 }); break;                    // open white, no gobo, full, open
        case "laser": set({ 0: 240, 1: 0 }); break;                                 // red + green + blue, no strobe
        case "laser50": case "laser51": set({ 0: 20 }); break;                      // its image colour
        case "dimmer": set({ 0: 255 }); break;
        default: break;
      }
    }
  }

  // -- walking (owner, 27 Nov: "mouse just doesn't help me traverse this room in any way I can conventionally relate
  // to"). The first-person way of moving through a room that Matterport, Street View and Sketchfab's first-person
  // mode use: you stand at eye height in the room; dragging turns your head where you stand (the room moves with the
  // pointer, as a photo sphere does); a click on the floor walks you there, on a wall walks you up to it facing it;
  // the wheel steps you towards what is under the cursor or back; W/S or ↑/↓ walk, A/D step aside, ←/→ turn, Q/E go
  // lower and higher (Shift: faster); right-drag (or Ctrl / Shift + drag) slides you sideways and up or down. The
  // camera's pivot is kept a metre ahead of the eye, so the glides, Home and the editing views work unchanged.
  // Orbiting (round a pivot, three.js's OrbitControls) stays for looking at one thing from all round it.
  nav: Nav = "walk";
  /** An editor handle is being dragged: the camera holds still. */
  private held = false;
  hold(on: boolean) { this.held = on; this.controls.enabled = !on && this.nav === "orbit"; }
  private keys = new Set<string>();
  private marker = new THREE.Group();
  private lookDir(): THREE.Vector3 { return this.controls.target.clone().sub(this.camera.position).normalize(); }

  /** Walk or orbit. ``place``: walking from outside the room, go in to stand at its back first. */
  onNav: (mode: Nav) => void = () => {};
  setNav(mode: Nav, place = true) {
    if (mode === this.nav) return;
    const was = this.nav;
    this.nav = mode;
    this.onNav(mode);
    this.keys.clear();
    this.controls.enabled = mode === "orbit" && !this.held;
    this.marker.visible = false;
    const flat = mode === "plan" || mode === "elev";
    this.lens(flat);
    if (flat) { if (place) this.flatFit(); return; }
    if (was === "plan" || was === "elev") {                  // out of a flat view: into the room, or over it
      if (place) { if (mode === "orbit") this.view("above"); else this.standAt("back"); }
      return;                                                // (else the caller's glide moves the camera: a pivot
    }                                                        // put here and OrbitControls' clamp jumped it first)
    if (mode === "orbit") {
      // the pivot goes on what is straight ahead, so orbiting turns round what you were looking at
      const ray = new THREE.Raycaster(this.camera.position.clone(), this.lookDir());
      const hit = ray.intersectObjects(this.built.children, true).find((h) => (h.object as THREE.Mesh).isMesh && h.object.visible);
      this.controls.target.copy(hit ? hit.point : this.camera.position.clone().addScaledVector(this.lookDir(), 3));
      this.controls.update();
      return;
    }
    const m = this.model;
    if (!m || !place) return;
    const p = this.camera.position, [X, Y, H] = m.size;
    const inside = p.x > 0 && p.x < X && p.z < 0 && p.z > -Y && p.y < H;
    if (!inside) { this.standAt("back"); return; }
    // already in the room: down to eye height where you are, looking level the way you were
    const d = this.lookDir().setY(0);
    if (d.lengthSq() < 1e-6) d.set(0, 0, -1);
    const to = new THREE.Vector3(p.x, Math.min(EYE_M, H - 0.2), p.z);
    this.glide(to.clone().add(d.normalize()), to);
  }

  /** The places to stand, for the view's list: each at eye height, looking across the room. */
  standPlaces(): { id: string; label: string }[] {
    return [{ id: "back", label: "The back of the room, looking forward" }, { id: "front", label: "The front, looking back" },
      { id: "middle", label: "The middle" }, { id: "back-left", label: "Back left corner" }, { id: "back-right", label: "Back right corner" },
      { id: "front-left", label: "Front left corner" }, { id: "front-right", label: "Front right corner" }];
  }
  standAt(id: string, now = false) {
    const m = this.model;
    if (!m) return;
    this.setNav("walk", false);
    const [X, Y, H] = m.size;
    const f = v3(m.frontVec).setZ(0);
    if (f.lengthSq() < 1e-6) f.set(0, 1, 0);
    f.normalize();
    const side = new THREE.Vector3(f.y, -f.x, 0);             // to the right as you look forward
    const c = new THREE.Vector3(X / 2, Y / 2, 0);
    const reach = (dir: THREE.Vector3) => {                   // how far from the middle to the wall that way, less a gap
      let best = Infinity;
      for (const [i, L] of [[0, X], [1, Y]] as const) {
        const di = dir.getComponent(i);
        if (Math.abs(di) > 1e-6) best = Math.min(best, ((di > 0 ? L : 0) - c.getComponent(i)) / di);
      }
      return Math.max(0, best - 0.6);
    };
    const along = (d: THREE.Vector3) => c.clone().addScaledVector(d, reach(d));
    const fb = id.startsWith("front") ? 1 : id.startsWith("back") ? -1 : 0;
    const lr = id.endsWith("left") ? -1 : id.endsWith("right") ? 1 : 0;
    let at = c.clone();
    if (fb && lr) at = along(f.clone().multiplyScalar(fb).addScaledVector(side, lr).normalize());
    else if (fb) at = along(f.clone().multiplyScalar(fb));
    const look = id === "front" ? f.clone().negate() : fb && lr ? c.clone().sub(at).setZ(0) : f.clone();   // a corner: across the room
    at.z = Math.min(EYE_M, H - 0.2);
    const pos = this.world(at), tgt = this.world(at.clone().add(look.normalize()));
    this.glide(tgt, pos, now, 0.7);
  }

  /** Where to look from when orbiting, for the view's list (owner, 27 Nov: "better nav options in Orbit mode"): the
   *  whole room from each side, each corner and above, then round each bar and each screen (orbit that thing). */
  orbitPlaces(): { id: string; label: string; group: "room" | "thing" }[] {
    const room: [string, string][] = [["back", "From the back"], ["back-left", "From the back left"], ["back-right", "From the back right"],
      ["left", "From the left"], ["right", "From the right"], ["front", "From the front, looking back"],
      ["front-left", "From the front left"], ["front-right", "From the front right"], ["above", "From above"]];
    const m = this.model;
    const things = m ? [...m.bars.map((b) => ({ id: `bar:${b.id}`, label: `Round ${b.label}` })),
      ...m.fixtures.filter((x) => x.kind === "screen" && x.at).map((x) => ({ id: `fixture:${x.id}`, label: `Round ${x.label}` }))] : [];
    return [...room.map(([id, label]) => ({ id: `room:${id}`, label, group: "room" as const })),
      ...things.map((t) => ({ ...t, group: "thing" as const }))];
  }
  orbitAt(id: string, now = false) {
    const m = this.model;
    if (!m) return;
    const [kind, key] = [id.slice(0, id.indexOf(":")), id.slice(id.indexOf(":") + 1)];
    if (kind === "bar" || kind === "fixture") {
      const at = kind === "bar" ? m.bars.find((b) => b.id === key)?.at : m.fixtures.find((x) => x.id === key)?.at;
      if (at) this.focus(at, 3);
      return;
    }
    if (kind !== "room") return;
    const f = v3(m.frontVec).setZ(0);
    if (f.lengthSq() < 1e-6) f.set(0, 1, 0);
    f.normalize();
    const side = new THREE.Vector3(f.y, -f.x, 0);             // to the right as you look forward
    const c = v3(m.centre);
    const tgt = this.world(c.clone().setZ(Math.min(1.5, m.size[2] * 0.4)));
    const r = Math.hypot(m.size[0], m.size[1]) * 0.75;
    const fb = key.startsWith("front") ? 1 : key.startsWith("back") ? -1 : 0;   // where the camera is, from the middle
    const lr = key.endsWith("left") ? -1 : key.endsWith("right") ? 1 : 0;
    const d = f.clone().multiplyScalar(fb).addScaledVector(side, lr);
    if (d.lengthSq() < 1e-6) d.copy(f).negate();               // above: from the back, nearly overhead
    const w = this.world(d.normalize());
    this.setNav("orbit", false);
    this.glide(tgt, this.orbitPos(tgt, Math.atan2(w.x, w.z), key === "above" ? 0.12 : fb && lr ? 1.05 : 1.2, r), now, 0.7);
  }

  /** A click in the view that picked nothing, while walking: walk there (the floor), or up to it (a wall, the
   *  ceiling, a thing), facing it. False: nothing there. */
  walkTo(nx: number, ny: number): boolean {
    const m = this.model;
    const p = this.surfaceAt(nx, ny);
    if (!m || !p) return false;
    const eye = this.camera.position.y;
    const here = this.camera.position.clone().setY(0);
    const [X, Y] = m.size;
    let dest = p.clone().setY(0);
    let face: THREE.Vector3;
    if (p.y < 0.05) face = this.lookDir().setY(0);                    // the floor: go there, still looking the same way
    else {
      face = p.clone().setY(0).sub(here);
      const dist = face.length();
      dest = here.clone().addScaledVector(face.normalize(), Math.max(0, dist - 1.2));   // up to it, a step short
      face = p.clone().sub(dest.clone().setY(eye));
    }
    dest.x = Math.max(WALL_GAP, Math.min(X - WALL_GAP, dest.x));
    dest.z = Math.max(-Y + WALL_GAP, Math.min(-WALL_GAP, dest.z));
    dest.y = eye;
    if (face.lengthSq() < 1e-6) face.copy(this.lookDir());
    this.glide(dest.clone().add(face.normalize()), dest, false, Math.min(1.1, 0.35 + dest.distanceTo(this.camera.position) * 0.12));
    this.marker.visible = false;
    return true;
  }

  /** While walking, the cursor over the floor shows where a click would take you. */
  walkHint(nx: number | null, ny = 0) {
    if (this.nav !== "walk" || nx === null) { this.marker.visible = false; return; }
    const p = this.surfaceAt(nx, ny);
    if (!p || p.y > 0.05) { this.marker.visible = false; return; }
    this.marker.position.set(p.x, -p.z, 0.01);              // the room's frame (x east, y north, z up)
    this.marker.visible = true;
  }

  /** A key held while the pointer is over the view (the page sends them): true when walking uses it. */
  key(code: string, down: boolean): boolean {
    if (this.nav !== "walk") return false;
    const used = ["KeyW", "KeyA", "KeyS", "KeyD", "KeyQ", "KeyE", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "ShiftLeft", "ShiftRight"];
    if (!used.includes(code)) return false;
    if (down) { this.keys.add(code); this.anim = null; } else this.keys.delete(code);
    return true;
  }
  clearKeys() { this.keys.clear(); }

  /** Turn the head where you stand: ``yaw`` left (+) about the vertical, ``pitch`` up (+), radians. */
  private turnHead(yaw: number, pitch: number) {
    const d = this.lookDir();
    const up = new THREE.Vector3(0, 1, 0);
    d.applyAxisAngle(up, yaw);
    const el = Math.asin(Math.max(-1, Math.min(1, d.y)));
    const lim = THREE.MathUtils.degToRad(85);
    const e2 = Math.max(-lim, Math.min(lim, el + pitch));
    const flat = d.clone().setY(0).normalize();
    d.copy(flat.multiplyScalar(Math.cos(e2))).setY(Math.sin(e2));
    this.controls.target.copy(this.camera.position).add(d);
    this.camera.lookAt(this.controls.target);
  }
  private walkStep(dt: number) {
    const k = this.keys;
    if (k.size) {
      const fast = k.has("ShiftLeft") || k.has("ShiftRight") ? 2.2 : 1;
      const fwd = this.lookDir().setY(0);
      if (fwd.lengthSq() < 1e-6) fwd.set(0, 0, -1);
      fwd.normalize();
      const right = new THREE.Vector3(-fwd.z, 0, fwd.x);
      const v = new THREE.Vector3();
      if (k.has("KeyW") || k.has("ArrowUp")) v.add(fwd);
      if (k.has("KeyS") || k.has("ArrowDown")) v.sub(fwd);
      if (k.has("KeyD")) v.add(right);
      if (k.has("KeyA")) v.sub(right);
      if (v.lengthSq() > 0) v.normalize().multiplyScalar(1.6 * fast * dt);
      if (k.has("KeyE")) v.y += 0.9 * fast * dt;
      if (k.has("KeyQ")) v.y -= 0.9 * fast * dt;
      this.camera.position.add(v);
      this.controls.target.add(v);
      const turn = (k.has("ArrowLeft") ? 1 : 0) - (k.has("ArrowRight") ? 1 : 0);
      if (turn) this.turnHead(turn * 1.6 * fast * dt, 0);
    }
    this.camera.lookAt(this.controls.target);
  }

  /** The pointer, while walking: left-drag turns the head (the room follows the pointer); right-drag, or Ctrl / ⌘ /
   *  Shift + left-drag, slides you sideways and up or down. A press that does not move is a click (the page's). */
  private walkPointer(el: HTMLCanvasElement) {
    let drag: { x: number; y: number; slide: boolean; id: number } | null = null;
    el.addEventListener("pointerdown", (e) => {
      if (this.nav === "orbit" || this.held || (e.button !== 0 && e.button !== 2 && e.button !== 1)) return;
      this.anim = null;
      drag = { x: e.clientX, y: e.clientY, slide: e.button === 2 || e.ctrlKey || e.metaKey || e.shiftKey, id: e.pointerId };
      try { el.setPointerCapture(e.pointerId); } catch { /* not ours to capture */ }
    });
    el.addEventListener("pointermove", (e) => {
      if (!drag || this.held || e.pointerId !== drag.id) return;
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      drag.x = e.clientX; drag.y = e.clientY;
      const perPx = THREE.MathUtils.degToRad(this.camera.fov) / Math.max(1, this.h);   // the room stays under the pointer
      if (this.flat) {                                       // a flat view: any drag slides the drawing under the pointer
        const m = (2 * this.camera.position.distanceTo(this.controls.target) * Math.tan(THREE.MathUtils.degToRad(this.camera.fov) / 2)) / Math.max(1, this.h);
        const ax = new THREE.Vector3().setFromMatrixColumn(this.camera.matrixWorld, 0);
        const ay = new THREE.Vector3().setFromMatrixColumn(this.camera.matrixWorld, 1);
        const v = ax.multiplyScalar(-dx * m).addScaledVector(ay, dy * m);
        this.camera.position.add(v);
        this.controls.target.add(v);
        return;
      }
      if (drag.slide) {
        const fwd = this.lookDir().setY(0).normalize();
        const right = new THREE.Vector3(-fwd.z, 0, fwd.x);
        const v = right.multiplyScalar(-dx * 0.006).add(new THREE.Vector3(0, dy * 0.006, 0));
        this.camera.position.add(v);
        this.controls.target.add(v);
      } else this.turnHead(dx * perPx, dy * perPx);
    });
    const end = (e: PointerEvent) => {
      if (!drag || e.pointerId !== drag.id) return;
      drag = null;
      try { el.releasePointerCapture(e.pointerId); } catch { /* already gone */ }
    };
    el.addEventListener("pointerup", end);
    el.addEventListener("pointercancel", end);
  }

  // -- the flat views: the plan and the elevations (owner, 27 Nov: "wouldn't a 3D view from above with objects
  // selectable and draggable make more sense (and then just make the perspective infinite)"; "Don't I need X and Y
  // elevation as well as Plan?"). The same camera, far off with a long lens (FLAT_FOV_DEG: as good as no perspective,
  // as a drawing is): the plan from above with the wall people face at the top; an elevation level, looking at one
  // wall (front, back, left, right), the floor at the bottom. Any drag slides the drawing; the wheel zooms to the
  // cursor; the editor picks things up straight away (in an elevation, across and up the wall's plane).
  elev: Elev = "front";
  get flat(): boolean { return this.nav === "plan" || this.nav === "elev"; }
  /** Show the plan, or an elevation looking at ``side``'s wall. */
  showFlat(which: "plan" | Elev) {
    if (which === "plan") { if (this.nav === "plan") this.flatFit(); else this.setNav("plan"); return; }   // one glide
    const was = this.nav;
    this.elev = which;
    if (was !== "elev") this.setNav("elev");
    else { this.lens(true); this.flatFit(); this.onNav("elev"); }
  }
  /** The room's directions on the floor: forward (towards the wall people face) and to the right as they face it. */
  private roomAxes(): { f: THREE.Vector3; r: THREE.Vector3 } {
    const m = this.model;
    const f = m ? v3(m.frontVec).setZ(0) : new THREE.Vector3(0, 1, 0);
    if (f.lengthSq() < 1e-6) f.set(0, 1, 0);
    f.normalize();
    return { f, r: new THREE.Vector3(f.y, -f.x, 0) };
  }
  /** Which way a flat view looks, in the room's frame. */
  flatLookRoom(): THREE.Vector3 {
    if (this.nav === "plan") return new THREE.Vector3(0, 0, -1);
    const { f, r } = this.roomAxes();
    return this.elev === "front" ? f : this.elev === "back" ? f.clone().negate() : this.elev === "right" ? r : r.clone().negate();
  }
  private flatLook(): THREE.Vector3 { return this.world(this.flatLookRoom()).normalize(); }
  /** The lens the view is to arrive at: the flat views' long one (the plan with the front wall at the top), else the
   *  3D one. The next glide eases into it (with none, frame() sets it at once). */
  private lens(flat: boolean) {
    const up = flat && this.nav === "plan" ? this.world(this.roomAxes().f).normalize() : new THREE.Vector3(0, 1, 0);
    this.lensTo = { fov: flat ? FLAT_FOV_DEG : 70, up };
  }
  /** How far off a flat view's camera is to show ``w`` x ``h`` metres. */
  private flatDist(w: number, h: number): number {
    const t = 2 * Math.tan(THREE.MathUtils.degToRad(FLAT_FOV_DEG) / 2);
    return Math.max(h / t, w / (t * Math.max(0.1, this.camera.aspect)));
  }
  /** The whole room in the flat view. */
  flatFit(now = false) {
    const m = this.model;
    if (!m) return;
    const [X, Y, H] = m.size;
    const ext = (u: THREE.Vector3) => Math.abs(u.x) * X + Math.abs(u.y) * Y;
    const { f, r } = this.roomAxes();
    const plan = this.nav === "plan";
    const across = plan ? ext(r) : ext(new THREE.Vector3().crossVectors(this.flatLookRoom(), new THREE.Vector3(0, 0, 1)));
    const tall = plan ? ext(f) : H;
    const t = this.world(new THREE.Vector3(X / 2, Y / 2, plan ? 0 : H / 2));
    this.glide(t, t.clone().addScaledVector(this.flatLook(), -this.flatDist(across * 1.12, tall * 1.12)), now, 0.5);
  }
  private flatZoom(delta: number, nx: number, ny: number) {
    const m = this.model;
    const p = this.camera.position, t = this.controls.target, look = this.flatLook();
    const d = p.distanceTo(t);
    const ray = new THREE.Raycaster();
    ray.setFromCamera(new THREE.Vector2(nx, ny), this.camera);
    const at = new THREE.Vector3();
    if (!ray.ray.intersectPlane(new THREE.Plane().setFromNormalAndCoplanarPoint(look, t), at)) return;
    const notch = Math.max(-3, Math.min(3, -delta / 100));
    const big = m ? Math.max(...m.size) * 3 : 30;
    const d2 = Math.max(this.flatDist(0.8, 0.8), Math.min(this.flatDist(big, big), d * Math.pow(0.82, notch)));
    const k = d2 / d;                                        // the point under the cursor stays there
    const off = t.clone().sub(at);                           // (worked out before the pivot moves)
    t.copy(at).addScaledVector(off, k);
    p.copy(t).addScaledVector(look, -d2);
  }

  /** Keep the camera out of the floor always (owner, 27 Nov: "not sure it should be possible for me to move my pov
   *  through the floor"), and inside the room's walls and ceiling when ``keepInside`` (a switch on the page). */
  keepInside = false;
  private keepCamera() {
    const m = this.model;
    if (!m || this.flat || this.anim) return;                  // (a glide in from outside isn't pulled in mid-way)
    const [X, Y, H] = m.size, e = 0.12;                    // three's frame: x east, y up, z = -north
    const p = this.camera.position, t = this.controls.target;
    let moved = false;
    const clamp = (v: THREE.Vector3, lo: [number, number, number], hi: [number, number, number]) => {
      const was = v.clone();
      v.set(Math.max(lo[0], Math.min(hi[0], v.x)), Math.max(lo[1], Math.min(hi[1], v.y)), Math.max(lo[2], Math.min(hi[2], v.z)));
      if (!was.equals(v)) moved = true;
    };
    if (this.nav === "walk") {
      const was = p.clone();
      clamp(p, [WALL_GAP, 0.25, -Y + WALL_GAP], [X - WALL_GAP, H - 0.15, -WALL_GAP]);
      if (moved) { t.add(p.clone().sub(was)); return; }      // the view keeps its direction against a wall
    } else if (this.keepInside) {
      clamp(p, [e, e, -Y + e], [X - e, H - e, -e]);
      clamp(t, [0, 0, -Y], [X, H, 0]);
    } else {
      clamp(p, [-Infinity, e, -Infinity], [Infinity, Infinity, Infinity]);
    }
    if (moved) this.camera.lookAt(t);
  }

  /** Where the camera is (to come back to after editing). */
  pose(): { target: THREE.Vector3; pos: THREE.Vector3; nav: Nav } {
    return { target: this.controls.target.clone(), pos: this.camera.position.clone(), nav: this.nav };
  }
  restore(p: { target: THREE.Vector3; pos: THREE.Vector3; nav?: Nav }) {
    if (p.nav) this.setNav(p.nav, false);
    this.glide(p.target, p.pos);
  }

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
