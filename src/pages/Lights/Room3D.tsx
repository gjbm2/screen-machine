// The Light setup page's 3D view (?tab=3d): the drawing room as the Room map has it (show_service's space), in 3D.
//   - The room's box (the front wall a shade darker), the screens with the picture each is showing now
//     (/output/<screen>.jpg, read again every minute), the Hue lamps in the colour room_service has them at, the mirror
//     ball turning, the piano where its area is, and each registered light's beam to where it lands (GET /rig/live).
//   - A planned light (a space appliance whose kind is "gigbar-2", not yet a registered light) is drawn as a Chauvet
//     GigBAR 2 on its stand: derbies, pars, laser and strobes. Nothing drives it yet, so "Preview" plays what it could
//     do (Calm / Party) in the Hue lamps' colours; it says so on the page.
//   - Tap a bar (or its chip) to move it: from the TV wall, along it, height, turn and tilt. Each change is saved to the
//     room (PUT /rig/space/appliances/<id>: at, mount, plane), so the Room map and later the real light keep it.
//   - Under the bar, what its place means: how much of its pars' light and derby dots land on the screens, and how much
//     of the laser passes through head height (room3d.ts).
// The geometry is room3d.ts (pure). three.js comes in with this tab only (index.tsx lazy-loads this file).

import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { Minus, Plus } from "lucide-react";
import { cn } from "@/lib/utils";
import { Notice, Spinner } from "@/pages/Room/bits";
import { CARD_CLASS } from "@/pages/Room/theme";
import { explainLightsError, type LightsApi } from "./api";
import type { Placeable, RoomSpace } from "./lib";
import {
  aimAngles,
  aimFrom,
  BAR_LENGTH_M,
  barFrame,
  DERBY_BEAMS,
  derbyDirs,
  emitterAt,
  GIGBAR_EMITTERS,
  GIGBAR_KIND,
  hitRoom,
  laserDirs,
  PAR_HALF_ANGLE_DEG,
  reportBar,
  surfaceWords,
  type BarFrame,
  type Picture,
  type V3,
} from "./room3d";
import { useLive, type SpaceState } from "./useRig";

type Demo = "off" | "calm" | "party";
type View = "guest" | "overview" | "above";

const CHIP = "min-h-10 rounded-full border px-4 text-[15px] font-medium transition-colors";
const CHIP_ON = "border-teal-600 bg-teal-600 text-white";
const CHIP_OFF = "border-stone-200 bg-card text-stone-700 hover:bg-stone-100";
const FALLBACK_SIZE: V3 = [5, 10, 3];
const FALLBACK_PALETTE = ["#ff3b6b", "#3b7bff", "#ffb03b", "#3bffb0"];
/** A derby's own lenses (OFL's colour presets: red, green, blue, white), for Party. */
const DERBY_LENSES = ["#ff2030", "#20ff40", "#3050ff", "#ffffff"].map((h) => new THREE.Color(h));
const PICTURE_REFRESH_MS = 60_000;
const SAVE_AFTER_MS = 500;
const BPM = 120;

export interface LampColour {
  hex: string | null;
  dim: number;
}

interface Bar {
  id: string;
  label: string;
  at: V3;
  normal: V3;
}

const v3 = (p: V3) => new THREE.Vector3(p[0], p[1], p[2]);
const pct = (x: number) => `${Math.round(x * 100)}%`;

function Stepper({ label, value, onStep, step, unit }: { label: string; value: number; onStep: (d: number) => void; step: number; unit: string }) {
  return (
    <div className="flex items-center justify-between gap-3 py-1.5">
      <span className="text-[15px] text-stone-700">{label}</span>
      <div className="flex items-center gap-2">
        <button type="button" aria-label={`${label} less`} onClick={() => onStep(-step)} className="flex h-10 w-10 items-center justify-center rounded-full border border-stone-200 bg-card hover:bg-stone-100">
          <Minus className="h-4 w-4" aria-hidden />
        </button>
        <span className="w-20 text-center tabular-nums text-[15px]">
          {unit === "m" ? value.toFixed(2) : Math.round(value)}
          {unit === "m" ? " m" : "°"}
        </span>
        <button type="button" aria-label={`${label} more`} onClick={() => onStep(step)} className="flex h-10 w-10 items-center justify-center rounded-full border border-stone-200 bg-card hover:bg-stone-100">
          <Plus className="h-4 w-4" aria-hidden />
        </button>
      </div>
    </div>
  );
}

// -- the GigBAR's drawing -----------------------------------------------------------------------------------------------

const BEAM_GEO = (() => {
  // A unit cone with its tip at the origin, opening along +Y (scaled to each beam's length and spread).
  const g = new THREE.CylinderGeometry(1, 0, 1, 24, 1, true);
  g.translate(0, 0.5, 0);
  return g;
})();
const DISC_GEO = new THREE.CircleGeometry(1, 24);
const Y = new THREE.Vector3(0, 1, 0);
const Z = new THREE.Vector3(0, 0, 1);

