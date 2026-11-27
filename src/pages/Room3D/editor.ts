// The room simulator as an editor (owner, 27 Nov: "why can't I click a light unit and adjust these various aspects of
// it -- isn't that the obvious affordance? reference any normal 3d editing studio ux"). What every 3D studio does:
//   - click a thing in the view to select it (it is outlined); click empty space, or Esc, to let go;
//   - a gizmo on the selected thing: arrows to move it (W), rings to rotate it (E), handles to scale it (R);
//   - while it is dragged a ghost of it follows the gizmo; letting go commits the change (the page saves it, the room
//     is read again and the real thing is drawn there);
//   - the numbers for exact entry live in the inspector beside the view (the page's, not this file's).
// The things are the page's: bars (move, and turn about the vertical), fixed lights on a bar (rotate: where they
// point), keep-out regions (move, scale). Everything here is drawn over the finished picture (RoomSim's overlay), in
// the room's own frame (metres: x east, y north, z up), so it never passes through the exposure.

import * as THREE from "three";
import { TransformControls } from "three/examples/jsm/controls/TransformControls.js";
import type { Vec3 } from "./model";

export type EditKind = "bar" | "light" | "region";
export type GizmoMode = "translate" | "rotate" | "scale";
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

const MODES: Record<EditKind, GizmoMode[]> = { bar: ["translate", "rotate"], light: ["rotate"], region: ["translate", "scale"] };
/** The gizmo modes a kind of thing takes, the first its default. */
export const modesOf = (kind: EditKind): GizmoMode[] => MODES[kind];

const UP = new THREE.Vector3(0, 0, 1);
const r2 = (x: number) => Math.round(x * 100) / 100;

export class RoomEditor {
  private group = new THREE.Group();
  private picks = new Map<string, THREE.Mesh>();
  private proxy = new THREE.Object3D();           // what the gizmo moves: the selected thing's stand-in
  private ghost = new THREE.Group();              // its outline (and a light's arrow), carried by the proxy
  private hoverBox: THREE.LineSegments;           // the outline of the thing under the cursor
  hovered: string | null = null;
  private tc: TransformControls;
  private helper: THREE.Object3D;
  private items: Editable[] = [];
  private ray = new THREE.Raycaster();
  selected: string | null = null;
  dragging = false;
  /** A drag has just ended: the click that ends it is not a pick. */
  justDragged = false;
  onChange: (key: string, change: EditChange) => void = () => {};
  /** A handle is being dragged (true) or let go (false): the page holds the camera still meanwhile. */
  onDragging: (on: boolean) => void = () => {};

