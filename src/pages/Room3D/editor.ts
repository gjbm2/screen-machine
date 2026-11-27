// The room simulator as an editor, with the handles room and lighting editors use (owner, 27 Nov: "why are there so
// many positioning arrows?"; "RESEARCH AND ADOPT BEST PRACTICE"). Each kind of thing has only the handles its own
// settings need, and the thing itself is a handle:
//   - a bar:      drag it to slide it across the floor; drag the ring round it to turn it; drag the arrow above it to
//                 raise or lower it;
//   - a light:    two handles, each setting one angle only (owner, 27 Nov: "there's no way unambiguously to express
//                 what direction I want it to point unless you expose angle and elevation as separate controls"): a
//                 flat ring round it turns its direction (its elevation stays), an upright arc on the side it points
//                 changes its elevation (its direction stays). Its beam is drawn to where it lands, as a guide only;
//   - a keep-out box, a lamp, a named place: drag it to slide it across the room; drag the arrow above it to raise
//                 or lower it (sizes are typed in the panel); a screen slides along the walls, onto the nearest one;
//                 a floor area slides across the floor. Places and areas have no body in the room: they are drawn
//                 as a pin and a patch of floor.
//   - one unit: a light on a bar can only be aimed (it moves with its bar); selecting either outlines the other.
// In the plan view (from above, as the old Room map was) a press on anything that moves picks it up at once.
// Hovering anything that can be dragged outlines it. While a drag goes on the thing's outline follows; letting go
// hands the change to the page (onChange), which saves it and draws the room again. Everything is drawn over the
// finished picture, in the room's own frame (metres: x east, y north, z up).

import * as THREE from "three";
import type { Vec3 } from "./model";

export type EditKind = "bar" | "light" | "part" | "region" | "screen" | "thing" | "place" | "area";
/** Kinds that slide across the room (a screen along the walls) and are raised by their arrow. */
const MOVES: EditKind[] = ["bar", "region", "screen", "thing", "place", "area"];
const LIFTS: EditKind[] = ["bar", "region", "screen", "thing", "place"];
export interface Editable {
  key: string;
  kind: EditKind;
  label?: string;           // shown by the cursor when it is over the thing
  at: Vec3;                 // where it is
  size: Vec3;               // the box that is picked and outlined (before a bar's turn)
  yaw?: number;             // a bar: its facing, radians anticlockwise from east
  dir?: Vec3;               // a light: where it points (unit)
  front?: Vec3;             // a light: its bar's front (level unit): tilt and turn are read back against it
  parent?: string;          // a light on a bar: the bar's key (they are one unit: outlined together)
  normal?: Vec3;            // a screen: which way it faces out of its wall
  tiltOnly?: boolean;       // a light in a one-axis bracket (a U-yoke): it only tilts, facing where its bar faces
}
export interface EditChange { at?: Vec3; facing_deg?: number; lean_deg?: number; turn_deg?: number; size?: Vec3; normal?: Vec3 }
type Handle = "move" | "ring" | "lift" | "turn" | "tilt";

const CYAN = 0x35e0ff, AMBER = 0xffb020;
const RING_R = 0.75, LIFT_H = 0.55;
const TURN_R = 0.3, TILT_R = 0.42;          // a light's direction ring and elevation arc (m)
const D = THREE.MathUtils.radToDeg;
/** A direction in the room as a heading (radians anticlockwise from east, seen from above) and an elevation
 *  (radians above level). */
function angles(d: Vec3, front?: Vec3): { h: number; e: number } {
  const flat = Math.hypot(d[0], d[1]) < 1e-6;                 // straight up or down: it faces its bar's front
  return { h: flat && front ? Math.atan2(front[1], front[0]) : Math.atan2(d[1], d[0]), e: Math.asin(Math.max(-1, Math.min(1, d[2]))) };
}
function fromAngles(h: number, e: number): Vec3 {
  return [Math.cos(e) * Math.cos(h), Math.cos(e) * Math.sin(h), Math.sin(e)];
}
/** A light's aim as the panel says it: degrees right of its bar's front looking out (- left), degrees above level. */
export function aimWords(dir: Vec3, front: Vec3 = [1, 0, 0]): { direction: number; elevation: number } {
  const { h, e } = angles(dir, front);
  let rel = D(h - Math.atan2(front[1], front[0]));
  rel = ((rel + 180) % 360 + 360) % 360 - 180;
  return { direction: Math.round(-rel * 2) / 2 || 0, elevation: Math.round(D(e) * 2) / 2 || 0 };
}
const r2 = (x: number) => Math.round(x * 100) / 100;