function glow(color: string, opacity: number) {
  return new THREE.MeshBasicMaterial({ color, transparent: true, opacity, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide, toneMapped: false });
}

/** A beam: a cone from `o` along `d` to where it lands, with a pool of light there. */
class Beam {
  cone: THREE.Mesh;
  pool: THREE.Mesh;
  constructor(parent: THREE.Object3D, coneOpacity: number, poolOpacity: number) {
    this.cone = new THREE.Mesh(BEAM_GEO, glow("#ffffff", coneOpacity));
    this.pool = new THREE.Mesh(DISC_GEO, glow("#ffffff", poolOpacity));
    parent.add(this.cone, this.pool);
  }
  set(o: V3, d: V3, size: V3, pictures: Picture[], halfAngle: number, color: THREE.Color, level: number) {
    const h = level > 0.01 ? hitRoom(o, d, size, pictures) : null;
    this.cone.visible = this.pool.visible = !!h;
    if (!h) return;
    const r = Math.max(0.004, h.dist * Math.tan((halfAngle * Math.PI) / 180));
    this.cone.position.set(o[0], o[1], o[2]);
    this.cone.quaternion.setFromUnitVectors(Y, v3(d));
    this.cone.scale.set(r, h.dist, r);
    this.pool.position.set(h.at[0] + h.normal[0] * 0.01, h.at[1] + h.normal[1] * 0.01, h.at[2] + h.normal[2] * 0.01);
    this.pool.quaternion.setFromUnitVectors(Z, v3(h.normal));
    this.pool.scale.setScalar(r);
    (this.cone.material as THREE.MeshBasicMaterial).color.copy(color).multiplyScalar(level);
    (this.pool.material as THREE.MeshBasicMaterial).color.copy(color).multiplyScalar(level);
  }
  hide() {
    this.cone.visible = this.pool.visible = false;
  }
}

/** One GigBAR 2 on its stand, and its light. */
class GigbarModel {
  id: string;
  body = new THREE.Group();
  light = new THREE.Group();
  frame: BarFrame;
  private stand = new THREE.Group();
  private pars: { beam: Beam; spot: THREE.SpotLight; at: V3 }[] = [];
  private derbies: { beams: Beam[]; at: V3; n: number }[] = [];
  private strobes: THREE.Mesh[] = [];
  private flash = new THREE.PointLight("#ffffff", 0, 8, 1.5);
  private laser: THREE.LineSegments;
  private laserDots: THREE.Points;
  private selectRing: THREE.Mesh;