  constructor(private root: THREE.Group, private scene: THREE.Scene, private camera: THREE.PerspectiveCamera, dom: HTMLElement) {
    root.add(this.group);
    this.group.add(this.proxy);
    this.proxy.add(this.ghost);
    this.proxy.visible = false;
    this.hoverBox = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(1, 1, 1)),
      new THREE.LineBasicMaterial({ color: 0xffffff, depthTest: false, transparent: true, opacity: 0.9 }));
    this.hoverBox.visible = false;
    this.group.add(this.hoverBox);
    this.tc = new TransformControls(camera, dom);
    this.tc.setSize(0.85);
    this.helper = this.tc.getHelper();
    scene.add(this.helper);
    this.tc.addEventListener("dragging-changed", (e) => {
      this.dragging = !!(e as unknown as { value: boolean }).value;
      this.onDragging(this.dragging);
      if (!this.dragging) { this.justDragged = true; this.commit(); }
    });
  }

  /** Hold Ctrl (or ⌘) while dragging a handle: moves snap to 5 cm, turns to 5°, sizes to 5 cm (as 3D studios do). */
  setSnap(on: boolean) {
    this.tc.setTranslationSnap(on ? 0.05 : null);
    this.tc.setRotationSnap(on ? THREE.MathUtils.degToRad(5) : null);
    this.tc.setScaleSnap(on ? 0.05 : null);
  }

  /** The thing under a point of the view gets a light outline (the selected one keeps its own); null: none. */
  hover(x: number, y: number): Editable | null {
    const key = this.dragging ? null : this.pick(x, y);
    const it = key && key !== this.selected ? this.items.find((i) => i.key === key) ?? null : null;
    this.hovered = key;
    if (!it) { this.hoverBox.visible = false; return key ? this.items.find((i) => i.key === key) ?? null : null; }
    const pad = 1.08;
    this.hoverBox.visible = true;
    this.hoverBox.position.set(it.at[0], it.at[1], it.at[2]);
    this.hoverBox.rotation.set(0, 0, it.yaw ?? 0);
    this.hoverBox.scale.set(it.size[0] * pad, it.size[1] * pad, it.size[2] * pad);
    return it;
  }
  clearHover() { this.hovered = null; this.hoverBox.visible = false; }

  /** The pointer is on one of the gizmo's handles (so a press there is the gizmo's, not the camera's). */
  get onHandle(): boolean { return this.tc.axis !== null; }
  get active(): boolean { return this.items.length > 0; }

  /** The things that can be picked. The selected one is put back where the room now has it, unless it is being dragged. */
  setItems(items: Editable[]) {
    this.items = items;
    for (const m of this.picks.values()) { this.group.remove(m); m.geometry.dispose(); }
    this.picks.clear();
    for (const it of items) {
      const m = new THREE.Mesh(new THREE.BoxGeometry(it.size[0], it.size[1], it.size[2]), new THREE.MeshBasicMaterial({ visible: false }));
      m.position.set(it.at[0], it.at[1], it.at[2]);
      if (it.yaw !== undefined) m.rotation.z = it.yaw;
      m.userData.key = it.key;
      this.group.add(m);
      this.picks.set(it.key, m);
    }
    this.group.updateMatrixWorld(true);
    if (this.selected && !this.dragging) this.select(items.some((x) => x.key === this.selected) ? this.selected : null);
  }

  /** Select a thing (null: nothing): its outline and the gizmo go on it. */
  select(key: string | null, mode?: GizmoMode) {
    const it = key ? this.items.find((x) => x.key === key) : undefined;
    this.selected = it ? it.key : null;
    for (const c of [...this.ghost.children]) this.ghost.remove(c);
    if (!it) { this.tc.detach(); this.proxy.visible = false; return; }
    const p = this.proxy;
    p.visible = true;
    p.position.set(it.at[0], it.at[1], it.at[2]);
    p.quaternion.identity();
    p.scale.set(1, 1, 1);
    const cyan = new THREE.LineBasicMaterial({ color: 0x35e0ff, depthTest: false, transparent: true });
    if (it.kind === "region") {
      p.scale.set(it.size[0], it.size[1], it.size[2]);                       // a unit box, scaled: the gizmo scales it
      this.ghost.add(new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(1, 1, 1)), cyan));
    } else {
      this.ghost.add(new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(it.size[0], it.size[1], it.size[2])), cyan));
      if (it.kind === "bar") {
        p.rotation.set(0, 0, it.yaw ?? 0);
        this.ghost.add(new THREE.ArrowHelper(new THREE.Vector3(1, 0, 0), new THREE.Vector3(), 0.7, 0x35e0ff, 0.16, 0.08));   // its front
      } else if (it.dir) {                                                   // a light: its local +y is where it points
        const y = new THREE.Vector3(it.dir[0], it.dir[1], it.dir[2]).normalize();
        let x = new THREE.Vector3().crossVectors(y, UP);
        if (x.lengthSq() < 1e-6) x = it.front ? new THREE.Vector3(-it.front[1], it.front[0], 0) : new THREE.Vector3(1, 0, 0);
        x.normalize();
        p.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(x, y, new THREE.Vector3().crossVectors(x, y)));
        this.ghost.add(new THREE.ArrowHelper(new THREE.Vector3(0, 1, 0), new THREE.Vector3(), 0.9, 0x35e0ff, 0.18, 0.09));
      }
    }
    p.updateMatrixWorld(true);
    this.tc.attach(p);
    this.setMode(mode && MODES[it.kind].includes(mode) ? mode : MODES[it.kind][0]);
  }

  setMode(mode: GizmoMode) {
    const it = this.items.find((x) => x.key === this.selected);
    if (!it || !MODES[it.kind].includes(mode)) return;
    this.tc.setMode(mode);
    this.tc.setSpace(mode === "scale" ? "local" : "world");
    const yawOnly = it.kind === "bar" && mode === "rotate";                  // a bar turns about the vertical only
    this.tc.showX = !yawOnly;                                                // (the vertical is three's y)
    this.tc.showZ = !yawOnly;
    this.tc.showY = true;
  }

  get mode(): GizmoMode { return this.tc.mode as GizmoMode; }

  /** The thing under a point of the view (x, y: -1..1), a light before the bar it is on; null: nothing. */
  pick(x: number, y: number): string | null {
    this.ray.setFromCamera(new THREE.Vector2(x, y), this.camera);
    const hits = this.ray.intersectObjects([...this.picks.values()], false);
    if (!hits.length) return null;
    const kindOf = (k: string) => this.items.find((i) => i.key === k)?.kind;
    const light = hits.find((h) => kindOf(h.object.userData.key) === "light");
    return (light ?? hits[0]).object.userData.key as string;
  }

  /** The gizmo was let go: what the thing is now. */
  private commit() {
    const it = this.items.find((x) => x.key === this.selected);
    if (!it) return;
    const p = this.proxy;
    if (it.kind === "bar") {
      const fwd = new THREE.Vector3(1, 0, 0).applyQuaternion(p.quaternion);  // its front, in the room
      this.onChange(it.key, { at: [r2(p.position.x), r2(p.position.y), r2(p.position.z)],
        facing_deg: Math.round(THREE.MathUtils.radToDeg(Math.atan2(fwd.y, fwd.x)) * 10) / 10 });
    } else if (it.kind === "region") {
      this.onChange(it.key, { at: [r2(p.position.x), r2(p.position.y), r2(p.position.z)],
        size: [r2(Math.max(0.1, Math.abs(p.scale.x))), r2(Math.max(0.1, Math.abs(p.scale.y))), r2(Math.max(0.1, Math.abs(p.scale.z)))] });
    } else {
      const d = new THREE.Vector3(0, 1, 0).applyQuaternion(p.quaternion).normalize();
      const f = it.front ?? [1, 0, 0];
      let lean = THREE.MathUtils.radToDeg(Math.acos(Math.max(-1, Math.min(1, d.z))));
      let turn = 0;
      if (Math.hypot(d.x, d.y) > 1e-4) turn = THREE.MathUtils.radToDeg(Math.atan2(d.x * -f[1] + d.y * f[0], d.x * f[0] + d.y * f[1]));
      if (Math.abs(turn) > 90) { lean = -lean; turn -= Math.sign(turn) * 180; }   // leaning back, not turned round
      this.onChange(it.key, { lean_deg: Math.round(lean * 2) / 2, turn_deg: Math.round(turn * 2) / 2 });
    }
  }

  dispose() {
    this.tc.detach();
    this.scene.remove(this.helper);
    this.tc.dispose();
    this.root.remove(this.group);
  }
}
