// /lights/room: the room in 3D, as configured and as it runs (owner, 26 Nov: "a proper 3D virtualisation of the
// actual config for a room, with the live feeds operating properly so we can simulate, run evals, etc.").
//
//   * The room: show_service's room model and rig (model.ts), re-read every ROOM_POLL_MS so edits to the deployment's
//     lights/ files show here.
//   * The lights: DMX from the virtual Art-Net node (tools/virtual_node: a real node that show_service sends the
//     same packets as the real one), never show_service's intent. ?node=<url> (default http://127.0.0.1:8705, this
//     Windows machine's node).
//   * The screens: each one's own live stream through room_service's viewer role, else its art (screens.ts).
//   * Hue lamps: show_service's last colour for each (no wire to read).

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import * as THREE from "three";
import { resolveRoomUrl } from "@/pages/Room/api";
import { RoomEditor, type Editable, type EditChange, type GizmoMode } from "./editor";
import { loadRoom, type KeepRegion, type RoomModel } from "./model";
import { hazeWords, LEVEL_VISIBILITY_M, mlOf, sigmaOf, sigmaOfSlider, sliderOf } from "./haze";
import { RoomSim, type Elev, type Nav } from "./scene";

const ROOM_POLL_MS = 5000;
/** A part's name without its bar's ("Bar A par 1" on Bar A: "Par 1"). */
const shortName = (label: string, bar?: string) => {
  const n = bar && label.startsWith(bar + " ") ? label.slice(bar.length + 1) : label;
  return n.charAt(0).toUpperCase() + n.slice(1);
};
const NODE_DEFAULT = "http://127.0.0.1:8705";
const SIDE_W = 340;

type Haze = { levels?: Record<string, number>; pick?: string | null; feel?: string | null; want?: string | null; phase?: string; estimate_ml_m3?: number; target_ml_m3?: number; output?: number; fan?: number };

type NodeStatus = { node?: { udp?: string; universe?: number }; artnet?: { pps?: number; age_s?: number | null; merging?: boolean; length?: number } };

/** The room in 3D. On its own (/lights/room) it fills the window; ``embedded`` (the Lights setup's 3D tab) it fills
 *  its box and leaves the readout to the page around it. ``picked``: the keep-out region being edited there;
 *  ``regions``: the page's own (unsaved) copy of the regions, drawn in place of the room's saved ones. */