  constructor(id: string, bar: Bar) {
    this.id = id;
    this.frame = barFrame(bar.at, bar.normal);
    const black = new THREE.MeshStandardMaterial({ color: "#1b1b1d", roughness: 0.5, metalness: 0.4 });
    const housing = new THREE.Mesh(new THREE.BoxGeometry(BAR_LENGTH_M, 0.11, 0.09), black);
    housing.userData.bar = id;
    this.body.add(housing);
    const lens = new THREE.MeshStandardMaterial({ color: "#2a2a30", roughness: 0.2, metalness: 0.8 });
    for (const e of GIGBAR_EMITTERS) {
      const m =
        e.kind === "strobe"
          ? new THREE.Mesh(new THREE.BoxGeometry(0.1, 0.022, 0.01), new THREE.MeshStandardMaterial({ color: "#d8d8e0", emissive: "#ffffff", emissiveIntensity: 0 }))
          : new THREE.Mesh(new THREE.CircleGeometry(e.kind === "laser" ? 0.015 : 0.04, 20), lens);
      m.position.set(e.right, e.up, 0.046);
      m.userData.bar = id;
      this.body.add(m);
      if (e.kind === "strobe") this.strobes.push(m);
    }
    this.selectRing = new THREE.Mesh(new THREE.BoxGeometry(BAR_LENGTH_M + 0.08, 0.17, 0.13), new THREE.MeshBasicMaterial({ color: "#14b8a6", wireframe: true }));
    this.selectRing.visible = false;
    this.body.add(this.selectRing);

    const steel = new THREE.MeshStandardMaterial({ color: "#3a3a3e", roughness: 0.6, metalness: 0.6 });
    const pole = new THREE.Mesh(new THREE.CylinderGeometry(0.018, 0.022, 1, 12), steel);
    pole.rotation.x = Math.PI / 2;
    pole.userData.pole = true;
    this.stand.add(pole);
    for (let k = 0; k < 3; k++) {
      const leg = new THREE.Mesh(new THREE.CylinderGeometry(0.012, 0.012, 0.75, 8), steel);
      leg.userData.leg = k;
      this.stand.add(leg);
    }

    for (const e of GIGBAR_EMITTERS) {
      if (e.kind === "par") {
        const spot = new THREE.SpotLight("#ffffff", 0, 12, (PAR_HALF_ANGLE_DEG * Math.PI) / 180, 0.5, 1.2);
        this.light.add(spot, spot.target);
        this.pars.push({ beam: new Beam(this.light, 0.025, 0.18), spot, at: [0, 0, 0] });
      } else if (e.kind === "derby") {
        this.derbies.push({ beams: Array.from({ length: DERBY_BEAMS }, () => new Beam(this.light, 0.07, 0.8)), at: [0, 0, 0], n: e.n });
      }
    }
    this.light.add(this.flash);
    const lg = new THREE.BufferGeometry();
    lg.setAttribute("position", new THREE.BufferAttribute(new Float32Array(25 * 2 * 3), 3));
    lg.setAttribute("color", new THREE.BufferAttribute(new Float32Array(25 * 2 * 3), 3));
    this.laser = new THREE.LineSegments(lg, new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.55, blending: THREE.AdditiveBlending, depthWrite: false, toneMapped: false }));
    const dg = new THREE.BufferGeometry();
    dg.setAttribute("position", new THREE.BufferAttribute(new Float32Array(25 * 3), 3));
    dg.setAttribute("color", new THREE.BufferAttribute(new Float32Array(25 * 3), 3));
    this.laserDots = new THREE.Points(dg, new THREE.PointsMaterial({ size: 0.05, vertexColors: true, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false, toneMapped: false }));
    this.light.add(this.laser, this.laserDots);
    this.place(bar);
  }

  get objects() {
    return [this.body, this.stand, this.light];
  }

  place(bar: Bar) {
    const f = (this.frame = barFrame(bar.at, bar.normal));
    const m = new THREE.Matrix4().makeBasis(v3(f.right), v3(f.up), v3(f.fwd));
    this.body.quaternion.setFromRotationMatrix(m);
    this.body.position.copy(v3(f.at));
    // The stand: a pole straight down from under the bar to a tripod on the floor.
    const top = f.at[2] - 0.07;
    for (const c of this.stand.children) {
      if (c.userData.pole) {
        c.position.set(f.at[0], f.at[1], top / 2);
        c.scale.set(1, Math.max(0.1, top), 1);
      } else {
        const k = c.userData.leg as number;
        const a = (k * 2 * Math.PI) / 3 + Math.atan2(f.fwd[1], f.fwd[0]);
        const foot = new THREE.Vector3(f.at[0] + Math.cos(a) * 0.45, f.at[1] + Math.sin(a) * 0.45, 0);
        const hip = new THREE.Vector3(f.at[0], f.at[1], 0.55);
        c.position.copy(foot.clone().add(hip).multiplyScalar(0.5));
        c.quaternion.setFromUnitVectors(Y, hip.clone().sub(foot).normalize());
        c.scale.set(1, hip.distanceTo(foot) / 0.75, 1);
      }
    }
    let p = 0;
    let d = 0;
    for (const e of GIGBAR_EMITTERS) {
      if (e.kind === "par") this.pars[p++].at = emitterAt(f, e);
      if (e.kind === "derby") this.derbies[d++].at = emitterAt(f, e);
    }
  }

  select(on: boolean) {
    this.selectRing.visible = on;
  }

  /** Draw the light at time `t` (s) in a preview mode. */
  update(t: number, demo: Demo, palette: THREE.Color[], size: V3, pictures: Picture[]) {
    const f = this.frame;
    const beat = (t * BPM) / 60;
    const n = palette.length;
    if (demo === "off") {
      for (const p of this.pars) {
        p.beam.hide();
        p.spot.intensity = 0;
      }
      for (const dz of this.derbies) dz.beams.forEach((b) => b.hide());
      this.strobes.forEach((s) => ((s.material as THREE.MeshStandardMaterial).emissiveIntensity = 0));
      this.flash.intensity = 0;
      this.laser.visible = this.laserDots.visible = false;
      return;
    }
    const party = demo === "party";
    // Pars: a slow crossfade round the palette (Calm), a new colour on every beat (Party).
    this.pars.forEach((p, i) => {
      const c = party ? palette[(Math.floor(beat) + i) % n].clone() : palette[Math.floor(t / 6 + i) % n].clone().lerp(palette[(Math.floor(t / 6 + i) + 1) % n], (t / 6) % 1);
      const level = party ? 0.55 + 0.45 * Math.max(0, 1 - (beat % 1) * 2) : 0.6;
      p.beam.set(p.at, f.fwd, size, pictures, PAR_HALF_ANGLE_DEG, c, level);
      p.spot.position.copy(v3(p.at));
      p.spot.target.position.copy(v3(p.at).add(v3(f.fwd)));
      p.spot.color.copy(c);
      p.spot.intensity = 25 * level;
    });
    // Derbies: the beamlets on two rings, turning: slowly in the lamps' colours (Calm), fast in their own (Party).
    const spin = (t * (party ? 140 : 30)) % 360;
    for (const dz of this.derbies) {
      const dirs = derbyDirs(f, spin, dz.n);
      dz.beams.forEach((b, k) => b.set(dz.at, dirs[k], size, pictures, 0.8, party ? DERBY_LENSES[(k + dz.n) % 4] : palette[(k + dz.n) % n], party ? 1 : 0.45));
    }
    // Strobes: on the off-beat in Party.
    const strobeOn = party && beat % 2 > 1 && beat % 2 < 1.08;
    this.strobes.forEach((s) => ((s.material as THREE.MeshStandardMaterial).emissiveIntensity = strobeOn ? 6 : 0));
    this.flash.position.copy(v3(f.at).add(v3(f.fwd).multiplyScalar(0.3)));
    this.flash.intensity = strobeOn ? 40 : 0;
    // Laser: red and green, swaying across (Party only).
    this.laser.visible = this.laserDots.visible = party;
    if (party) {
      const lo = emitterAt(f, GIGBAR_EMITTERS.find((e) => e.kind === "laser")!);
      const dirs = laserDirs(f, Math.sin(t * 0.7), (t * 20) % 360);
      const lp = this.laser.geometry.getAttribute("position") as THREE.BufferAttribute;
      const lc = this.laser.geometry.getAttribute("color") as THREE.BufferAttribute;
      const dp = this.laserDots.geometry.getAttribute("position") as THREE.BufferAttribute;
      const dc = this.laserDots.geometry.getAttribute("color") as THREE.BufferAttribute;
      dirs.forEach((d, k) => {
        const h = hitRoom(lo, d, size, pictures);
        const end = h ? h.at : lo;
        const [r, g] = k % 2 ? [0, 1] : [1, 0];
        lp.setXYZ(2 * k, lo[0], lo[1], lo[2]);
        lp.setXYZ(2 * k + 1, end[0], end[1], end[2]);
        lc.setXYZ(2 * k, r * 0.4, g * 0.4, 0);
        lc.setXYZ(2 * k + 1, r * 0.4, g * 0.4, 0);
        dp.setXYZ(k, end[0], end[1], end[2]);
        dc.setXYZ(k, r, g, 0);
      });
      lp.needsUpdate = lc.needsUpdate = dp.needsUpdate = dc.needsUpdate = true;
    }
  }
}

