// The room simulator as an editor, with the handles room and lighting editors use (owner, 27 Nov: "why are there so
// many positioning arrows?"; "RESEARCH AND ADOPT BEST PRACTICE"). Each kind of thing has only the handles its own
// settings need, and the thing itself is a handle:
//   - a bar:      drag it to slide it across the floor; drag the ring round it to turn it; drag the arrow above it to
//                 raise or lower it;
//   - a light:    drag the spot where its beam lands, over the walls, floor and ceiling: it aims there (as lighting
//                 design tools aim a fixture at a focus point);
//   - a keep-out box: drag it to slide it across the room; drag the arrow above it to raise or lower it (its size is
//                 typed in the panel).
// Hovering anything that can be dragged outlines it. While a drag goes on the thing's outline follows; letting go
// hands the change to the page (onChange), which saves it and draws the room again. Everything is drawn over the
// finished picture, in the room's own frame (metres: x east, y north, z up).

import * as THREE from "three";
import type { Vec3 } from "./model";

export type EditKind = "bar" | "light" | "region";
export interface Editable {
  key: string;
  kind: EditKind;
  label?: string;           // shown by the cursor when it is over the thing
  at: Vec3;                 // where it is
  size: Vec3;               // the box that is picked and outlined (before a bar's turn)
  yaw?: number;             // a bar: its facing, radians anticlockwise from east
  dir?: Vec3;               // a light: where it points (unit)
  front?: Vec3;             // a light: its bar's front (level unit): tilt and turn are read back against it
}
export interface EditChange { at?: Vec3; facing_deg?: number; lean_deg?: number; turn_deg?: number; size?: Vec3 }
type Handle = "move" | "ring" | "lift" | "aim";