function mat(color: number, opacity = 1) {
  return new THREE.MeshBasicMaterial({ color, transparent: true, opacity, depthTest: false, depthWrite: false });
}
const unseen = () => new THREE.MeshBasicMaterial({ visible: false });

export class RoomEditor {
  private group = new THREE.Group();
  private picks = new Map<string, THREE.Mesh>();  // each thing's (invisible) body: a click selects, a drag moves
  private ui = new THREE.Group();                 // the selected thing's outline and handles
  private handles: THREE.Object3D[] = [];         // what a press can grab (their userData.handle)
  private hoverBox: THREE.LineSegments;
  private marks = new THREE.Group();              // places and areas, which have no body in the room: always drawn
  /** Plan view: a press on any thing that moves picks it up at once (as on a map); elsewhere it is clicked first. */
  grabAny = false;
  /** An elevation: which way it looks (room frame). A drag moves a thing in the plane across it: along the wall and
   *  up or down, not to and fro. null: across the floor. */
  dragPlane: THREE.Vector3 | null = null;
  /** A press picked up a thing that was not selected: the page selects it. */
  onPick: (key: string) => void = () => {};
  private items: Editable[] = [];
  private ray = new THREE.Raycaster();
  private room: Vec3 = [6, 6, 3];
  private drag: { handle: Handle; p0: THREE.Vector3; start: Editable; ref?: number; dup?: boolean } | null = null;
  private live: Editable | null = null;           // the selected thing as the drag has it now
  selected: string | null = null;
  dragging = false;
  /** A drag has just ended: the click that ends it is not a pick. */
  justDragged = false;
  /** This press picked the thing up (the plan): its click has been had, even if it didn't move. */
  private pickedUp = false;
  onChange: (key: string, change: EditChange) => void = () => {};
  /** A drag starts (true) or ends (false): the page holds the camera still meanwhile. */
  onDragging: (on: boolean) => void = () => {};