// -- the room -----------------------------------------------------------------------------------------------------------

function buildRoom(size: V3, front: string): THREE.Group {
  const g = new THREE.Group();
  const wall = (tint: string) => new THREE.MeshStandardMaterial({ color: tint, roughness: 0.9, side: THREE.BackSide });
  const frontFace: Record<string, number> = { east: 0, west: 1, north: 2, south: 3 };
  const mats = [0, 1, 2, 3, 4, 5].map((i) => {
    if (i === 5) return wall("#5a4030"); // floor (-z)
    if (i === 4) return wall("#cfc8bb"); // ceiling (+z)
    return wall(i === frontFace[front] ? "#a99f92" : "#d8d0c3");
  });
  const box = new THREE.Mesh(new THREE.BoxGeometry(size[0], size[1], size[2]), mats);
  box.position.set(size[0] / 2, size[1] / 2, size[2] / 2);
  box.receiveShadow = true;
  g.add(box);
  const edges = new THREE.LineSegments(new THREE.EdgesGeometry(box.geometry), new THREE.LineBasicMaterial({ color: "#8a8174" }));
  edges.position.copy(box.position);
  g.add(edges);
  return g;
}

function buildPiano(centre: V3, area: [number, number]): THREE.Group {
  // A grand piano's outline (a Steinway B, 2.11 x 1.48 m), its long side along the area's longer side.
  const g = new THREE.Group();
  const L = 2.11;
  const W = 1.48;
  const s = new THREE.Shape();
  s.moveTo(0, 0);
  s.lineTo(0, W);
  s.lineTo(0.55, W);
  s.bezierCurveTo(1.3, W, 1.25, W * 0.45, L, W * 0.38);
  s.bezierCurveTo(L + 0.05, W * 0.3, L, 0, L * 0.8, 0);
  s.lineTo(0, 0);
  const lacquer = new THREE.MeshStandardMaterial({ color: "#0b0b0c", roughness: 0.15, metalness: 0.3 });
  const body = new THREE.Mesh(new THREE.ExtrudeGeometry(s, { depth: 0.32, bevelEnabled: false }), lacquer);
  body.position.z = 0.62;
  g.add(body);
  const keys = new THREE.Mesh(new THREE.BoxGeometry(0.15, W * 0.86, 0.03), new THREE.MeshStandardMaterial({ color: "#f3efe6" }));
  keys.position.set(-0.07, W / 2, 0.78);
  g.add(keys);
  for (const [x, y] of [[0.2, 0.15], [0.2, W - 0.15], [L * 0.85, W * 0.25]] as [number, number][]) {
    const leg = new THREE.Mesh(new THREE.CylinderGeometry(0.05, 0.04, 0.62, 10), lacquer);
    leg.rotation.x = Math.PI / 2;
    leg.position.set(x, y, 0.31);
    g.add(leg);
  }
  // The lid, propped open on its hinge along the straight side.
  const lid = new THREE.Mesh(new THREE.ShapeGeometry(s), new THREE.MeshStandardMaterial({ color: "#121214", roughness: 0.2, side: THREE.DoubleSide }));
  lid.position.set(0, 0, 0.95);
  lid.rotation.x = 0.75;
  g.add(lid);
  g.position.set(centre[0] - L / 2, centre[1] - W / 2, 0);
  if (area[1] > area[0]) {
    g.rotation.z = Math.PI / 2;
    g.position.set(centre[0] + W / 2, centre[1] - L / 2, 0);
  }
  return g;
}

