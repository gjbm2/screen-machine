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

import { useEffect, useMemo, useRef, useState } from "react";
import { resolveRoomUrl } from "@/pages/Room/api";
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
export default function Room3DPage({ embedded = false, picked = null, regions }: {
  embedded?: boolean; picked?: string | null; regions?: KeepRegion[];
} = {}) {
  const room = useMemo(() => resolveRoomUrl(window.location.search), []);
  const node = useMemo(() => (new URLSearchParams(window.location.search).get("node") || NODE_DEFAULT).replace(/\/+$/, ""), []);
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
        if (fps < 48 && sim.scale > 0.4) sim.scale = Math.max(0.4, sim.scale * Math.max(0.6, fps / 55));
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
    return () => { cancelAnimationFrame(raf); window.clearInterval(wt); ro.disconnect(); sim.dispose(); simRef.current = null; };
  }, [room]);

  // the room as configured
  useEffect(() => {
    let dead = false;
    const get = () => loadRoom(room).then((m) => {
      if (dead) return;
      setModel(m); setRoomErr(null);
      simRef.current?.setModel(m);
    }).catch((e) => !dead && setRoomErr(e instanceof Error ? e.message : String(e)));
    void get();
    const t = window.setInterval(get, ROOM_POLL_MS);
    return () => { dead = true; window.clearInterval(t); };
  }, [room]);

  // the keep-out regions: red when a laser's burst crosses one
  useEffect(() => {
    const crossed = new Set(Object.values(model?.lasers ?? {}).flatMap((c) => c.crosses));
    simRef.current?.setKeepOut((regions ?? model?.keepOut ?? []).map((r) => (
      { centre: r.centre, size: r.size, crossed: crossed.has(r.name), picked: r.name === picked })));
  }, [model, regions, picked]);

  // the wire: the virtual node's DMX output (one event per DMX refresh), and its status
  useEffect(() => {
    const es = new EventSource(`${node}/dmx`);
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
  }, [node]);

  useEffect(() => { if (simRef.current) simRef.current.showPaths = showPaths; }, [showPaths]);

  // the haze in the room: show_service's own estimate (atmosphere.py), which is what drives the hazer
  useEffect(() => {
    let dead = false;
    const get = async () => {
      try {
        const h: Haze = await (await fetch(`${room.replace(/\/+$/, "")}/show/api/v1/haze`, { cache: "no-store" })).json();
        if (dead) return;
        setHaze(h);
        if (simRef.current && hazeDragRef.current == null) simRef.current.sigma = sigmaOf(h.estimate_ml_m3, h.levels);
      } catch { if (!dead) setHaze(null); }
    };
    void get();
    const t = window.setInterval(get, 1000);
    return () => { dead = true; window.clearInterval(t); };
  }, [room]);

  // drag to orbit, wheel to zoom
  const drag = useRef<{ x: number; y: number } | null>(null);
  const a = nodeSt?.artnet;
  const artLive = a && a.age_s != null && a.age_s < 4;
  const pill = "rounded-full bg-[#1a1e2a] px-2.5 py-0.5";
  return (
    <div className={embedded ? "relative flex h-[70vh] min-h-[420px] flex-col overflow-hidden rounded-xl bg-[#06070a] text-[#e3e6ee]"
      : "fixed inset-0 flex flex-col bg-[#06070a] text-[#e3e6ee]"} style={{ fontSize: 13 }} data-testid="room-sim">
      <header className="flex flex-wrap items-center gap-2 border-b border-[#252a38] bg-[#0d1017] px-3 py-1.5">
        <b className="text-[15px]">Room in 3D</b>
        <span className={pill} style={{ color: roomErr ? "#ff6b6b" : "#6bd38a" }}>
          {roomErr ? `room: ${roomErr}` : model ? `room ${model.size.join(" × ")} m · ${model.fixtures.length} fixtures${model.problems.length ? ` · ${model.problems.length} problems` : ""}` : "room: loading"}
        </span>
        <span className={pill} style={{ color: dmxLive && artLive ? "#6bd38a" : "#ff6b6b" }}>
          {!dmxLive ? `no DMX from the virtual node (${node})` : artLive ? `Art-Net ${a?.pps}/s · universe ${nodeSt?.node?.universe}${a?.merging ? " · MERGING" : ""}` : "node up, no Art-Net (holding)"}
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
              <span ref={hazeBar} className="relative inline-block h-3 w-32 cursor-ew-resize touch-none rounded bg-[#2c3346]"
                onPointerDown={(e) => { if (!haze) return; e.currentTarget.setPointerCapture(e.pointerId); preview(at(e.clientX)); }}
                onPointerMove={(e) => { if (hazeDragRef.current != null) preview(at(e.clientX)); }}
                onPointerUp={() => { if (hazeDragRef.current != null) void commit(hazeDragRef.current); }}>
                <span className="absolute inset-y-0 left-0 rounded bg-[#cfd4dc]" style={{ width: `${pos * 100}%` }} />
                {Object.entries(LEVEL_VISIBILITY_M).map(([w, v]) => (
                  <span key={w} className="absolute inset-y-0 w-px bg-[#6b7183]" style={{ left: `${sliderOf(3 / v) * 100}%` }} title={w} />
                ))}
              </span>
              <span className="w-20 text-[#8d93a5]">{haze ? hazeWords(hazeDrag != null ? sigmaOfSlider(hazeDrag) : live) : "no reading"}</span>
            </span>
          );
        })()}
        <label className={`${pill} cursor-pointer`} title="An aid for aiming, not what the room looks like: draws every beam half-visible whatever the haze.">
          <input type="checkbox" checked={showPaths} onChange={(e) => setShowPaths(e.target.checked)} /> Show beam paths
        </label>
        {(["front", "side", "above"] as const).map((v) => (
          <button key={v} className="rounded border border-[#2c3346] bg-[#1a1e2a] px-2 py-0.5 capitalize" onClick={() => simRef.current?.view(v)}>{v}</button>
        ))}
        <a className={pill} href={`${node}/`} target="_blank" rel="noreferrer">Board</a>
        {embedded ? <a className={pill} href="/lights/room" target="_blank" rel="noreferrer">Full window</a>
          : <a className={pill} href="/settings/lights?tab=3d">Lights setup</a>}
      </header>
      <div className="relative flex min-h-0 flex-1">
        <div ref={hostRef} className="relative min-w-0 flex-1"
          onPointerDown={(e) => { drag.current = { x: e.clientX, y: e.clientY }; (e.target as HTMLElement).setPointerCapture(e.pointerId); }}
          onPointerMove={(e) => { if (!drag.current) return; simRef.current?.orbitBy(e.clientX - drag.current.x, e.clientY - drag.current.y); drag.current = { x: e.clientX, y: e.clientY }; }}
          onPointerUp={() => { drag.current = null; }}
          onWheel={(e) => simRef.current?.zoomBy(e.deltaY)}>
          <canvas ref={canvasRef} className="absolute inset-0 block" />
          <div className="pointer-events-none absolute bottom-1.5 left-2.5 text-xs text-[#8d93a5]">
            drag: orbit · wheel: zoom · the lights read the Art-Net wire, at their makers' brightness; the picture adapts as an eye does · {perf}
          </div>
        </div>
        <aside className={embedded ? "hidden" : "overflow-auto border-l border-[#252a38] bg-[#0b0d13] px-3 py-2"} style={{ width: SIDE_W }}>
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