export default function Room3DPage({ embedded = false, picked = null, regions, refresh = 0, editables, selected = null,
  mode, onSelect, onEdit, onDuplicate, panel, frame = 0, feed, toolbar, startNav, liveAim = null, editShow = "on",
  pointing = false, onPointAt, pointWords, pointThings = true }: {
  embedded?: boolean; picked?: string | null; regions?: KeepRegion[];
  /** Bump it to read the room again at once (the page around it has just saved a change). */
  refresh?: number;
  /** The editor (editor.ts): the things that can be clicked and moved, which one is selected, the gizmo's mode, and
   *  what the page does when one is picked or changed. ``panel``: the page's inspector, shown beside the view. */
  editables?: Editable[]; selected?: string | null; mode?: GizmoMode;
  /** "Point at…": the next click in the view picks a place (a thing's middle, or the spot on a wall, the floor, the
   *  ceiling) and hands it to onPointAt (room metres). */
  pointing?: boolean;
  onPointAt?: (at: [number, number, number]) => void;
  /** False: the spot clicked, never a thing's middle (putting a new thing down). */
  pointThings?: boolean;
  /** The words over the view while it waits for that click (default: pointing a light). */
  pointWords?: string;
  /** How the selected light is drawn: on (bright), off, or as the scene has it. */
  editShow?: "on" | "off" | "show";
  /** A light being aimed from the panel (a slider moving): drawn that way at once. */
  liveAim?: { id: string; dir: [number, number, number] } | null;
  /** Ctrl+drag let go: copy that thing to there. */
  onDuplicate?: (key: string, at: [number, number, number]) => void;
  onSelect?: (key: string | null) => void; onEdit?: (key: string, change: EditChange) => void; panel?: ReactNode;
  /** Bump it to turn the camera to the selected thing ("frame selected": picked from the list, or F). */
  frame?: number;
  /** Where the lights' DMX comes from, in place of the virtual node: show_service's preview (``base``/dmx``query``
   *  and ``base``/status), or the node when ``live``. */
  feed?: { base: string; query?: string; live?: boolean };
  /** The page's own controls, put in the header (embedded). */
  toolbar?: ReactNode;
  /** Open in this way of moving (the plan, for "Show it on the map"), not the one remembered. */
  startNav?: Nav;
} = {}) {
  const edRef = useRef<RoomEditor | null>(null);
  const namesRef = useRef<HTMLDivElement | null>(null);          // the plan's names on things, placed each frame
  const itemsRef = useRef<Editable[]>([]);
  itemsRef.current = editables ?? [];
  const selRef = useRef<string | null>(null);
  selRef.current = selected;
  const cb = useRef({ onSelect, onEdit, onDuplicate, onPointAt, pointing, pointThings });
  const fromView = useRef(false);                                 // the last selection was made in the view
  cb.current = { onSelect, onEdit, onDuplicate, onPointAt, pointing, pointThings };
  const press = useRef<{ x: number; y: number } | null>(null);
  const room = useMemo(() => resolveRoomUrl(window.location.search), []);
  const nodeParam = useMemo(() => (new URLSearchParams(window.location.search).get("node") || NODE_DEFAULT).replace(/\/+$/, ""), []);
  const node = feed && !feed.live ? feed.base.replace(/\/+$/, "") : nodeParam;
  const dmxQuery = feed && !feed.live ? feed.query ?? "" : "";
  // Taking pictures (tools/room/light_shots.py: the library's picture of each lighting scene): ?shot=1 draws the room
  // alone, at full size whatever the frame rate, from ?view=front|side|above, in ?haze=light|medium|thick (a fixed
  // haze, not the room's own reading).
  const shot = useMemo(() => {
    const q = new URLSearchParams(window.location.search);
    if (!q.has("shot")) return null;
    const v = q.get("view"), hz = q.get("haze") || "";
    return { view: (v === "side" || v === "above" ? v : "front") as "front" | "side" | "above",
      sigma: hz in LEVEL_VISIBILITY_M ? 3 / LEVEL_VISIBILITY_M[hz] : hz === "clear" ? 0 : null };
  }, []);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const hostRef = useRef<HTMLDivElement | null>(null);
  const simRef = useRef<RoomSim | null>(null);
  const [model, setModel] = useState<RoomModel | null>(null);
  const [roomErr, setRoomErr] = useState<string | null>(null);
  const [nodeSt, setNodeSt] = useState<NodeStatus | null>(null);
  const [dmxLive, setDmxLive] = useState(false);
  const [words, setWords] = useState<{ unit: string; rows: [string, string][] }[]>([]);
  const [perf, setPerf] = useState("");
  const [fps, setFps] = useState(0);
  const [showPaths, setShowPaths] = useState(false);
  const [opts, setOpts] = useState(false);              // the editor header's "more" menu
  const storedNav = (): Nav => { try { const v = localStorage.getItem("room3d.nav"); return v === "orbit" || v === "plan" || v === "elev" ? v : "walk"; } catch { return "walk"; } };
  const [elev, setElev] = useState<Elev>(() => { try { const v = localStorage.getItem("room3d.elev"); return v === "back" || v === "left" || v === "right" ? v : "front"; } catch { return "front"; } });
  const last3d = useRef<"walk" | "orbit">((() => { try { return localStorage.getItem("room3d.move") === "orbit" ? "orbit" : "walk"; } catch { return "walk"; } })());
  /** A view from the header or a key: 3D (in the last way of moving), the plan, or an elevation (Front / Side keep
   *  which of the pair they last looked from). */
  const showView = (v: "3d" | "plan" | "front" | "side") => {
    const sim = simRef.current;
    if (!sim) return;
    if (v === "3d") { chooseNav(last3d.current); return; }
    if (v === "plan") { sim.showFlat("plan"); remember("plan", elev); return; }
    const pair: Elev[] = v === "front" ? ["front", "back"] : ["left", "right"];
    const e = pair.includes(elev) ? elev : pair[0];
    sim.showFlat(e); remember("elev", e);
  };
  const flipElev = () => {
    const sim = simRef.current;
    if (!sim) return;
    const e: Elev = ({ front: "back", back: "front", left: "right", right: "left" } as const)[sim.elev];
    sim.showFlat(e); remember("elev", e);
  };
  const remember = (n: Nav, e: Elev) => { try { localStorage.setItem("room3d.nav", n); localStorage.setItem("room3d.elev", e); } catch { /* this visit */ } };
  const [nav, setNavState] = useState<Nav>(() => startNav ?? storedNav());
  /** Walk, orbit or the plan, from the header (remembered); the view also switches by itself to orbit a thing being
   *  edited while walking. */
  const chooseNav = (m: Nav) => {
    if (m === "walk" || m === "orbit") { last3d.current = m; try { localStorage.setItem("room3d.move", m); } catch { /* this visit */ } }
    const sim = simRef.current;
    sim?.setNav(m, false);
    // a click on Walk or Orbit starts afresh, even when it is already on (owner, 27 Nov: "clicking walk, orbit afresh
    // should reset each view to sensible starting point"): walking at the back of the room, orbiting the whole room
    if (m === "walk") sim?.standAt("back"); else if (m === "orbit") sim?.view("side");
    try { localStorage.setItem("room3d.nav", m); } catch { /* this visit only */ }
  };
  const [inside, setInside] = useState(() => { try { return localStorage.getItem("room3d.inside") === "1"; } catch { return false; } });
  useEffect(() => {
    if (simRef.current) simRef.current.keepInside = inside;
    try { localStorage.setItem("room3d.inside", inside ? "1" : "0"); } catch { /* a blocked store: this visit only */ }
  }, [inside]);
  const [haze, setHaze] = useState<Haze | null>(null);
  const [hazeDrag, setHazeDrag] = useState<number | null>(null);   // the bar's position while it's dragged (0-1)
  const hazeDragRef = useRef<number | null>(null);
  const hazeBar = useRef<HTMLSpanElement | null>(null);

  // the scene
  useEffect(() => {
    const canvas = canvasRef.current, host = hostRef.current;
    if (!canvas || !host) return;
    const sim = new RoomSim({ canvas, room });
    simRef.current = sim;
    try { sim.keepInside = localStorage.getItem("room3d.inside") === "1"; } catch { /* default: free */ }
    const n0 = startNav ?? storedNav();
    try { const e = localStorage.getItem("room3d.elev"); if (e === "back" || e === "left" || e === "right") sim.elev = e; } catch { /* front */ }
    if (n0 !== "walk") sim.setNav(n0);
    const onNav = (m: Nav) => {
      setNavState(m); setElev(sim.elev);
      const ed = edRef.current;
      if (ed) { ed.grabAny = m === "plan" || m === "elev"; ed.dragPlane = m === "elev" ? sim.flatLookRoom() : null; }
    };
    sim.onNav = onNav;
    const ed = new RoomEditor(sim.overlayRoot, sim.overlayScene, sim.camera, canvas);
    ed.onChange = (key, change) => cb.current.onEdit?.(key, change);
    ed.onDuplicate = (key, at) => cb.current.onDuplicate?.(key, at);
    ed.onAimLive = (key, dir) => sim.liveAim(key.slice(key.indexOf(":") + 1), dir);   // a light turns as it is dragged
    ed.onPick = (key) => { fromView.current = true; cb.current.onSelect?.(key); };
    onNav(sim.nav);
    ed.onDragging = (on) => sim.hold(on);                         // a handle being dragged holds the camera still
    edRef.current = ed;
    const fit = () => sim.resize(host.clientWidth, host.clientHeight);
    const ro = new ResizeObserver(fit);
    ro.observe(host);
    fit();
    let raf = 0, frames = 0, fpsAt = performance.now();
    const gl = sim.renderer.getContext();
    const ext = gl.getExtension("WEBGL_debug_renderer_info");
    const gpu = String(ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER));
    const v = new THREE.Vector3();
    const placeNames = () => {
      const el = namesRef.current;
      if (!el) return;
      // the plan: every thing's name; any view: the names of the selected bar's parts (what a click there selects)
      const all = itemsRef.current, sel = selRef.current;
      const unit = sel ? (all.find((x) => x.key === sel)?.parent ?? sel) : null;
      const parts = unit ? all.filter((x) => x.parent === unit && x.label) : [];
      const plan = sim.nav === "plan" || sim.nav === "elev";
      const items = [...(plan ? all.filter((x) => x.label && !x.parent && x.kind !== "light") : []), ...parts];
      el.style.display = items.length ? "" : "none";
      if (!items.length) return;
      const have = new Map(Array.from(el.children).filter((c) => (c as HTMLElement).dataset.key).map((c) => [(c as HTMLElement).dataset.key!, c as HTMLElement]));
      const W = host.clientWidth, Hh = host.clientHeight;
      const scr = (p: readonly number[]) => { v.set(p[0], p[2], -p[1]).project(sim.camera); return [((v.x + 1) / 2) * W, ((1 - v.y) / 2) * Hh] as const; };
      // a selected bar's parts: where they would overlap on screen (the plan looks down on a bar whose heads and pars
      // hang under it and whose derbies and laser stand on it), their names go in a row beside the bar instead, in
      // order along it (then from the top down), each with a line to its part (owner, 27 Nov: "some are on top of
      // each other, so I have no way to select them")
      const chips = new Map<string, [number, number]>();
      const pts = parts.map((it) => ({ it, p: scr(it.at) }));
      const crowded = pts.some((a, i) => pts.some((b, j) => j > i && Math.abs(a.p[0] - b.p[0]) < 46 && Math.abs(a.p[1] - b.p[1]) < 16));
      if (crowded && unit) {
        const u = all.find((x) => x.key === unit);
        const yaw = u?.yaw ?? 0, ax = [-Math.sin(yaw), Math.cos(yaw)];          // along the bar (its length)
        const order = [...pts].sort((a, b) => {
          const da = (a.it.at[0] - (u?.at[0] ?? 0)) * ax[0] + (a.it.at[1] - (u?.at[1] ?? 0)) * ax[1];
          const db = (b.it.at[0] - (u?.at[0] ?? 0)) * ax[0] + (b.it.at[1] - (u?.at[1] ?? 0)) * ax[1];
          return Math.abs(da - db) > 0.03 ? da - db : b.it.at[2] - a.it.at[2];
        });
        // left to right on screen as along the bar, whichever way it is seen
        const p0 = scr([(u?.at[0] ?? 0) - ax[0], (u?.at[1] ?? 0) - ax[1], u?.at[2] ?? 0]), p1 = scr([(u?.at[0] ?? 0) + ax[0], (u?.at[1] ?? 0) + ax[1], u?.at[2] ?? 0]);
        if (p1[0] < p0[0]) order.reverse();
        const cx = order.reduce((a, o) => a + o.p[0], 0) / order.length, low = Math.max(...order.map((o) => o.p[1]));
        const gap = 72, per = Math.max(1, Math.floor((W - 60) / gap));
        order.forEach((o, i) => {
          const row = Math.floor(i / per), inRow = Math.min(per, order.length - row * per), k = i % per;
          const span = (inRow - 1) * gap;                      // the whole row shifts to fit the view, never squashed
          const x0 = Math.max(40, Math.min(W - 40 - span, cx - span / 2));
          const y = Math.min(Hh - 20 - row * 24, low + 46) + row * 24;
          chips.set(o.it.key, [x0 + k * gap, y]);
        });
      }
      // their leader lines: an svg under the names
      let svg = el.querySelector("svg") as SVGSVGElement | null;
      if (!svg) { svg = document.createElementNS("http://www.w3.org/2000/svg", "svg"); svg.setAttribute("class", "absolute inset-0 h-full w-full"); el.prepend(svg); }
      const lines = new Map(Array.from(svg.children).map((c) => [(c as SVGLineElement).dataset.key!, c as SVGLineElement]));
      for (const [key, [x, y]] of chips) {
        let ln = lines.get(key);
        if (!ln) { ln = document.createElementNS("http://www.w3.org/2000/svg", "line"); ln.dataset.key = key; ln.setAttribute("stroke", "#0d9488"); ln.setAttribute("stroke-width", "1"); ln.setAttribute("opacity", "0.7"); svg.appendChild(ln); }
        const p = pts.find((o) => o.it.key === key)!.p;
        ln.setAttribute("x1", String(x)); ln.setAttribute("y1", String(y)); ln.setAttribute("x2", String(p[0])); ln.setAttribute("y2", String(p[1]));
        lines.delete(key);
      }
      for (const ln of lines.values()) ln.remove();
      // while the parts' names line up beside the bar, the room's other names stand back (only the unit's own show)
      const shownItems = chips.size ? items.filter((it) => it.parent === unit || it.key === unit) : items;
      for (const it of shownItems) {
        let n = have.get(it.key);
        if (!n) {
          n = document.createElement("span");
          n.dataset.key = it.key;
          n.className = "pointer-events-auto absolute -translate-x-1/2 cursor-pointer whitespace-nowrap rounded bg-white/85 px-1 text-[11px] leading-4 text-stone-800 shadow-sm hover:bg-white hover:ring-1 hover:ring-teal-500";
          const key = it.key;
          n.onclick = (ev) => { ev.stopPropagation(); fromView.current = true; cb.current.onSelect?.(key); };   // a name selects its thing
          n.onpointerdown = (ev) => {                          // ... and a press-and-drag on it moves it, as on the thing
            if (ev.button !== 0) return;
            const r = host.getBoundingClientRect(), ed = edRef.current;
            const x = ((ev.clientX - r.left) / r.width) * 2 - 1, y = -(((ev.clientY - r.top) / r.height) * 2 - 1);
            if (ed?.grabItem(key, x, y)) {
              ev.stopPropagation();
              host.setPointerCapture(ev.pointerId);
              fromView.current = true; cb.current.onSelect?.(key);
            }
          };
          el.appendChild(n);
        }
        const words = it.parent ? shortName(it.label!, all.find((x) => x.key === it.parent)?.label) : it.label!;
        if (n.textContent !== words) n.textContent = words;
        const on = it.key === sel;
        n.style.background = on ? "#0d9488" : ""; n.style.color = on ? "#fff" : "";
        const chip = chips.get(it.key);
        const [nx, ny] = chip ? [chip[0], chip[1] - 8] : (() => { const q = scr(it.at); return [q[0], q[1] + 10]; })();
        n.style.left = `${nx}px`;
        n.style.top = `${ny}px`;
        have.delete(it.key);
      }
      for (const n of have.values()) n.remove();
    };
    const loop = (now: number) => {
      sim.frame(now);
      placeNames();
      frames++;
      if (now - fpsAt >= 1000) {                               // adaptive resolution: hold ~50 fps
        const fps = (frames * 1000) / (now - fpsAt);
        setFps(Math.round(fps));
        frames = 0; fpsAt = now;
        const was = sim.scale;
        if (shot) sim.scale = 1;                               // a picture: full size, however slowly it draws
        else if (fps < 48 && sim.scale > 0.4) sim.scale = Math.max(0.4, sim.scale * Math.max(0.6, fps / 55));
        else if (fps > 58 && sim.scale < 1) sim.scale = Math.min(1, sim.scale * 1.12);
        if (sim.scale !== was) fit();
        const hidden = /^brave$/i.test(gpu.trim()), soft = /swiftshader|software|llvmpipe/i.test(gpu);
        const lux = sim.light.lux;                             // the light in the room now (light.ts): direct and bounced
        setPerf(`light in the room ≈ ${lux < 1 ? lux.toFixed(1) : lux < 10 ? lux.toFixed(0) : String(Math.round(lux / 5) * 5)} lux · ${fps.toFixed(0)} fps · drawn at ${Math.round(sim.scale * 100)}% · GPU: ${hidden ? "in use (Brave hides its name)" : gpu.replace(/^ANGLE \(|\)$/g, "")}${soft ? " (software!)" : ""}`);
      }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    const wt = window.setInterval(() => setWords(sim.words()), 300);
    return () => { cancelAnimationFrame(raf); window.clearInterval(wt); ro.disconnect(); ed.dispose(); edRef.current = null; sim.dispose(); simRef.current = null; };
  }, [room]);

  // the room as configured
  useEffect(() => {
    let dead = false;
    const get = () => loadRoom(room).then((m) => {
      if (dead) return;
      setModel(m); setRoomErr(null);
      simRef.current?.setModel(m);
      if (shot) simRef.current?.view(shot.view, true);
    }).catch((e) => !dead && setRoomErr(e instanceof Error ? e.message : String(e)));
    void get();
    const t = window.setInterval(get, ROOM_POLL_MS);
    return () => { dead = true; window.clearInterval(t); };
  }, [room, refresh]);

  // the editor: what can be picked, which is selected, the gizmo's mode
  useEffect(() => {
    const ed = edRef.current, sim = simRef.current;
    if (!ed || !sim) return;
    ed.setItems(editables ?? []);
    sim.overlayOn = (editables ?? []).length > 0;
  }, [editables, model]);
  // editing starts: remember the room view; editing ends (Esc, Done, a click on empty space): glide back to it
  const roomView = useRef<ReturnType<RoomSim["pose"]> | null>(null);
  const wasSelected = useRef<string | null>(null);
  useEffect(() => {
    const sim = simRef.current;
    if (sim && selected && !wasSelected.current) roomView.current = sim.pose();
    if (sim && !selected && wasSelected.current && roomView.current) { sim.restore(roomView.current); roomView.current = null; }
    wasSelected.current = selected;
  }, [selected]);
  useEffect(() => {
    const ed = edRef.current;
    if (ed && ed.selected !== selected) ed.select(selected);
  }, [selected, editables, model]);
  useEffect(() => { simRef.current?.highlight(selected, editShow); }, [selected, model, editShow]);
  useEffect(() => { if (liveAim) simRef.current?.liveAim(liveAim.id, liveAim.dir); }, [liveAim]);   // what is selected lights up
  useEffect(() => { if (model) edRef.current?.setRoom(model.size); }, [model]);
  useEffect(() => {
    const it = (editables ?? []).find((x) => x.key === selected);
    const here = fromView.current;
    fromView.current = false;
    if (here && simRef.current?.flat) return;                    // picked on a flat view: the drawing stays put
    if (frame > 0 && it) simRef.current?.focus(it.at, Math.max(1.2, 2.2 * Math.max(...it.size)));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [frame]);

  // the keep-out regions: red when a laser's burst crosses one
  useEffect(() => {
    const crossed = new Set(Object.values(model?.lasers ?? {}).flatMap((c) => c.crosses));
    simRef.current?.setKeepOut((regions ?? model?.keepOut ?? []).map((r) => (
      { centre: r.centre, size: r.size, crossed: crossed.has(r.name), picked: r.name === picked })));
  }, [model, regions, picked]);

  // the wire: the virtual node's DMX output (one event per DMX refresh), and its status
  useEffect(() => {
    const es = new EventSource(`${node}/dmx${dmxQuery}`);
    es.onmessage = (ev) => {
      const sim = simRef.current;
      if (!sim) return;
      try {
        const m = JSON.parse(ev.data), bin = atob(m.d), u = new Uint8Array(512);
        for (let i = 0; i < 512; i++) u[i] = bin.charCodeAt(i);
        sim.dmx = u; sim.dmxAt = performance.now();
        // the preview's planned lights (drawn here only, never on a wire): each one's channels, by its id
        if (m.p && typeof m.p === "object") {
          const pm = new Map<string, Uint8Array>();
          for (const [id, b64] of Object.entries(m.p as Record<string, string>)) {
            const b = atob(b64), a = new Uint8Array(b.length);
            for (let i = 0; i < b.length; i++) a[i] = b.charCodeAt(i);
            pm.set(id, a);
          }
          sim.plannedDmx = pm;
        } else sim.plannedDmx = null;
      } catch { /* a bad event: keep the last frame */ }
    };
    const st = window.setInterval(async () => {
      try {
        const s: NodeStatus = await (await fetch(`${node}/status`, { cache: "no-store" })).json();
        setNodeSt(s);
        if (simRef.current) simRef.current.universe = s.node?.universe ?? null;
      } catch { setNodeSt(null); }
      setDmxLive(!!simRef.current && performance.now() - simRef.current.dmxAt < 1500);
    }, 1000);
    return () => { es.close(); window.clearInterval(st); };
  }, [node, dmxQuery]);

  useEffect(() => { if (simRef.current) simRef.current.showPaths = showPaths; }, [showPaths]);

  // the haze in the room: show_service's own estimate (atmosphere.py), which is what drives the hazer
  useEffect(() => {
    let dead = false;
    const get = async () => {
      try {
        const h: Haze = await (await fetch(`${room.replace(/\/+$/, "")}/show/api/v1/haze`, { cache: "no-store" })).json();
        if (dead) return;
        setHaze(h);
        if (simRef.current && hazeDragRef.current == null) simRef.current.sigma = shot?.sigma ?? sigmaOf(h.estimate_ml_m3, h.levels);
      } catch { if (!dead) setHaze(null); }
    };
    void get();
    const t = window.setInterval(get, 1000);
    return () => { dead = true; window.clearInterval(t); };
  }, [room]);

  // Moving round the room: the camera is three.js's OrbitControls (scene.ts), set up as the browser's 3D tools agree
  // (left-drag orbits, right-drag or Ctrl / Shift + drag pans, the wheel or a pinch zooms towards the cursor). This
  // page adds what a 3D editor adds over that: hovering a thing outlines and names it, a click selects it, a
  // double-click focuses on the place clicked (on nothing: back to the start), Home goes back to the start, and Ctrl
  // held while a handle is dragged snaps.
  const drag = useRef(false);
  const viewRef = useRef<(v: "3d" | "plan" | "front" | "side") => void>(() => {});
  const flipRef = useRef<() => void>(() => {});
  const hover = useRef(false);
  const [tip, setTip] = useState<{ x: number; y: number; text: string } | null>(null);   // the name by the cursor
  const [cursor, setCursor] = useState("default");
  const ndc = (e: { clientX: number; clientY: number }, el: Element) => {
    const r = el.getBoundingClientRect();
    return [((e.clientX - r.left) / r.width) * 2 - 1, -(((e.clientY - r.top) / r.height) * 2 - 1)] as const;
  };
  useEffect(() => {
    const typing = (t: EventTarget | null) => !!t && ["INPUT", "TEXTAREA", "SELECT"].includes((t as HTMLElement).tagName);
    const down = (e: KeyboardEvent) => {
      if (e.key === "Control" || e.key === "Meta") edRef.current?.setSnap(true);   // snap while a handle is dragged
      if (typing(e.target) || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "Home" && hover.current) { e.preventDefault(); simRef.current?.home(); }
      else if (hover.current && ["5", "7", "1", "3", "9"].includes(e.key) && embedded) {
        e.preventDefault();
        if (e.key === "9") { if (simRef.current?.nav === "elev") flipRef.current(); }
        else viewRef.current(({ "5": "3d", "7": "plan", "1": "front", "3": "side" } as const)[e.key as "5" | "7" | "1" | "3"]);
      }
      else if (hover.current && simRef.current?.key(e.code, true)) e.preventDefault();   // walking: W A S D, arrows, Q E
    };
    const up = (e: KeyboardEvent) => {
      if (e.key === "Control" || e.key === "Meta") edRef.current?.setSnap(false);
      simRef.current?.key(e.code, false);
    };
    const blur = () => simRef.current?.clearKeys();
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    window.addEventListener("blur", blur);
    return () => { window.removeEventListener("keydown", down); window.removeEventListener("keyup", up); window.removeEventListener("blur", blur); };
  }, []);
  const a = nodeSt?.artnet;
  const artLive = a && a.age_s != null && a.age_s < 4;
  // The page's chrome: in the Light setup page (embedded) the app's own light theme round the view; on its own, the
  // dark room-monitor look. The view itself is the room, dark or lit as the lights make it, either way.
  const T = embedded
    ? { root: "relative flex h-[72vh] min-h-[460px] flex-col overflow-hidden rounded-xl border border-stone-200 bg-white text-stone-800",
        header: "flex flex-wrap items-center gap-2 border-b border-stone-200 bg-white px-3 py-2", pill: "rounded-full bg-stone-100 px-2.5 py-0.5",
        btn: "rounded-lg border border-stone-300 bg-white px-2.5 py-1 capitalize hover:bg-stone-50", ok: "#15803d", bad: "#b91c1c",
        track: "bg-stone-200", fill: "bg-stone-500", tick: "bg-stone-400", muted: "text-stone-500",
        aside: "w-[300px] shrink-0 overflow-auto border-l border-stone-200 bg-white px-3 py-2 text-[13px]" }
    : { root: "fixed inset-0 flex flex-col bg-[#06070a] text-[#e3e6ee]",
        header: "flex flex-wrap items-center gap-2 border-b border-[#252a38] bg-[#0d1017] px-3 py-1.5", pill: "rounded-full bg-[#1a1e2a] px-2.5 py-0.5",
        btn: "rounded border border-[#2c3346] bg-[#1a1e2a] px-2 py-0.5 capitalize", ok: "#6bd38a", bad: "#ff6b6b",
        track: "bg-[#2c3346]", fill: "bg-[#cfd4dc]", tick: "bg-[#6b7183]", muted: "text-[#8d93a5]",
        aside: "w-[300px] shrink-0 overflow-auto border-l border-[#252a38] bg-[#0b0d13] px-3 py-2 text-[13px]" };
  const pill = T.pill;
  const previewing = !!feed && !feed.live;
  // How you move round the room: walk (you stand in it) or orbit (round a point), and the places to go to.
  const dark = !embedded;
  const seg = (on: boolean) => `h-8 px-3 ${on ? (dark ? "bg-[#2c3346] text-white" : "bg-stone-800 text-white") : dark ? "hover:bg-[#1a1e2a]" : "hover:bg-stone-50"}`;
  // Views and moving are separate (owner, 27 Nov: "are navigation styles distinct from views, maybe?"): the VIEW is
  // where you look from: 3D, the Plan (from above) or an elevation (Front: the wall people face; Side: a side wall; ⇄
  // looks from the opposite wall). The flat views only slide and zoom. MOVING (Walk or Orbit) is how you move in 3D,
  // and only shows there, with the places to stand. ⌂: the whole room in the view you are in. Keys while the pointer
  // is over the view, as Blender's: 5 3D, 7 Plan, 1 Front, 3 Side, 9 the opposite wall.
  const flat = nav === "plan" || nav === "elev";
  const bd = dark ? "border-[#2c3346]" : "border-stone-300";
  const view = nav === "plan" ? "plan" : nav === "elev" ? (elev === "front" || elev === "back" ? "front" : "side") : "3d";
  const VIEWS: [string, string, string][] = [["3d", "3D", "The room in 3D: walk through it or orbit round (5)"],
    ["plan", "Plan", "From straight above, as a map (7)"], ["front", "Front", "An elevation: looking at the wall people face (1)"],
    ["side", "Side", "An elevation: looking at a side wall (3)"]];
  viewRef.current = showView;
  flipRef.current = flipElev;
  const navControls = (
    <div className="flex shrink-0 items-center gap-2" data-testid="nav-controls">
      {/* a narrow header (the embedded editor's, a container): the views as a dropdown */}
      {embedded && (
        <select value={view} aria-label="View" className={`h-8 rounded-lg border bg-white px-2 text-stone-800 ${bd} [@container(min-width:1100px)]:hidden`}
          onChange={(e) => showView(e.target.value as "3d" | "plan" | "front" | "side")}>
          {VIEWS.map(([v, words]) => <option key={v} value={v}>{words}</option>)}
        </select>
      )}
      <div className={`${embedded ? "hidden [@container(min-width:1100px)]:inline-flex" : "inline-flex"} overflow-hidden rounded-lg border ${bd}`} role="group" aria-label="View">
        {VIEWS.map(([v, words, title], i) => (
          <button key={v} className={`${seg(view === v)} ${i ? `border-l ${bd}` : ""}`} aria-pressed={view === v} data-testid={`view-${v}`} title={title}
            onClick={() => showView(v as "3d" | "plan" | "front" | "side")}>{words}</button>
        ))}
      </div>
      {/* the elevations' ⇄, the 3D view's Walk / Orbit and places: each keeps its slot when it doesn't apply */}
      <button className={`h-8 w-[78px] shrink-0 rounded-lg border px-2 ${bd} ${dark ? "" : "hover:bg-stone-50"} ${nav === "elev" ? "" : "invisible"}`} data-testid="view-flip"
        tabIndex={nav === "elev" ? 0 : -1} aria-hidden={nav !== "elev"}
        title={`Look from the other side: now at the ${elev} wall (9)`} onClick={() => flipElev()}>⇄ {elev}</button>
      {(
        <div className={`flex shrink-0 items-center gap-2 ${flat ? "invisible" : ""}`} aria-hidden={flat}>
          <div className={`inline-flex overflow-hidden rounded-lg border ${bd}`} role="group" aria-label="How to move">
            <button className={seg(nav === "walk")} aria-pressed={nav === "walk"} data-testid="nav-walk" onClick={() => chooseNav("walk")}
              title="Stand in the room: drag to look round, click the floor to walk there, wheel or W/S to walk, A/D to step aside, Q/E lower/higher">Walk</button>
            <button className={`${seg(nav === "orbit")} border-l ${bd}`} aria-pressed={nav === "orbit"} data-testid="nav-orbit" onClick={() => chooseNav("orbit")}
              title="Turn round a point: drag to orbit, right-drag to pan, wheel to zoom to the cursor">Orbit</button>
          </div>
          {nav === "walk" ? (
          <select value="" aria-label="Go to" data-testid="go-to"
            className={`h-8 rounded-lg border px-2 ${bd} ${dark ? "bg-[#1a1e2a] text-[#e3e6ee]" : "bg-white text-stone-800"} ${embedded ? "w-[84px] [@container(min-width:900px)]:w-[100px] [@container(min-width:1250px)]:w-[150px]" : ""}`}
            onChange={(e) => { const v = e.target.value; if (v) simRef.current?.standAt(v); }}>
            <option value="">Stand at…</option>
            {(simRef.current?.standPlaces() ?? []).map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
          </select>) : (
          <select value="" aria-label="Look from" data-testid="look-from"
            className={`h-8 rounded-lg border px-2 ${bd} ${dark ? "bg-[#1a1e2a] text-[#e3e6ee]" : "bg-white text-stone-800"} ${embedded ? "w-[84px] [@container(min-width:900px)]:w-[100px] [@container(min-width:1250px)]:w-[150px]" : ""}`}
            onChange={(e) => { const v = e.target.value; if (v) simRef.current?.orbitAt(v); }}>
            <option value="">Look from…</option>
            <optgroup label="The room">
              {(simRef.current?.orbitPlaces() ?? []).filter((p) => p.group === "room").map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
            </optgroup>
            {(simRef.current?.orbitPlaces() ?? []).some((p) => p.group === "thing") && (
              <optgroup label="Orbit round">
                {(simRef.current?.orbitPlaces() ?? []).filter((p) => p.group === "thing").map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
              </optgroup>
            )}
          </select>
          )}
        </div>
      )}
      <button className={`h-8 rounded-lg border px-2.5 ${bd} ${dark ? "" : "hover:bg-stone-50"}`} title="The whole room in this view (Home)" aria-label="The whole room"
        data-testid="home" onClick={() => simRef.current?.home()}>⌂</button>
    </div>
  );
  return (
    <div className={T.root} style={{ fontSize: 13 }} data-testid="room-sim">
      {embedded ? (
        // The editor's header: one row, all from the left, every toggle in a place of its own whatever is chosen (owner,
        // 27 Nov: "some controls right aligned is bad because 3D / Plan / Orbit etc. toggles change what comes after so
        // control jumps away below the mouse"): the views and moving (their slots kept when hidden), ⌂ ⋯ ⧉, then what
        // the lights show, whose own changing controls come last.
        <header className={shot ? "hidden" : "flex items-center gap-2 border-b border-stone-200 bg-white px-3 py-2 [container-type:inline-size]"} data-testid="sim-header">
          {navControls}
          <div className="relative">
            <button className="h-8 rounded-lg border border-stone-300 px-2.5 hover:bg-stone-50" aria-expanded={opts} aria-label="More" title="Haze, beam paths"
              onClick={() => setOpts((o) => !o)} data-testid="sim-more">⋯</button>
            {opts && (
              <>
                <div className="fixed inset-0 z-20" onClick={() => setOpts(false)} />
                <div className="absolute right-0 top-full z-30 mt-1 w-72 space-y-3 rounded-lg border border-stone-200 bg-white p-3 shadow-lg">
                  <div>
                    <div className="mb-1 text-[12px] font-semibold uppercase tracking-wide text-stone-500">Haze in the room</div>
        {(() => {
          // the haze in the air now (show_service's model): drag the bar to say what the room is, by eye
          const live = sigmaOf(haze?.estimate_ml_m3, haze?.levels);
          const pos = hazeDrag ?? sliderOf(live);
          const at = (x: number) => { const r = hazeBar.current!.getBoundingClientRect(); return Math.max(0, Math.min(1, (x - r.left) / r.width)); };
          const preview = (p: number) => { hazeDragRef.current = p; setHazeDrag(p); if (simRef.current) simRef.current.sigma = sigmaOfSlider(p); };
          const commit = async (p: number) => {
            try {
              const r = await fetch(`${room.replace(/\/+$/, "")}/show/api/v1/haze/now`, { method: "PUT",
                headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ml_m3: mlOf(sigmaOfSlider(p), haze?.levels) }) });
              if (r.ok) setHaze(await r.json());
            } catch { /* the next read shows what holds */ }
            hazeDragRef.current = null; setHazeDrag(null);
          };
          return (
            <span className="flex items-center gap-2" title="The haze in the air now (modelled). Drag to set it by eye; the model carries on from there.">
              <span ref={hazeBar} className={`relative inline-block h-3 w-32 cursor-ew-resize touch-none rounded ${T.track}`}
                onPointerDown={(e) => { if (!haze) return; e.currentTarget.setPointerCapture(e.pointerId); preview(at(e.clientX)); }}
                onPointerMove={(e) => { if (hazeDragRef.current != null) preview(at(e.clientX)); }}
                onPointerUp={() => { if (hazeDragRef.current != null) void commit(hazeDragRef.current); }}>
                <span className={`absolute inset-y-0 left-0 rounded ${T.fill}`} style={{ width: `${pos * 100}%` }} />
                {Object.entries(LEVEL_VISIBILITY_M).map(([w, v]) => (
                  <span key={w} className={`absolute inset-y-0 w-px ${T.tick}`} style={{ left: `${sliderOf(3 / v) * 100}%` }} title={w} />
                ))}
              </span>
              <span className={`w-20 ${T.muted}`}>{haze ? hazeWords(hazeDrag != null ? sigmaOfSlider(hazeDrag) : live) : "no reading"}</span>
            </span>
          );
        })()}
                  </div>
                  <label className="flex cursor-pointer items-center gap-2" title="The view never goes through a wall or the ceiling (it never goes under the floor either way).">
                    <input type="checkbox" checked={inside} onChange={(e) => setInside(e.target.checked)} data-testid="keep-inside" /> Keep the view inside the room
                  </label>
                  <label className="flex cursor-pointer items-center gap-2" title="An aid for aiming, not what the room looks like: draws every beam half-visible whatever the haze.">
                    <input type="checkbox" checked={showPaths} onChange={(e) => setShowPaths(e.target.checked)} /> Show every beam's path
                  </label>
                </div>
              </>
            )}
          </div>
          <a href="/lights/room" target="_blank" rel="noreferrer" title="Open the live view in a new window" aria-label="Open the live view in a new window"
            className="hidden h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-stone-300 text-[15px] hover:bg-stone-50 [@container(min-width:900px)]:inline-flex" data-testid="pop-out">⧉</a>
          <span className="mx-1 h-6 w-px shrink-0 bg-stone-200" aria-hidden />
          {toolbar}
        </header>
      ) : (
      <header className={shot ? "hidden" : T.header}>
        <b className="text-[15px]">{embedded ? "Room" : "Room in 3D"}</b>
        {toolbar}
        <span className={pill} style={{ color: roomErr ? T.bad : T.ok }}>
          {roomErr ? `room: ${roomErr}` : model ? `room ${model.size.join(" × ")} m · ${model.fixtures.length} fixtures${model.problems.length ? ` · ${model.problems.length} problems` : ""}` : "room: loading"}
        </span>
        <span className={pill} style={{ color: dmxLive && (artLive || previewing) ? T.ok : T.bad }}>
          {previewing ? (dmxLive ? "preview: nothing is sent to the lights" : "starting the preview…")
            : !dmxLive ? `no DMX from the virtual node (${node})` : artLive ? `live: Art-Net ${a?.pps}/s · universe ${nodeSt?.node?.universe}${a?.merging ? " · MERGING" : ""}` : "node up, no Art-Net (holding)"}
        </span>
        <span className="flex-1" />
        {(() => {
          // the haze in the air now (show_service's model): drag the bar to say what the room is, by eye
          const live = sigmaOf(haze?.estimate_ml_m3, haze?.levels);
          const pos = hazeDrag ?? sliderOf(live);
          const at = (x: number) => { const r = hazeBar.current!.getBoundingClientRect(); return Math.max(0, Math.min(1, (x - r.left) / r.width)); };
          const preview = (p: number) => { hazeDragRef.current = p; setHazeDrag(p); if (simRef.current) simRef.current.sigma = sigmaOfSlider(p); };
          const commit = async (p: number) => {
            try {
              const r = await fetch(`${room.replace(/\/+$/, "")}/show/api/v1/haze/now`, { method: "PUT",
                headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ml_m3: mlOf(sigmaOfSlider(p), haze?.levels) }) });
              if (r.ok) setHaze(await r.json());
            } catch { /* the next read shows what holds */ }
            hazeDragRef.current = null; setHazeDrag(null);
          };
          return (
            <span className={`${pill} flex items-center gap-2`} title="The haze in the air now (modelled). Drag to set it by eye; the model carries on from there.">
              Haze
              <span ref={hazeBar} className={`relative inline-block h-3 w-32 cursor-ew-resize touch-none rounded ${T.track}`}
                onPointerDown={(e) => { if (!haze) return; e.currentTarget.setPointerCapture(e.pointerId); preview(at(e.clientX)); }}
                onPointerMove={(e) => { if (hazeDragRef.current != null) preview(at(e.clientX)); }}
                onPointerUp={() => { if (hazeDragRef.current != null) void commit(hazeDragRef.current); }}>
                <span className={`absolute inset-y-0 left-0 rounded ${T.fill}`} style={{ width: `${pos * 100}%` }} />
                {Object.entries(LEVEL_VISIBILITY_M).map(([w, v]) => (
                  <span key={w} className={`absolute inset-y-0 w-px ${T.tick}`} style={{ left: `${sliderOf(3 / v) * 100}%` }} title={w} />
                ))}
              </span>
              <span className={`w-20 ${T.muted}`}>{haze ? hazeWords(hazeDrag != null ? sigmaOfSlider(hazeDrag) : live) : "no reading"}</span>
            </span>
          );
        })()}
        <label className={`${pill} cursor-pointer`} title="An aid for aiming, not what the room looks like: draws every beam half-visible whatever the haze.">
          <input type="checkbox" checked={showPaths} onChange={(e) => setShowPaths(e.target.checked)} /> Show beam paths
        </label>
        {navControls}
        {!embedded && <a className={pill} href={`${node}/`} target="_blank" rel="noreferrer">Board</a>}
        {!embedded && <a className={pill} href="/settings/lights?tab=3d">Lights setup</a>}
      </header>
      )}
      <div className="relative flex min-h-0 flex-1">
        <div ref={hostRef} className="relative min-w-0 flex-1"
          onPointerEnter={() => { hover.current = true; }}
          onPointerLeave={() => { hover.current = false; edRef.current?.clearHover(); simRef.current?.walkHint(null); simRef.current?.clearKeys(); setTip(null); }}
          onContextMenu={(e) => e.preventDefault()}
          onPointerDownCapture={(e) => {
            if ((e.target as HTMLElement).dataset?.key) return;          // a name label: its own click selects
            if (cb.current.pointing) return;                             // pointing: a click picks a place, it grabs nothing
            // before the camera's controls see it: a press on the selected thing or one of its handles is a drag of
            // that thing (the camera holds still: onDragging), anything else is the camera's
            const ed = edRef.current;
            if (e.button !== 0 || !ed || !ed.active) return;
            const [x, y] = ndc(e, e.currentTarget);
            if (ed.grab(x, y)) { e.currentTarget.setPointerCapture(e.pointerId); setCursor("grabbing"); setTip(null); }
          }}
          onPointerDown={(e) => {
            if ((e.target as HTMLElement).dataset?.key) { press.current = null; return; }
            press.current = e.button === 0 ? { x: e.clientX, y: e.clientY } : null;
            if (edRef.current?.dragging) return;
            drag.current = true;
            setCursor(e.button === 0 && !(e.shiftKey || e.ctrlKey || e.metaKey) ? "grabbing" : "move");
            setTip(null);
            edRef.current?.clearHover();
            simRef.current?.walkHint(null);
          }}
          onPointerMove={(e) => {
            const ed = edRef.current, sim = simRef.current;
            const [x, y] = ndc(e, e.currentTarget);
            const r = e.currentTarget.getBoundingClientRect();
            if (ed?.dragging) {                                          // the thing being dragged follows
              ed.dragTo(x, y);
              const w = ed.readout();
              setTip(w ? { x: e.clientX - r.left, y: e.clientY - r.top, text: w } : null);
              return;
            }
            if (drag.current) return;
            const it = ed && ed.active ? ed.hover(x, y) : null;          // over a thing: outline it, name it
            const tipText = it && it.label ? it.label : null;
            setTip((t) => (tipText ? { x: e.clientX - r.left, y: e.clientY - r.top, text: tipText } : t ? null : t));
            sim?.walkHint(it || selected ? null : x, y);                // over the floor, walking: where a click goes
            const c = (ed && ed.active ? ed.cursorAt(x, y) : null) ?? "default";
            setCursor((was) => (was === c ? was : c));
          }}
          onDoubleClick={(e) => {
            const [x, y] = ndc(e, e.currentTarget), ed = edRef.current;
            const key = ed && ed.active ? ed.pick(x, y, true) : null;   // straight to the part under the cursor
            if (key) { fromView.current = true; cb.current.onSelect?.(key); }   // a thing: select it
            else if (simRef.current?.nav === "orbit") simRef.current.goTo(x, y);   // orbiting: round that spot
          }}
          onPointerUp={(e) => {
            drag.current = false;
            setCursor("default");
            const ed = edRef.current, sim = simRef.current, p = press.current;
            if (ed?.dragging) {
              ed.release(); setTip(null);
              if (ed.justDragged) return;                              // moved: a drag, not a click
            }
            press.current = null;
            if (!p) return;
            if (ed?.justDragged) { ed.justDragged = false; return; }   // the end of a handle drag is not a click
            if (Math.hypot(e.clientX - p.x, e.clientY - p.y) > 4) return;   // the view was turned, nothing was clicked
            const [x, y] = ndc(e, e.currentTarget);
            if (cb.current.pointing) {                                 // "Point at…": a thing's middle, else the spot hit
              const k = cb.current.pointThings && ed && ed.active ? ed.pick(x, y, true) : null;
              const it = k ? (editables ?? []).find((q) => q.key === k && q.key !== selected) : undefined;
              const p3 = it ? it.at : (() => { const w = sim?.surfaceAt(x, y); return w ? [w.x, -w.z, w.y] as [number, number, number] : null; })();
              if (p3) cb.current.onPointAt?.([Math.round(p3[0] * 100) / 100, Math.round(p3[1] * 100) / 100, Math.round(p3[2] * 100) / 100]);
              return;
            }
            const key = ed && ed.active ? ed.pick(x, y) : null;
            if (key) {                                                 // a thing: edit it
              fromView.current = true; cb.current.onSelect?.(key);
              const lp = ed?.lastPick, r = e.currentTarget.getBoundingClientRect();
              const it = (editables ?? []).find((q) => q.key === key);
              if (lp && lp.n > 1 && it?.label) setTip({ x: e.clientX - r.left, y: e.clientY - r.top, text: `${it.label} · ${lp.i + 1} of ${lp.n} here: click again for the next` });
              return;
            }
            if (selected) { cb.current.onSelect?.(null); return; }     // nothing, while editing: back to the room
            if (sim?.nav === "walk") sim.walkTo(x, y);                 // nothing, walking: walk there
          }}>
          <canvas ref={canvasRef} className="absolute inset-0 block" style={{ cursor: pointing ? "crosshair" : cursor }} />
          {pointing && (
            <div className="pointer-events-none absolute left-1/2 top-2 z-10 -translate-x-1/2 rounded-lg bg-teal-700/95 px-3 py-1.5 text-[13px] text-white shadow" data-testid="pointing">
              {pointWords ?? "Point at: click a thing, or a spot on a wall, the floor or the ceiling · Esc: cancel"}
            </div>
          )}
          <div ref={namesRef} className="pointer-events-none absolute inset-0 overflow-hidden" data-testid="plan-names" />
          {embedded && !shot && (
            <div className="absolute left-2.5 top-2 z-10" data-testid="sim-back">
              {selected ? (() => {
                // up one level: a light to its bar (they are one unit), anything else to the whole room
                const up = (editables ?? []).find((x) => x.key === (editables ?? []).find((y) => y.key === selected)?.parent);
                return (
                  <span className="flex gap-1.5">
                    {up && <button onClick={() => cb.current.onSelect?.(up.key)} title={`Up to ${up.label ?? "its bar"} (Esc)`} data-testid="up"
                      className="rounded-lg bg-white/95 px-3 py-1.5 text-[13px] font-medium text-stone-800 shadow hover:bg-white">↑ {up.label ?? "Its bar"}</button>}
                    <button onClick={() => cb.current.onSelect?.(null)} title={up ? "Back to the whole room" : "Back to the whole room (Esc)"} data-testid="back"
                      className="rounded-lg bg-white/95 px-3 py-1.5 text-[13px] font-medium text-stone-800 shadow hover:bg-white">← Back to the room</button>
                  </span>
                );
              })() : null}
            </div>
          )}
          {embedded && !shot && (
            <div className="pointer-events-none absolute right-2.5 top-2 flex gap-1.5 text-[11px]" data-testid="sim-status">
              <span className="rounded bg-white/85 px-1.5 py-0.5 text-stone-700 shadow-sm">
                {previewing ? (dmxLive ? "Preview · nothing is sent to the lights" : "Starting the preview…")
                  : !dmxLive ? "Live · no picture from the virtual node" : artLive ? "Live · what the room is doing now" : "Live · the room has gone quiet"}
                {fps ? ` · ${fps} fps` : ""}
              </span>
              {(roomErr || model?.problems.length) ? <span className="rounded bg-rose-50/90 px-1.5 py-0.5 text-rose-700 shadow-sm">{roomErr ?? `${model?.problems.length} problems in the room set-up`}</span> : null}
            </div>
          )}
          {tip && (
            <div className="pointer-events-none absolute z-10 rounded bg-black/75 px-2 py-0.5 text-xs text-white"
              style={{ left: tip.x + 14, top: tip.y + 12 }} data-testid="hover-tip">{tip.text}</div>
          )}
          <div className={shot ? "hidden" : "pointer-events-none absolute bottom-1.5 left-2.5 right-2.5 text-xs text-[#aab0c0]"}>
            {embedded
              ? (selected && nav === "plan"
                ? "Editing, from above: a bar's parts stand on and hang under it, so their names line up beside it: click a name, or click the same spot again for the next part down · Front (1) shows them apart · Esc: up a level"
                : selected
                ? "Editing: drag to look round it · a bar: drag it, its ring to turn it, its arrow to raise it; click one of its lights to edit that · a light: the amber arc tilts it · Esc: up a level · click empty space: back"
                : nav === "elev"
                  ? `Elevation, looking at the ${elev} wall: drag a thing across and up or down · drag the wall to slide the view · wheel: zoom to the cursor · ⇄ (9): the other side · click: edit it, again for a light on a bar`
                : nav === "plan"
                  ? "Plan: drag a thing to place it (a screen goes onto the nearest wall; a bar moves whole) · drag the floor to slide the plan · wheel: zoom to the cursor · click: edit it, again (or double-click) for a light on a bar"
                : nav === "walk"
                  ? "Walking: drag to look round · click the floor to go there · wheel or W/S: forward / back · A/D: step aside · Q/E: lower / higher · click a bar to edit it, again (or double-click) for one of its lights"
                  : "Orbiting: drag to turn round · right-drag: pan · wheel: zoom to the cursor · double-click: turn round that spot · click a bar to edit it, again for one of its lights")
              : `${nav === "walk" ? "drag: look round · click the floor: walk there · wheel / W S: forward, back · A D: aside · Q E: lower, higher" : "drag: orbit · right-drag: pan · wheel: zoom · double-click: focus there"} · the lights read the Art-Net wire, at their makers' brightness; the picture adapts as an eye does · ${perf}`}
          </div>
        </div>
        {embedded && panel ? (
          <aside className={T.aside} data-testid="inspector">{panel}</aside>
        ) : null}
        <aside className={embedded || shot ? "hidden" : "overflow-auto border-l border-[#252a38] bg-[#0b0d13] px-3 py-2"} style={{ width: SIDE_W }}>
          {words.map((g) => (
            <div key={g.unit} className="mb-2">
              <h2 className="mb-1 mt-2 text-sm font-semibold">{g.unit}</h2>
              {g.rows.map(([k, v], i) => (
                <div key={i} className="grid grid-cols-[84px_1fr] gap-1.5 border-b border-[#161a24] py-0.5">
                  <span className="text-[#8d93a5]">{k}</span><span>{v}</span>
                </div>
              ))}
            </div>
          ))}
          {model?.problems.length ? (
            <div className="mt-3 text-xs text-[#ffc857]">{model.problems.map((p, i) => <div key={i}>{p}</div>)}</div>
          ) : null}
        </aside>
      </div>
    </div>
  );
}