const CYAN = 0x35e0ff;
const RING_R = 0.75, LIFT_H = 0.55;
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
  private items: Editable[] = [];
  private ray = new THREE.Raycaster();
  private room: Vec3 = [6, 6, 3];
  private drag: { handle: Handle; p0: THREE.Vector3; start: Editable; ref?: number } | null = null;
  private live: Editable | null = null;           // the selected thing as the drag has it now
  selected: string | null = null;
  dragging = false;
  /** A drag has just ended: the click that ends it is not a pick. */
  justDragged = false;
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
  }

  get active(): boolean { return this.items.length > 0; }
  /** The room's size (m), for where a light's beam lands and how far a thing can go. */
  setRoom(size: Vec3) { this.room = size; }
  /** No snapping: a drag is free, and exact values are typed in the panel. */
  setSnap(_on: boolean) { /* nothing */ }

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
    this.group.updateMatrixWorld(true);
    if (!this.dragging) this.select(items.some((x) => x.key === this.selected) ? this.selected : null);
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
    if (it.kind === "bar" || it.kind === "region") {
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
      // its beam's line, and the spot where it lands: drag the spot to aim it
      const spot = this.landing(it.at, it.dir);
      const beam = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(...it.at), spot]),
        new THREE.LineDashedMaterial({ color: CYAN, dashSize: 0.08, gapSize: 0.05, depthTest: false, transparent: true }));
      beam.computeLineDistances();
      const ring = new THREE.Mesh(new THREE.TorusGeometry(0.13, 0.012, 8, 40), mat(CYAN, 0.9));
      ring.position.copy(spot);
      ring.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), new THREE.Vector3(...it.at).sub(spot).normalize());
      const dot = new THREE.Mesh(new THREE.SphereGeometry(0.06, 20, 14), mat(CYAN));
      dot.position.copy(spot);
      const fat = new THREE.Mesh(new THREE.SphereGeometry(0.18, 8, 6), unseen());
      fat.position.copy(spot);
      this.ui.add(beam, ring, dot);
      handle(fat, "aim");
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

  /** The thing under a point of the view (a light before the bar it is on); null: nothing. */
  pick(x: number, y: number): string | null {
    const hs = this.hits(x, y, [...this.picks.values()]);
    if (!hs.length) return null;
    const kindOf = (k: string) => this.items.find((i) => i.key === k)?.kind;
    const light = hs.find((h) => kindOf(h.object.userData.key) === "light");
    return (light ?? hs[0]).object.userData.key as string;
  }

  /** What a press there would grab: one of the selected thing's handles, or the selected thing itself (to move it). */
  private grabbable(x: number, y: number): Handle | null {
    const h = this.hits(x, y, this.handles)[0];
    if (h) return h.object.userData.handle as Handle;
    const it = this.live;
    if (it && (it.kind === "bar" || it.kind === "region")) {
      const body = this.picks.get(it.key);
      if (body && this.hits(x, y, [body]).length) return "move";
    }
    return null;
  }

  /** The thing under the cursor gets a light outline (not the selected one); returns it, for its name. */
  hover(x: number, y: number): Editable | null {
    if (this.dragging) return null;
    const key = this.grabbable(x, y) ? this.selected : this.pick(x, y);
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
    if (this.grabbable(x, y)) return "grab";
    return this.pick(x, y) ? "pointer" : null;
  }

  /** A press: starts a drag when it is on the selected thing or one of its handles (true: the page keeps the camera
   *  out of it). */
  grab(x: number, y: number): boolean {
    const h = this.grabbable(x, y);
    const it = this.live;
    if (!h || !it) return false;
    const r = this.roomRay(x, y);
    const p0 = new THREE.Vector3(...it.at);
    let ref: number | undefined;
    if (h === "move" || h === "ring") {
      r.intersectPlane(new THREE.Plane(new THREE.Vector3(0, 0, 1), -it.at[2]), p0);
      if (h === "ring") ref = Math.atan2(p0.y - it.at[1], p0.x - it.at[0]);
    } else if (h === "lift") {
      const p = this.liftPoint(r, it.at);
      if (p) ref = p.z;
    }
    this.drag = { handle: h, p0, start: { ...it }, ref };
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
      const p = floorAt(s.at[2]);
      if (!p) return;
      it.at = [Math.max(0, Math.min(this.room[0], s.at[0] + p.x - d.p0.x)),
               Math.max(0, Math.min(this.room[1], s.at[1] + p.y - d.p0.y)), s.at[2]];
    } else if (d.handle === "lift") {
      const p = this.liftPoint(r, s.at);
      if (!p || d.ref === undefined) return;
      it.at = [s.at[0], s.at[1], Math.max(0.1, Math.min(this.room[2] - 0.05, s.at[2] + p.z - d.ref))];
    } else if (d.handle === "ring") {
      const p = floorAt(s.at[2]);
      if (!p) return;
      it.yaw = (s.yaw ?? 0) + Math.atan2(p.y - s.at[1], p.x - s.at[0]) - (d.ref ?? 0);
    } else {
      // aim: the spot follows the cursor over the room's walls, floor and ceiling; the light points at it
      let best = Infinity;
      const hit = new THREE.Vector3();
      for (let i = 0; i < 3; i++) {
        const di = r.direction.getComponent(i);
        if (Math.abs(di) < 1e-9) continue;
        for (const wall of [0, this.room[i]]) {
          const t = (wall - r.origin.getComponent(i)) / di;
          if (t <= 1e-3 || t >= best) continue;
          const q = r.origin.clone().addScaledVector(r.direction, t);
          if ([0, 1, 2].every((k) => k === i || (q.getComponent(k) >= -1e-3 && q.getComponent(k) <= this.room[k] + 1e-3))) {
            best = t;
            hit.copy(q);
          }
        }
      }
      if (!Number.isFinite(best)) return;
      const dir = hit.sub(new THREE.Vector3(...s.at)).normalize();
      it.dir = [dir.x, dir.y, dir.z];
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
    if (!d || !it) return;
    const s = d.start;
    if (d.handle === "move" || d.handle === "lift") {
      if (it.at.every((v, i) => Math.abs(v - s.at[i]) < 1e-3)) return;
      this.onChange(it.key, it.kind === "region" ? { at: it.at.map(r2) as Vec3, size: it.size } : { at: it.at.map(r2) as Vec3 });
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