// -- the view -----------------------------------------------------------------------------------------------------------

export default function Room3D({ api, space, things, lamps }: { api: LightsApi; space: SpaceState; things: Placeable[]; lamps: Record<string, LampColour> }) {
  const mount = useRef<HTMLDivElement | null>(null);
  const live = useLive(api, true);
  const [demo, setDemo] = useState<Demo>("calm");
  const [view, setView] = useState<View>("guest");
  const [selected, setSelected] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, Bar>>({});
  const [saveError, setSaveError] = useState<string | null>(null);
  const [glError, setGlError] = useState<string | null>(null);

  const sp: RoomSpace | null = space.space;
  const size: V3 = (sp?.size as V3 | null) ?? FALLBACK_SIZE;
  const byId = useMemo(() => new Map(things.map((t) => [t.id, t])), [things]);

  const pictures = useMemo((): (Picture & { screen: string | null })[] => {
    if (!sp) return [];
    return Object.entries(sp.appliances).flatMap(([id, a]) => {
      const t = byId.get(id);
      if (!a.at || !a.plane?.normal || !a.plane.size || (t ? t.kind !== "screen" : a.kind !== "screen" && !!a.kind)) return [];
      return [{ id, label: t?.label ?? a.label ?? id, at: a.at as V3, normal: a.plane.normal as V3, up: (a.plane.up ?? [0, 0, 1]) as V3, size: a.plane.size, screen: t?.screen ?? null }];
    });
  }, [sp, byId]);

  const bars = useMemo((): Bar[] => {
    if (!sp) return [];
    return Object.entries(sp.appliances).flatMap(([id, a]) => {
      if (a.kind !== GIGBAR_KIND || !a.at) return [];
      return [drafts[id] ?? { id, label: a.label ?? id, at: a.at as V3, normal: (a.plane?.normal ?? [1, 0, 0]) as V3 }];
    });
  }, [sp, drafts]);

  const palette = useMemo(() => {
    const on = Object.values(lamps).flatMap((l) => (l.hex ? [l.hex] : []));
    return [...new Set(on.length >= 2 ? on : FALLBACK_PALETTE)].map((h) => new THREE.Color(h));
  }, [lamps]);

  const reports = useMemo(() => Object.fromEntries(bars.map((b) => [b.id, reportBar(barFrame(b.at, b.normal), size, pictures)])), [bars, size, pictures]);

  // What the render loop reads (kept in a ref so the loop never restarts for a change).
  const state = useRef({ demo, palette, size, pictures: pictures as Picture[], selected });
  state.current = { demo, palette, size, pictures, selected };

  const world = useRef<{
    scene: THREE.Scene;
    camera: THREE.PerspectiveCamera;
    controls: OrbitControls;
    renderer: THREE.WebGLRenderer;
    statics: THREE.Group;
    bulbs: Map<string, { bulb: THREE.Mesh; light: THREE.PointLight }>;
    screens: Map<string, THREE.Mesh>;
    bars: Map<string, GigbarModel>;
    ball: THREE.Mesh | null;
    beams: THREE.Group;
  } | null>(null);

  // The renderer, camera and loop: once.
  useEffect(() => {
    const el = mount.current;
    if (!el) return;
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true });
    } catch (e) {
      setGlError(e instanceof Error ? e.message : String(e));
      return;
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    el.appendChild(renderer.domElement);
    renderer.domElement.style.touchAction = "none";
    const scene = new THREE.Scene();
    scene.background = new THREE.Color("#0e0c0b");
    scene.add(new THREE.HemisphereLight("#fff4e0", "#3a2a20", 0.35));
    const camera = new THREE.PerspectiveCamera(55, 1, 0.05, 200);
    camera.up.set(0, 0, 1);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    const statics = new THREE.Group();
    const beams = new THREE.Group();
    scene.add(statics, beams);
    world.current = { scene, camera, controls, renderer, statics, bulbs: new Map(), screens: new Map(), bars: new Map(), ball: null, beams };

    const resize = () => {
      const w = el.clientWidth;
      const h = el.clientHeight;
      renderer.setSize(w, h, false);
      renderer.domElement.style.width = "100%";
      renderer.domElement.style.height = "100%";
      camera.aspect = w / Math.max(1, h);
      camera.updateProjectionMatrix();
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(el);

    // Tap a bar to select it (a tap, not the end of a turn of the view).
    let down: [number, number] | null = null;
    const onDown = (e: PointerEvent) => (down = [e.clientX, e.clientY]);
    const onUp = (e: PointerEvent) => {
      if (!down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 6) return;
      const r = renderer.domElement.getBoundingClientRect();
      const ray = new THREE.Raycaster();
      ray.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), camera);
      const bodies = [...(world.current?.bars.values() ?? [])].map((b) => b.body);
      const hit = ray.intersectObjects(bodies, true).find((x) => x.object.userData.bar);
      if (hit) setSelected(hit.object.userData.bar as string);
    };
    renderer.domElement.addEventListener("pointerdown", onDown);
    renderer.domElement.addEventListener("pointerup", onUp);

    let raf = 0;
    const t0 = performance.now();
    const loop = () => {
      raf = requestAnimationFrame(loop);
      if (document.visibilityState !== "visible") return;
      const t = (performance.now() - t0) / 1000;
      const s = state.current;
      const w = world.current;
      if (!w) return;
      for (const b of w.bars.values()) {
        b.update(t, s.demo, s.palette, s.size, s.pictures);
        b.select(b.id === s.selected);
      }
      if (w.ball) w.ball.rotation.z = t * 0.6;
      controls.update();
      renderer.render(scene, camera);
    };
    loop();

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      renderer.domElement.removeEventListener("pointerdown", onDown);
      renderer.domElement.removeEventListener("pointerup", onUp);
      controls.dispose();
      scene.traverse((o) => {
        const m = o as THREE.Mesh;
        if (m.geometry && m.geometry !== BEAM_GEO && m.geometry !== DISC_GEO) m.geometry.dispose();
        const mat = m.material as THREE.Material | THREE.Material[] | undefined;
        (Array.isArray(mat) ? mat : mat ? [mat] : []).forEach((x) => {
          (x as THREE.MeshBasicMaterial).map?.dispose();
          x.dispose();
        });
      });
      renderer.dispose();
      renderer.domElement.remove();
      world.current = null;
    };
  }, []);

  // The room and what's fixed in it: again when the room changes.
  const roomKey = sp ? JSON.stringify([size, sp.front, pictures, sp.regions, sp.points, Object.keys(sp.appliances)]) : "";
  useEffect(() => {
    const w = world.current;
    if (!w || !sp) return;
    w.statics.clear();
    w.bulbs.clear();
    w.screens.clear();
    w.ball = null;
    w.statics.add(buildRoom(size, sp.front));
    for (const p of pictures) {
      const geo = new THREE.PlaneGeometry(p.size[0], p.size[1]);
      const mat = new THREE.MeshBasicMaterial({ color: "#222", toneMapped: false });
      const pic = new THREE.Mesh(geo, mat);
      const f = barFrame(p.at, p.normal, p.up);
      pic.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(v3(f.right), v3(f.up), v3(f.fwd)));
      pic.position.copy(v3(p.at).add(v3(p.normal).multiplyScalar(0.035)));
      const bezel = new THREE.Mesh(new THREE.BoxGeometry(p.size[0] + 0.06, p.size[1] + 0.06, 0.03), new THREE.MeshStandardMaterial({ color: "#2b2622", roughness: 0.6 }));
      bezel.quaternion.copy(pic.quaternion);
      bezel.position.copy(v3(p.at).add(v3(p.normal).multiplyScalar(0.015)));
      w.statics.add(bezel, pic);
      if (p.screen) w.screens.set(p.screen, pic);
    }
    for (const [id, a] of Object.entries(sp.appliances)) {
      const t = byId.get(id);
      if (!a.at || t?.kind !== "lamp") continue;
      const bulb = new THREE.Mesh(new THREE.SphereGeometry(0.11, 20, 14), new THREE.MeshBasicMaterial({ color: "#555", toneMapped: false }));
      bulb.position.copy(v3(a.at as V3));
      const light = new THREE.PointLight("#ffffff", 0, 7, 1.4);
      light.position.copy(bulb.position);
      w.statics.add(bulb, light);
      w.bulbs.set(id, { bulb, light });
    }
    for (const p of sp.points) {
      if (p.name !== "mirror-ball") {
        const pin = new THREE.Mesh(new THREE.SphereGeometry(0.05, 12, 8), new THREE.MeshBasicMaterial({ color: "#14b8a6" }));
        pin.position.copy(v3(p.at as V3));
        w.statics.add(pin);
        continue;
      }
      const ball = new THREE.Mesh(new THREE.IcosahedronGeometry(0.2, 3), new THREE.MeshStandardMaterial({ color: "#e8ebf0", emissive: "#3a3d44", metalness: 0.6, roughness: 0.25, flatShading: true }));
      ball.position.copy(v3(p.at as V3));
      const wire = new THREE.Mesh(new THREE.CylinderGeometry(0.004, 0.004, Math.max(0.01, size[2] - p.at[2] - 0.2), 6), new THREE.MeshBasicMaterial({ color: "#777" }));
      wire.rotation.x = Math.PI / 2;
      wire.position.set(p.at[0], p.at[1], (size[2] + p.at[2] + 0.2) / 2);
      w.statics.add(ball, wire);
      w.ball = ball;
    }
    const piano = sp.regions.find((r) => r.name === "piano");
    if (piano) w.statics.add(buildPiano(piano.centre as V3, piano.size));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [roomKey]);

  // The screens' pictures: now and every minute.
  const screenKey = pictures.map((p) => p.screen).join(",");
  useEffect(() => {
    const loader = new THREE.TextureLoader();
    const load = () => {
      const w = world.current;
      if (!w) return;
      for (const [screen, pic] of w.screens) {
        loader.load(`/output/${encodeURIComponent(screen)}.jpg?t=${Date.now()}`, (tex) => {
          tex.colorSpace = THREE.SRGBColorSpace;
          const mat = pic.material as THREE.MeshBasicMaterial;
          mat.map?.dispose();
          mat.map = tex;
          mat.color.set("#ffffff");
          mat.needsUpdate = true;
        });
      }
    };
    load();
    const timer = setInterval(load, PICTURE_REFRESH_MS);
    return () => clearInterval(timer);
  }, [screenKey, roomKey]);

  // The lamps' colours: a Hue lamp's from room_service (every poll), a DMX lamp's from the live view.
  useEffect(() => {
    const w = world.current;
    if (!w) return;
    for (const [id, { bulb, light }] of w.bulbs) {
      const lf = live?.fixtures[id];
      const l = lamps[id] ?? (lf ? { hex: lf.intensity > 0.01 ? lf.hex : null, dim: lf.intensity } : undefined);
      const on = !!l?.hex;
      (bulb.material as THREE.MeshBasicMaterial).color.set(on ? l!.hex! : "#444");
      light.color.set(on ? l!.hex! : "#000");
      light.intensity = on ? 6 * (l!.dim || 1) : 0;
    }
  }, [lamps, live, roomKey]);

  // The planned GigBARs: made, moved or dropped as the room changes.
  useEffect(() => {
    const w = world.current;
    if (!w) return;
    const keep = new Set(bars.map((b) => b.id));
    for (const [id, m] of w.bars) {
      if (keep.has(id)) continue;
      m.objects.forEach((o) => w.scene.remove(o));
      w.bars.delete(id);
    }
    for (const b of bars) {
      const m = w.bars.get(b.id);
      if (m) m.place(b);
      else {
        const nm = new GigbarModel(b.id, b);
        w.bars.set(b.id, nm);
        nm.objects.forEach((o) => w.scene.add(o));
      }
    }
  }, [bars]);

  // The registered lights' beams (GET /rig/live), drawn from where each light is to where it lands.
  useEffect(() => {
    const w = world.current;
    if (!w) return;
    w.beams.clear();
    for (const f of Object.values(live?.fixtures ?? {})) {
      if (!f.at || !f.hit || f.intensity <= 0.01) continue;
      const g = new THREE.BufferGeometry().setFromPoints([v3(f.at as V3), v3(f.hit as V3)]);
      w.beams.add(new THREE.Line(g, new THREE.LineBasicMaterial({ color: f.hex ?? "#ffffff", transparent: true, opacity: 0.4 + 0.6 * f.intensity })));
    }
  }, [live]);

  // The camera's place for each view.
  useEffect(() => {
    const w = world.current;
    if (!w) return;
    const [W, L, H] = size;
    const at = new THREE.Vector3(W / 2, L / 2, H * 0.4);
    if (view === "guest") {
      w.camera.position.set(W - 0.5, L / 2, 1.6);
      at.set(0, L / 2, 1.7);
    } else if (view === "overview") {
      w.camera.position.set(W + 5, -3, H + 4);
    } else {
      w.camera.position.set(W / 2 + 0.01, L / 2 - 0.01, H + Math.max(W, L) * 1.1);
      at.set(W / 2, L / 2, 0);
    }
    w.controls.target.copy(at);
    w.controls.update();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, size[0], size[1], size[2]]);

  // Moving a bar: the draft shows at once; the room is told after a pause (one save in flight per bar).
  const saveTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const move = (b: Bar, next: Partial<Bar>) => {
    const nb = { ...b, ...next };
    nb.at = [Math.min(size[0], Math.max(0, nb.at[0])), Math.min(size[1], Math.max(0, nb.at[1])), Math.min(size[2] - 0.1, Math.max(0.6, nb.at[2]))];
    setDrafts((d) => ({ ...d, [b.id]: nb }));
    clearTimeout(saveTimers.current[b.id]);
    saveTimers.current[b.id] = setTimeout(() => {
      api
        .place(b.id, { at: nb.at.map((x) => Math.round(x * 100) / 100), mount: "floor", plane: { normal: nb.normal.map((x) => Math.round(x * 10000) / 10000), up: [0, 0, 1], size: [BAR_LENGTH_M, 0.387] } })
        .then(() => {
          setSaveError(null);
          space.reload();
        })
        .catch((e) => setSaveError(explainLightsError(e)));
    }, SAVE_AFTER_MS);
  };
  useEffect(() => () => Object.values(saveTimers.current).forEach(clearTimeout), []);

  const sel = bars.find((b) => b.id === selected) ?? null;
  const selAim = sel ? aimAngles(sel.normal) : null;

  return (
    <div className="space-y-4" data-testid="lights-3d">
      {space.error && !sp && <Notice tone="bad">{space.error}</Notice>}
      {glError && <Notice tone="bad">This device can't draw 3D ({glError}).</Notice>}
      <div className="relative overflow-hidden rounded-2xl border border-stone-200 shadow-sm" style={{ height: "min(62vh, 560px)" }}>
        <div ref={mount} className="absolute inset-0" />
        {!sp && !space.error && (
          <p className="absolute inset-0 flex items-center justify-center gap-2 text-stone-300">
            <Spinner /> Reading the room…
          </p>
        )}
        <div className="pointer-events-none absolute left-3 top-3 rounded-full bg-black/50 px-3 py-1 text-xs text-white/80">Drag to look round · pinch or scroll to zoom</div>
      </div>

      <div className="flex flex-wrap gap-2" role="group" aria-label="View">
        {(
          [
            ["guest", "From the room"],
            ["overview", "Whole room"],
            ["above", "From above"],
          ] as [View, string][]
        ).map(([v, words]) => (
          <button key={v} type="button" onClick={() => setView(v)} className={cn(CHIP, view === v ? CHIP_ON : CHIP_OFF)}>
            {words}
          </button>
        ))}
      </div>

      {bars.length > 0 && (
        <section className={cn(CARD_CLASS, "space-y-3 px-4 py-3")} aria-label="GigBARs">
          <div>
            <h2 className="text-lg font-semibold text-stone-800">GigBARs</h2>
            <p className="text-[15px] text-muted-foreground">Planned, not plugged in yet: the preview shows what they could do, in the lamps' colours.</p>
          </div>
          <div className="flex flex-wrap gap-2" role="group" aria-label="Preview">
            {(
              [
                ["off", "Off"],
                ["calm", "Calm"],
                ["party", "Party"],
              ] as [Demo, string][]
            ).map(([d, words]) => (
              <button key={d} type="button" onClick={() => setDemo(d)} className={cn(CHIP, demo === d ? CHIP_ON : CHIP_OFF)}>
                {words}
              </button>
            ))}
          </div>
          <div className="flex flex-wrap gap-2" role="group" aria-label="Move a GigBAR">
            {bars.map((b) => (
              <button key={b.id} type="button" onClick={() => setSelected(selected === b.id ? null : b.id)} className={cn(CHIP, selected === b.id ? CHIP_ON : CHIP_OFF)}>
                {b.label}
              </button>
            ))}
          </div>
          {sel && selAim && (
            <div className="divide-y divide-stone-100">
              <Stepper label="From the west wall" value={sel.at[0]} step={0.1} unit="m" onStep={(d) => move(sel, { at: [sel.at[0] + d, sel.at[1], sel.at[2]] })} />
              <Stepper label="From the south wall" value={sel.at[1]} step={0.1} unit="m" onStep={(d) => move(sel, { at: [sel.at[0], sel.at[1] + d, sel.at[2]] })} />
              <Stepper label="Bar height" value={sel.at[2]} step={0.1} unit="m" onStep={(d) => move(sel, { at: [sel.at[0], sel.at[1], sel.at[2] + d] })} />
              <Stepper label="Turn (0° faces east)" value={selAim.turn} step={5} unit="deg" onStep={(d) => move(sel, { normal: aimFrom(selAim.turn + d, selAim.tilt) })} />
              <Stepper label="Tilt (down is −)" value={selAim.tilt} step={5} unit="deg" onStep={(d) => move(sel, { normal: aimFrom(selAim.turn, Math.max(-80, Math.min(60, selAim.tilt + d))) })} />
            </div>
          )}
          {saveError && <Notice tone="warn">{saveError}</Notice>}
          <ul className="space-y-1 text-[15px] text-stone-700">
            {bars.map((b) => {
              const r = reports[b.id];
              if (!r) return null;
              return (
                <li key={b.id}>
                  <span className="font-medium">{b.label}:</span> pars land mostly on {surfaceWords(r.parsLand, pictures)}; {pct(r.parsOnScreens)} of
                  their light and {pct(r.derbyOnScreens)} of the derby dots fall on the screens; {pct(r.laserAtHeads)} of the laser beams pass through
                  head height{r.laserAtHeads > 0 ? " (tilt it up to keep them overhead)" : ""}.
                </li>
              );
            })}
          </ul>
        </section>
      )}
    </div>
  );
}