  constructor(private root: THREE.Group, _scene: THREE.Scene, private camera: THREE.PerspectiveCamera, _dom: HTMLElement) {
    root.add(this.group);
    this.group.add(this.ui);
    this.hoverBox = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(1, 1, 1)),
      new THREE.LineBasicMaterial({ color: 0xffffff, depthTest: false, transparent: true, opacity: 0.9 }));
    this.hoverBox.visible = false;
    this.group.add(this.hoverBox);
    this.group.add(this.marks);
  }

  get active(): boolean { return this.items.length > 0; }
  /** The room's size (m), for where a light's beam lands and how far a thing can go. */
  setRoom(size: Vec3) { this.room = size; }
  /** Ctrl (or ⌘) held: a drag of a thing makes a copy where it is let go (owner, 27 Nov: "CTRL+DRAG to duplicate"). */
  private ctrl = false;
  setSnap(on: boolean) { this.ctrl = on; }
  /** A Ctrl+drag let go: the page makes the copy (the original stays where it was). */
  onDuplicate: (key: string, at: Vec3) => void = () => {};

  /** The things that can be picked. The selected one is drawn again where the room now has it (not mid-drag). */
  setItems(items: Editable[]) {
    this.items = items;
    for (const m of this.picks.values()) { this.group.remove(m); m.geometry.dispose(); }
    this.picks.clear();
    for (const it of items) {
      const m = new THREE.Mesh(new THREE.BoxGeometry(it.size[0], it.size[1], it.size[2]), unseen());
      m.position.set(it.at[0], it.at[1], it.at[2]);
      if (it.yaw !== undefined) m.rotation.z = it.yaw;
      m.userData.key = it.key;
      this.group.add(m);
      this.picks.set(it.key, m);
    }
    this.drawMarks();
    this.group.updateMatrixWorld(true);
    if (!this.dragging) this.select(items.some((x) => x.key === this.selected) ? this.selected : null);
  }

  /** A place: a pin from the floor up to it. An area: its patch of floor. */
  private drawMarks() {
    for (const c of [...this.marks.children]) { this.marks.remove(c); c.traverse((o) => (o as THREE.Mesh).geometry?.dispose()); }
    for (const it of this.items) {
      const at = this.drag && this.live && this.live.key === it.key ? this.live.at : it.at;
      if (it.kind === "place") {
        const pin = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(at[0], at[1], 0), new THREE.Vector3(...at)]),
          new THREE.LineBasicMaterial({ color: 0xff5d8f, depthTest: false, transparent: true, opacity: 0.8 }));
        const head = new THREE.Mesh(new THREE.SphereGeometry(0.09, 16, 12), mat(0xff5d8f, 0.9));
        head.position.set(at[0], at[1], Math.max(0.09, at[2]));
        const foot = new THREE.Mesh(new THREE.RingGeometry(0.12, 0.16, 32), mat(0xff5d8f, 0.8));
        foot.position.set(at[0], at[1], 0.01);
        this.marks.add(pin, head, foot);
      } else if (it.kind === "area") {
        const patch = new THREE.Mesh(new THREE.PlaneGeometry(it.size[0], it.size[1]), mat(0x7c5cff, 0.18));
        patch.position.set(at[0], at[1], 0.01);
        const edge = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.PlaneGeometry(it.size[0], it.size[1])),
          new THREE.LineBasicMaterial({ color: 0x7c5cff, depthTest: false, transparent: true }));
        edge.position.copy(patch.position);
        this.marks.add(patch, edge);
      }
    }
  }

  /** Select a thing (null: nothing): its outline and its handles go on it. */
  select(key: string | null) {
    const it = key ? this.items.find((x) => x.key === key) : undefined;
    this.selected = it ? it.key : null;
    this.live = it ? { ...it } : null;
    this.build();
  }

  // -- drawing the selected thing ---------------------------------------------------------------------------------
  private build() {
    for (const c of [...this.ui.children]) {
      this.ui.remove(c);
      c.traverse((o) => { (o as THREE.Mesh).geometry?.dispose(); });
    }
    this.handles = [];
    const it = this.live;
    if (!it) return;
    const line = new THREE.LineBasicMaterial({ color: CYAN, depthTest: false, transparent: true });
    const outline = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(it.size[0], it.size[1], it.size[2])), line);
    outline.position.set(it.at[0], it.at[1], it.at[2]);
    if (it.yaw !== undefined) outline.rotation.z = it.yaw;
    this.ui.add(outline);
    const handle = (o: THREE.Object3D, h: Handle) => { o.userData.handle = h; this.handles.push(o); this.ui.add(o); };
    // one unit: a light's bar, a bar's lights, outlined fainter with it
    const kin = this.items.filter((x) => x.key !== it.key && (x.key === it.parent || x.parent === it.key));
    for (const k of kin) {
      const at = it.kind === "bar" && this.drag ? [k.at[0] + it.at[0] - (this.drag.start.at[0]), k.at[1] + it.at[1] - this.drag.start.at[1], k.at[2] + it.at[2] - this.drag.start.at[2]] : k.at;
      const o = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(k.size[0], k.size[1], k.size[2])),
        new THREE.LineDashedMaterial({ color: CYAN, dashSize: 0.04, gapSize: 0.03, depthTest: false, transparent: true, opacity: 0.7 }));
      o.computeLineDistances();
      o.position.set(at[0], at[1], at[2]);
      if (k.yaw !== undefined) o.rotation.z = k.yaw;
      this.ui.add(o);
    }
    if (LIFTS.includes(it.kind)) {
      // one arrow above it: raise or lower it
      const top = it.at[2] + it.size[2] / 2;
      const shaft = new THREE.Mesh(new THREE.CylinderGeometry(0.012, 0.012, LIFT_H, 8), mat(CYAN));
      shaft.rotation.x = Math.PI / 2; shaft.position.set(it.at[0], it.at[1], top + LIFT_H / 2);
      const tip = new THREE.Mesh(new THREE.ConeGeometry(0.05, 0.12, 16), mat(CYAN));
      tip.rotation.x = Math.PI / 2; tip.position.set(it.at[0], it.at[1], top + LIFT_H + 0.06);
      const grab = new THREE.Mesh(new THREE.CylinderGeometry(0.08, 0.08, LIFT_H + 0.15, 8), unseen());
      grab.rotation.x = Math.PI / 2; grab.position.set(it.at[0], it.at[1], top + (LIFT_H + 0.15) / 2);
      this.ui.add(shaft, tip);
      handle(grab, "lift");
    }
    if (it.kind === "bar") {
      // one ring round it, flat at its height, a knob at its front: turn it
      const ring = new THREE.Mesh(new THREE.TorusGeometry(RING_R, 0.01, 8, 96), mat(CYAN, 0.9));
      ring.position.set(it.at[0], it.at[1], it.at[2]);
      const fat = new THREE.Mesh(new THREE.TorusGeometry(RING_R, 0.07, 6, 48), unseen());
      fat.position.copy(ring.position);
      const yaw = it.yaw ?? 0;
      const knob = new THREE.Mesh(new THREE.SphereGeometry(0.045, 16, 12), mat(CYAN));
      knob.position.set(it.at[0] + Math.cos(yaw) * RING_R, it.at[1] + Math.sin(yaw) * RING_R, it.at[2]);
      this.ui.add(ring, knob);
      handle(fat, "ring");
    }
    if (it.kind === "light" && it.dir) {
      const P = new THREE.Vector3(...it.at);
      const { h, e } = angles(it.dir, it.front);
      const hv = new THREE.Vector3(Math.cos(h), Math.sin(h), 0);
      const fr = it.front ?? [Math.cos(h), Math.sin(h), 0];
      const fv = new THREE.Vector3(fr[0], fr[1], 0).normalize();        // its bar's front (a U-bracket's plane)
      const up = new THREE.Vector3(0, 0, 1);
      // its beam, to where it lands: a guide, not a handle
      const spot = this.landing(it.at, it.dir);
      const beam = new THREE.Line(new THREE.BufferGeometry().setFromPoints([P, spot]),
        new THREE.LineDashedMaterial({ color: 0xffffff, dashSize: 0.08, gapSize: 0.05, depthTest: false, transparent: true, opacity: 0.8 }));
      beam.computeLineDistances();
      const dot = new THREE.Mesh(new THREE.SphereGeometry(0.04, 16, 12), mat(0xffffff, 0.9));
      dot.position.copy(spot);
      this.ui.add(beam, dot);
      // direction: a flat ring round it, its knob on the side it points
      const ring = new THREE.Mesh(new THREE.TorusGeometry(TURN_R, 0.009, 8, 96), mat(CYAN, 0.95));
      ring.position.copy(P);
      const fatRing = new THREE.Mesh(new THREE.TorusGeometry(TURN_R, 0.05, 6, 48), unseen());
      fatRing.position.copy(P);
      const tick = new THREE.Line(new THREE.BufferGeometry().setFromPoints([P, P.clone().addScaledVector(hv, TURN_R)]),
        new THREE.LineBasicMaterial({ color: CYAN, depthTest: false, transparent: true }));
      const knobT = new THREE.Mesh(new THREE.SphereGeometry(0.035, 16, 12), mat(CYAN));
      knobT.position.copy(P).addScaledVector(hv, TURN_R);
      if (!it.tiltOnly) { this.ui.add(ring, tick, knobT); handle(fatRing, "turn"); }
      // elevation: an upright half circle from straight down to straight up on that side, its knob where it points
      // a U-bracket's light: its whole swing, from level backwards over the top to straight down, in the plane of its
      // bar's front (it only tilts); else the half circle on the side it points
      const arcPts: THREE.Vector3[] = [];
      const [a0, a1] = it.tiltOnly ? [-Math.PI / 2, Math.PI] : [-Math.PI / 2, Math.PI / 2];   // tiltOnly: lean from up
      for (let i = 0; i <= 64; i++) {
        const a = a0 + ((a1 - a0) * i) / 64;
        arcPts.push(it.tiltOnly
          ? P.clone().addScaledVector(fv, TILT_R * Math.sin(a)).addScaledVector(up, TILT_R * Math.cos(a))
          : P.clone().addScaledVector(hv, TILT_R * Math.cos(a)).addScaledVector(up, TILT_R * Math.sin(a)));
      }
      const curve = new THREE.CatmullRomCurve3(arcPts);
      const arc = new THREE.Mesh(new THREE.TubeGeometry(curve, 48, 0.009, 6, false), mat(AMBER, 0.95));
      const fatArc = new THREE.Mesh(new THREE.TubeGeometry(curve, 24, 0.05, 6, false), unseen());
      const level = new THREE.Line(new THREE.BufferGeometry().setFromPoints([P, P.clone().addScaledVector(it.tiltOnly ? fv : hv, TILT_R)]),
        new THREE.LineDashedMaterial({ color: AMBER, dashSize: 0.03, gapSize: 0.03, depthTest: false, transparent: true, opacity: 0.6 }));
      level.computeLineDistances();
      const knobE = new THREE.Mesh(new THREE.SphereGeometry(0.035, 16, 12), mat(AMBER));
      knobE.position.copy(P).add(new THREE.Vector3(...it.dir).multiplyScalar(TILT_R));
      this.ui.add(arc, level, knobE);
      handle(fatArc, "tilt");
    }
    this.ui.updateMatrixWorld(true);
  }

  /** Where a beam from ``at`` along ``dir`` lands on the room's box (room frame). */
  private landing(at: Vec3, dir: Vec3): THREE.Vector3 {
    let best = 20;
    for (let i = 0; i < 3; i++) {
      if (Math.abs(dir[i]) < 1e-9) continue;
      for (const wall of [0, this.room[i]]) {
        const t = (wall - at[i]) / dir[i];
        if (t > 1e-3 && t < best) best = t;
      }
    }
    return new THREE.Vector3(at[0] + dir[0] * best, at[1] + dir[1] * best, at[2] + dir[2] * best);
  }

  // -- the pointer ----------------------------------------------------------------------------------------------------
  /** The view's ray through a point of it (x, y: -1..1), in the room's frame. */
  private roomRay(x: number, y: number): THREE.Ray {
    this.ray.setFromCamera(new THREE.Vector2(x, y), this.camera);
    const inv = new THREE.Matrix4().copy(this.root.matrixWorld).invert();
    const r = this.ray.ray.clone().applyMatrix4(inv);
    r.direction.normalize();
    return r;
  }
  private hits(x: number, y: number, objs: THREE.Object3D[]) {
    this.ray.setFromCamera(new THREE.Vector2(x, y), this.camera);
    return this.ray.intersectObjects(objs, false);
  }

  /** The thing a click there selects (null: nothing). A bar and its lights are one unit, picked as editors pick a
   *  group (owner, 27 Nov: "hard to select a whole gigbar -- individual components win on mouse click too easily"):
   *  from outside the unit a click on any of it selects the bar; once the bar or one of its lights is selected, a
   *  click on a light selects that light (drilling in). ``deep``: the light at once (a double-click). */
  pick(x: number, y: number, deep = false): string | null {
    const key = this.pickPart(x, y);
    if (!key || deep) return key;
    const it = this.items.find((i) => i.key === key);
    const parent = it?.parent && this.items.some((i) => i.key === it.parent) ? it.parent : null;
    if (!parent) return key;
    const sel = this.selected ? this.items.find((i) => i.key === this.selected) : undefined;
    const inside = this.selected === parent || sel?.parent === parent;
    if (!inside) return parent;
    // clicked again where it was clicked last, on a stack of parts (heads under pars under a laser, from above):
    // the next one down the stack (owner, 27 Nov: "some are on top of each other, so I have no way to select them")
    const stack = this.partsAt(x, y, parent);
    const last = this.lastPick;
    if (last && Math.abs(last.x - x) < 0.02 && Math.abs(last.y - y) < 0.02 && stack.length > 1 && this.selected && stack.includes(this.selected)) {
      const next = stack[(stack.indexOf(this.selected) + 1) % stack.length];
      this.lastPick = { x, y, n: stack.length, i: stack.indexOf(next) };
      return next;
    }
    this.lastPick = { x, y, n: stack.length, i: Math.max(0, stack.indexOf(key)) };
    return key;
  }
  /** The last click inside a unit: where, and which of how many stacked parts it gave (for "2 of 3 here"). */
  lastPick: { x: number; y: number; n: number; i: number } | null = null;
  /** A unit's parts under a point of the view, nearest first. */
  private partsAt(x: number, y: number, parent: string): string[] {
    const hs = this.hits(x, y, [...this.picks.values()]);
    const out: string[] = [];
    for (const h of hs) {
      const k = h.object.userData.key as string, it = this.items.find((i) => i.key === k);
      if (it?.parent === parent && !out.includes(k)) out.push(k);
    }
    return out;
  }
  /** The nearest thing under a point of the view, a light before the bar it is on. */
  private pickPart(x: number, y: number): string | null {
    const hs = this.hits(x, y, [...this.picks.values()]);
    if (!hs.length) return null;
    const item = (k: string) => this.items.find((i) => i.key === k);
    // a part of a unit (a light, a head, a strobe LED) before the bar it is on: the nearest such part
    const part = hs.find((h) => !!item(h.object.userData.key)?.parent || item(h.object.userData.key)?.kind === "light");
    return (part ?? hs[0]).object.userData.key as string;
  }
  /** The selected thing's unit: up one level (a light to its bar), else null. */
  parentOf(key: string | null): string | null {
    const it = key ? this.items.find((i) => i.key === key) : undefined;
    return it?.parent && this.items.some((i) => i.key === it.parent) ? it.parent : null;
  }

  /** What a press there would grab: one of the selected thing's handles, or the selected thing itself (to move it). */
  private grabbable(x: number, y: number): Handle | null {
    const h = this.hits(x, y, this.handles)[0];
    if (h) return h.object.userData.handle as Handle;
    const it = this.live;
    if (it && MOVES.includes(it.kind)) {
      const body = this.picks.get(it.key);
      if (body && this.hits(x, y, [body]).length) return "move";
    }
    return null;
  }

  /** The thing under the cursor gets a light outline (not the selected one); returns it, for its name. */
  hover(x: number, y: number): Editable | null {
    if (this.dragging) return null;
    const p = this.pick(x, y);                        // inside a selected bar: its part under the cursor
    const key = p && p !== this.selected ? p : this.grabbable(x, y) ? this.selected : p;
    const it = key ? this.items.find((i) => i.key === key) ?? null : null;
    if (!it || key === this.selected) { this.hoverBox.visible = false; return it; }
    this.hoverBox.visible = true;
    this.hoverBox.position.set(it.at[0], it.at[1], it.at[2]);
    this.hoverBox.rotation.set(0, 0, it.yaw ?? 0);
    this.hoverBox.scale.set(it.size[0] * 1.08, it.size[1] * 1.08, it.size[2] * 1.08);
    return it;
  }
  clearHover() { this.hoverBox.visible = false; }
  /** The cursor a point of the view should show: over a handle or the selected thing "grab", over another thing
   *  "pointer", else null (the view's own). */
  cursorAt(x: number, y: number): "grab" | "pointer" | null {
    const p = this.pick(x, y);
    if (p && p !== this.selected) return "pointer";      // a click selects it (a selected bar's part: drill in)
    if (this.grabbable(x, y)) return "grab";
    return p ? "pointer" : null;
  }

  /** A press: starts a drag when it is on the selected thing or one of its handles (true: the page keeps the camera
   *  out of it). */
  grab(x: number, y: number): boolean {
    let h = this.grabbable(x, y);
    if (!h && this.grabAny) {                         // plan view: pick it up straight away
      const key = this.pick(x, y);
      const k = key ? this.items.find((i) => i.key === key) : undefined;
      if (k && MOVES.includes(k.kind)) { this.select(k.key); this.onPick(k.key); h = "move"; this.pickedUp = true; }
    }
    const it = this.live;
    if (!h || !it) return false;
    const r = this.roomRay(x, y);
    const p0 = new THREE.Vector3(...it.at);
    let ref: number | undefined;
    if (h === "move" && this.dragPlane) {
      r.intersectPlane(new THREE.Plane().setFromNormalAndCoplanarPoint(this.dragPlane, new THREE.Vector3(...it.at)), p0);
    } else if (h === "move" || h === "ring") {
      r.intersectPlane(new THREE.Plane(new THREE.Vector3(0, 0, 1), -it.at[2]), p0);
      if (h === "ring") ref = Math.atan2(p0.y - it.at[1], p0.x - it.at[0]);
    } else if (h === "lift") {
      const p = this.liftPoint(r, it.at);
      if (p) ref = p.z;
    } else if (h === "turn" && it.dir) {
      const p = new THREE.Vector3();
      if (r.intersectPlane(new THREE.Plane(new THREE.Vector3(0, 0, 1), -it.at[2]), p)) ref = Math.atan2(p.y - it.at[1], p.x - it.at[0]);
    } else if (h === "tilt" && it.dir) {
      const a = this.tiltAngle(r, it.at, this.tiltHeading(it));
      if (a !== null) ref = a;
    }
    this.drag = { handle: h, p0, start: { ...it }, ref, dup: this.ctrl && h === "move" };
    this.dragging = true;
    this.hoverBox.visible = false;
    this.onDragging(true);
    return true;
  }

  /** A vertical plane through ``at`` facing the camera: where the cursor's ray meets it (for raising a thing). */
  private liftPoint(r: THREE.Ray, at: Vec3): THREE.Vector3 | null {
    const n = new THREE.Vector3(r.direction.x, r.direction.y, 0);
    if (n.lengthSq() < 1e-6) return null;
    const p = new THREE.Vector3();
    return r.intersectPlane(new THREE.Plane().setFromNormalAndCoplanarPoint(n.normalize(), new THREE.Vector3(...at)), p) ? p : null;
  }

  /** The heading a light's tilt arc stands on: a U-bracket's, its bar's front; else the way it points. */
  private tiltHeading(it: Editable): number {
    if (it.tiltOnly && it.front) return Math.atan2(it.front[1], it.front[0]);
    return angles(it.dir ?? [1, 0, 0], it.front).h;
  }
  /** The angle above level of where the cursor's ray meets the upright plane through ``at`` along heading ``h``. */
  private tiltAngle(r: THREE.Ray, at: Vec3, h: number): number | null {
    const n = new THREE.Vector3(-Math.sin(h), Math.cos(h), 0);
    const P = new THREE.Vector3(...at), q = new THREE.Vector3();
    if (Math.abs(n.dot(r.direction)) < 0.03) return null;           // the arc seen edge on: no sure reading
    if (!r.intersectPlane(new THREE.Plane().setFromNormalAndCoplanarPoint(n, P), q)) return null;
    q.sub(P);
    return Math.atan2(q.z, q.x * Math.cos(h) + q.y * Math.sin(h));
  }

  /** While a light's handle is dragged: its direction and elevation in words, for the page to show by the cursor. */
  readout(): string | null {
    const d = this.drag, it = this.live;
    if (!d || !it || !it.dir || (d.handle !== "turn" && d.handle !== "tilt")) return null;
    if (it.tiltOnly && it.front) {                       // a U-bracket: its tilt from straight up, as on the bracket
      const f = it.front, l = Math.round(D(Math.atan2(it.dir[0] * f[0] + it.dir[1] * f[1], it.dir[2])) * 2) / 2;
      return `Tilt ${Math.abs(l) < 0.25 ? "straight up" : l < 0 ? `${-l}° back (away from the room)` : `${l}° forward (towards the room)`}`;
    }
    const w = aimWords(it.dir, it.front);
    const dirTxt = Math.abs(w.direction) < 0.25 ? "straight ahead" : `${Math.abs(w.direction)}° ${w.direction > 0 ? "right" : "left"}`;
    const elTxt = Math.abs(w.elevation - 90) < 0.25 ? "straight up" : Math.abs(w.elevation) < 0.25 ? "level" : `${Math.abs(w.elevation)}° ${w.elevation > 0 ? "above" : "below"} level`;
    return d.handle === "turn" ? `Direction ${dirTxt}` : `Elevation ${elTxt}`;
  }

  /** The pointer moves while dragging: the thing's outline and handles follow. */
  dragTo(x: number, y: number) {
    const d = this.drag, it = this.live;
    if (!d || !it) return;
    const r = this.roomRay(x, y), s = d.start;
    const floorAt = (z: number) => {
      const p = new THREE.Vector3();
      return r.intersectPlane(new THREE.Plane(new THREE.Vector3(0, 0, 1), -z), p) ? p : null;
    };
    if (d.handle === "move") {
      let p: THREE.Vector3 | null;
      if (this.dragPlane) {                               // an elevation: across the wall and up or down
        p = new THREE.Vector3();
        if (!r.intersectPlane(new THREE.Plane().setFromNormalAndCoplanarPoint(this.dragPlane, new THREE.Vector3(...s.at)), p)) p = null;
      } else p = floorAt(s.at[2]);
      if (!p) return;
      const z = this.dragPlane && LIFTS.includes(it.kind) ? Math.max(it.kind === "place" ? 0 : 0.1, Math.min(this.room[2] - 0.05, s.at[2] + p.z - d.p0.z)) : s.at[2];
      it.at = [Math.max(0, Math.min(this.room[0], s.at[0] + p.x - d.p0.x)),
               Math.max(0, Math.min(this.room[1], s.at[1] + p.y - d.p0.y)), z];
      if (it.kind === "screen") {                     // a screen hangs on the nearest wall, facing into the room
        const [X, Y] = this.room, [x0, y0] = it.at;
        const walls: [number, Vec3, Vec3][] = [[x0, [0, y0, z], [1, 0, 0]], [X - x0, [X, y0, z], [-1, 0, 0]],
          [y0, [x0, 0, z], [0, 1, 0]], [Y - y0, [x0, Y, z], [0, -1, 0]]];
        const w = walls.reduce((a, b) => (b[0] < a[0] ? b : a));
        it.at = w[1]; it.normal = w[2]; it.yaw = Math.atan2(w[2][1], w[2][0]);
      }
      if (it.kind === "area" || it.kind === "place") this.drawMarks();
    } else if (d.handle === "lift") {
      const p = this.liftPoint(r, s.at);
      if (!p || d.ref === undefined) return;
      it.at = [s.at[0], s.at[1], Math.max(it.kind === "place" ? 0 : 0.1, Math.min(this.room[2] - 0.05, s.at[2] + p.z - d.ref))];
      if (it.kind === "place") this.drawMarks();
    } else if (d.handle === "ring") {
      const p = floorAt(s.at[2]);
      if (!p) return;
      it.yaw = (s.yaw ?? 0) + Math.atan2(p.y - s.at[1], p.x - s.at[0]) - (d.ref ?? 0);
    } else if (d.handle === "turn" && s.dir) {
      // direction only: the cursor's angle round the light on its own level, from where the drag began
      const p = floorAt(s.at[2]);
      if (!p || d.ref === undefined) return;
      const { h, e } = angles(s.dir, s.front);
      it.dir = fromAngles(h + Math.atan2(p.y - s.at[1], p.x - s.at[0]) - d.ref, e);
    } else if (d.handle === "tilt" && s.dir && s.tiltOnly) {
      // a U-bracket: the tilt about its bar's length, from up (0) forward (+) or back (-), as far as straight down
      const h = this.tiltHeading(s);
      const a = this.tiltAngle(r, s.at, h);
      if (a === null || d.ref === undefined) return;
      const fv = new THREE.Vector3(Math.cos(h), Math.sin(h), 0);
      const lean0 = Math.atan2(s.dir[0] * fv.x + s.dir[1] * fv.y, s.dir[2]);
      const lean = Math.max(-Math.PI / 2, Math.min(Math.PI, lean0 - (a - d.ref)));
      it.dir = [fv.x * Math.sin(lean), fv.y * Math.sin(lean), Math.cos(lean)];
    } else if (d.handle === "tilt" && s.dir) {
      // elevation only: the cursor's angle on the upright arc, from straight down to straight up
      const { h, e } = angles(s.dir, s.front);
      const a = this.tiltAngle(r, s.at, h);
      if (a === null || d.ref === undefined) return;
      const lim = THREE.MathUtils.degToRad(89.5);              // never quite vertical: the direction would be lost
      it.dir = fromAngles(h, Math.max(-lim, Math.min(lim, e + a - d.ref)));
    }
    this.build();
  }

  /** The pointer is let go: the change goes to the page. */
  release() {
    const d = this.drag, it = this.live;
    this.drag = null;
    if (!this.dragging) return;
    this.dragging = false;
    this.justDragged = true;
    this.onDragging(false);
    const picked = this.pickedUp;
    this.pickedUp = false;
    if (!d || !it) return;
    const s = d.start;
    // a press on the selected thing or a handle that changed nothing is a click (on a light of a selected bar: drill in)
    const same = it.at.every((v, i) => Math.abs(v - s.at[i]) < 1e-3) && Math.abs((it.yaw ?? 0) - (s.yaw ?? 0)) < 1e-3
      && (!it.dir || !s.dir || it.dir.every((v, i) => Math.abs(v - s.dir![i]) < 1e-6));
    if (same) { if (!picked) this.justDragged = false; return; }
    if (d.dup) {                                      // a copy where it was let go; the original goes back
      const at = it.at.map(r2) as Vec3;
      this.live = { ...s };
      this.build();
      this.onDuplicate(it.key, at);
      return;
    }
    if (d.handle === "move" || d.handle === "lift") {
      this.onChange(it.key, it.kind === "region" ? { at: it.at.map(r2) as Vec3, size: it.size }
        : it.kind === "screen" ? { at: it.at.map(r2) as Vec3, normal: it.normal } : { at: it.at.map(r2) as Vec3 });
    } else if (d.handle === "ring") {
      if (Math.abs((it.yaw ?? 0) - (s.yaw ?? 0)) < 1e-3) return;
      const deg = THREE.MathUtils.radToDeg(it.yaw ?? 0);
      this.onChange(it.key, { facing_deg: Math.round((((deg + 180) % 360 + 360) % 360 - 180) * 10) / 10 });
    } else if (it.dir) {
      const dd = it.dir, f = it.front ?? [1, 0, 0];
      let lean = THREE.MathUtils.radToDeg(Math.acos(Math.max(-1, Math.min(1, dd[2]))));
      let turn = 0;
      if (Math.hypot(dd[0], dd[1]) > 1e-4) turn = THREE.MathUtils.radToDeg(Math.atan2(dd[0] * -f[1] + dd[1] * f[0], dd[0] * f[0] + dd[1] * f[1]));
      if (Math.abs(turn) > 90) { lean = -lean; turn -= Math.sign(turn) * 180; }   // leaning back, not turned round
      this.onChange(it.key, { lean_deg: Math.round(lean * 2) / 2, turn_deg: Math.round(turn * 2) / 2 });
    }
  }

  dispose() {
    this.root.remove(this.group);
  }
}

/** There are no gizmo modes any more (each thing has its own handles); kept so callers compile. */
export type GizmoMode = "translate" | "rotate" | "scale";
export const modesOf = (_kind: EditKind): GizmoMode[] => [];
