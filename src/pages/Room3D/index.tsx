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
import { resolveRoomUrl } from "@/pages/Room/api";
import { RoomEditor, type Editable, type EditChange, type GizmoMode } from "./editor";
import { loadRoom, type KeepRegion, type RoomModel } from "./model";
import { hazeWords, LEVEL_VISIBILITY_M, mlOf, sigmaOf, sigmaOfSlider, sliderOf } from "./haze";
import { RoomSim } from "./scene";

const ROOM_POLL_MS = 5000;
const NODE_DEFAULT = "http://127.0.0.1:8705";
const SIDE_W = 340;

type Haze = { levels?: Record<string, number>; pick?: string | null; feel?: string | null; want?: string | null; phase?: string; estimate_ml_m3?: number; target_ml_m3?: number; output?: number; fan?: number };

type NodeStatus = { node?: { udp?: string; universe?: number }; artnet?: { pps?: number; age_s?: number | null; merging?: boolean; length?: number } };

/** The room in 3D. On its own (/lights/room) it fills the window; ``embedded`` (the Lights setup's 3D tab) it fills
 *  its box and leaves the readout to the page around it. ``picked``: the keep-out region being edited there;
 *  ``regions``: the page's own (unsaved) copy of the regions, drawn in place of the room's saved ones. */
export default function Room3DPage({ embedded = false, picked = null, regions, refresh = 0, editables, selected = null,
  mode, onSelect, onEdit, panel, frame = 0, feed, toolbar }: {
  embedded?: boolean; picked?: string | null; regions?: KeepRegion[];
  /** Bump it to read the room again at once (the page around it has just saved a change). */
  refresh?: number;
  /** The editor (editor.ts): the things that can be clicked and moved, which one is selected, the gizmo's mode, and
   *  what the page does when one is picked or changed. ``panel``: the page's inspector, shown beside the view. */
  editables?: Editable[]; selected?: string | null; mode?: GizmoMode;
  onSelect?: (key: string | null) => void; onEdit?: (key: string, change: EditChange) => void; panel?: ReactNode;
  /** Bump it to turn the camera to the selected thing ("frame selected": picked from the list, or F). */
  frame?: number;
  /** Where the lights' DMX comes from, in place of the virtual node: show_service's preview (``base``/dmx``query``
   *  and ``base``/status), or the node when ``live``. */
  feed?: { base: string; query?: string; live?: boolean };
  /** The page's own controls, put in the header (embedded). */
  toolbar?: ReactNode;
} = {}) {
  const edRef = useRef<RoomEditor | null>(null);
  const cb = useRef({ onSelect, onEdit });
  cb.current = { onSelect, onEdit };
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
  const [showPaths, setShowPaths] = useState(false);
  const [opts, setOpts] = useState(false);              // the editor header's "more" menu
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
    const ed = new RoomEditor(sim.overlayRoot, sim.overlayScene, sim.camera, canvas);
    ed.onChange = (key, change) => cb.current.onEdit?.(key, change);
    ed.onDragging = (on) => { sim.controls.enabled = !on; };      // a handle being dragged holds the camera still
    edRef.current = ed;
    const fit = () => sim.resize(host.clientWidth, host.clientHeight);
    const ro = new ResizeObserver(fit);
    ro.observe(host);
    fit();
    let raf = 0, frames = 0, fpsAt = performance.now();
    const gl = sim.renderer.getContext();
    const ext = gl.getExtension("WEBGL_debug_renderer_info");
    const gpu = String(ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER));
    const loop = (now: number) => {
      sim.frame(now);
      frames++;
      if (now - fpsAt >= 1000) {                               // adaptive resolution: hold ~50 fps
        const fps = (frames * 1000) / (now - fpsAt);
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
    if (ed && ed.selected !== selected) ed.select(selected, mode);
  }, [selected, editables, model, mode]);
  useEffect(() => { if (mode) edRef.current?.setMode(mode); }, [mode, selected]);
  useEffect(() => {
    const it = (editables ?? []).find((x) => x.key === selected);
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
  const hover = useRef(false);
  const [tip, setTip] = useState<{ x: number; y: number; text: string } | null>(null);   // the name by the cursor
  const [cursor, setCursor] = useState("grab");
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
    };
    const up = (e: KeyboardEvent) => { if (e.key === "Control" || e.key === "Meta") edRef.current?.setSnap(false); };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    return () => { window.removeEventListener("keydown", down); window.removeEventListener("keyup", up); };
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
  return (
    <div className={T.root} style={{ fontSize: 13 }} data-testid="room-sim">
      {embedded ? (
        // The editor's header: one row. Left, what the lights show (the page's); right, the views and a menu of the rest.
        <header className={shot ? "hidden" : "flex items-center gap-2 border-b border-stone-200 bg-white px-3 py-2"} data-testid="sim-header">
          {toolbar}
          <span className="flex-1" />
          <div className="inline-flex overflow-hidden rounded-lg border border-stone-300" role="group" aria-label="View">
            {(["front", "side", "above"] as const).map((v, i) => (
              <button key={v} className={`h-8 px-3 capitalize hover:bg-stone-50 ${i ? "border-l border-stone-300" : ""}`} onClick={() => simRef.current?.view(v)}>{v}</button>
            ))}
            <button className="h-8 border-l border-stone-300 px-2.5 hover:bg-stone-50" title="Back to the start view (Home)" aria-label="Start view" onClick={() => simRef.current?.home()}>⌂</button>
          </div>
          <div className="relative">
            <button className="h-8 rounded-lg border border-stone-300 px-2.5 hover:bg-stone-50" aria-expanded={opts} aria-label="More" title="Haze, beam paths, the live view"
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
                  <label className="flex cursor-pointer items-center gap-2" title="An aid for aiming, not what the room looks like: draws every beam half-visible whatever the haze.">
                    <input type="checkbox" checked={showPaths} onChange={(e) => setShowPaths(e.target.checked)} /> Show every beam's path
                  </label>
                  <a className="block text-teal-700 hover:underline" href="/lights/room" target="_blank" rel="noreferrer">Open the live view in a new window ↗</a>
                </div>
              </>
            )}
          </div>
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
        {(["front", "side", "above"] as const).map((v) => (
          <button key={v} className={T.btn} onClick={() => simRef.current?.view(v)}>{v}</button>
        ))}
        {!embedded && <a className={pill} href={`${node}/`} target="_blank" rel="noreferrer">Board</a>}
        {!embedded && <a className={pill} href="/settings/lights?tab=3d">Lights setup</a>}
      </header>
      )}
      <div className="relative flex min-h-0 flex-1">
        <div ref={hostRef} className="relative min-w-0 flex-1"
          onPointerEnter={() => { hover.current = true; }}
          onPointerLeave={() => { hover.current = false; edRef.current?.clearHover(); setTip(null); }}
          onContextMenu={(e) => e.preventDefault()}
          onPointerDown={(e) => {
            press.current = e.button === 0 ? { x: e.clientX, y: e.clientY } : null;
            if (e.button === 0 && edRef.current?.onHandle) return;     // the gizmo's own press: not the camera's
            drag.current = true;
            setCursor(e.button === 0 && !(e.shiftKey || e.ctrlKey || e.metaKey) ? "grabbing" : "move");
            setTip(null);
            edRef.current?.clearHover();
          }}
          onPointerMove={(e) => {
            const ed = edRef.current;
            if (drag.current || !ed || !ed.active || ed.dragging) return;
            const [x, y] = ndc(e, e.currentTarget);                      // over a thing: outline it, name it, a hand
            const it = ed.onHandle ? null : ed.hover(x, y);
            const r = e.currentTarget.getBoundingClientRect();
            setTip(it && it.label ? { x: e.clientX - r.left, y: e.clientY - r.top, text: it.label } : null);
            setCursor(ed.onHandle || it ? "pointer" : "grab");
          }}
          onDoubleClick={(e) => {
            const [x, y] = ndc(e, e.currentTarget), ed = edRef.current;
            const key = ed && ed.active ? ed.pick(x, y) : null;
            if (key) cb.current.onSelect?.(key);                       // a thing: select it (the page frames it)
            else simRef.current?.goTo(x, y);                           // a wall, the floor: orbit round that spot
          }}
          onPointerUp={(e) => {
            drag.current = false;
            setCursor("grab");
            const ed = edRef.current, p = press.current;
            press.current = null;
            if (!ed || !ed.active || !p) return;
            if (ed.justDragged) { ed.justDragged = false; return; }    // the end of a gizmo drag is not a click
            if (Math.hypot(e.clientX - p.x, e.clientY - p.y) > 4) return;   // the view was turned, nothing was clicked
            const [x, y] = ndc(e, e.currentTarget);
            cb.current.onSelect?.(ed.pick(x, y));
          }}>
          <canvas ref={canvasRef} className="absolute inset-0 block" style={{ cursor }} />
          {embedded && !shot && (
            <div className="pointer-events-none absolute left-2.5 top-2 flex gap-1.5 text-[11px]" data-testid="sim-status">
              <span className="rounded bg-white/85 px-1.5 py-0.5 text-stone-700 shadow-sm">
                {previewing ? (dmxLive ? "Preview · nothing is sent to the lights" : "Starting the preview…")
                  : !dmxLive ? "Live · no picture from the virtual node" : artLive ? "Live · what the room is doing now" : "Live · the room has gone quiet"}
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
              ? "Drag: orbit · right-drag or Shift-drag: pan · wheel: zoom to the cursor · double-click: focus there (on empty space: back to the start) · F: frame the selection · Home: start view"
              : `drag: orbit · right-drag: pan · wheel: zoom · double-click: focus there · the lights read the Art-Net wire, at their makers' brightness; the picture adapts as an eye does · ${perf}`}
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
