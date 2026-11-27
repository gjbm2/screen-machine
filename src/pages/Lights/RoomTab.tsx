// The Light setup page's 3D tab (?tab=3d): the room simulator (pages/Room3D: the room as configured, every light drawn
// from the Art-Net wire) as an editor (owner, 27 Nov: "the whole simulator view should be integrated into the lighting
// set up app section"; "why can't I click a light unit and adjust these various aspects of it -- isn't that the obvious
// affordance? reference any normal 3d editing studio ux").
//   - Click a bar, a light on a bar or a keep-out box in the view to select it; click empty space or press Esc to let go.
//   - Picking from the list (or F) turns the camera to it. The gizmo on it moves it (W), turns it (E) or scales it (R): a bar moves and turns about the vertical, a light
//     turns to where it should point, a keep-out box moves and scales. Letting go saves, and the room is drawn again.
//   - The inspector beside the view has the same thing's numbers for exact entry, and with nothing selected lists
//     everything (the outliner): click a name to select it.
// What is set: a bar's place, height and facing (PUT /rig/space/bars/<id>); a fixed light's tilt and turn (its setup:
// lean_deg, turn_deg); a keep-out region (PUT /rig/space/keep_out/<name>: a box no beam should cross, for the light
// groups ticked or for every light). A moving light steers round a region that is for it; a laser can't be steered,
// so it is checked and the tilt that clears is offered.
// What the lights show while setting up (owner, 27 Nov: "the 3D view in light setup isn't the same as a real time
// view. it should allow user easy to set theme / scene etc."): a preview from show_service's own engine, never sent to
// a light (preview.py: plain light for aiming, a colour, or a lighting scene); "Live now" shows the virtual node's
// wire; "Pop out the live view" opens /lights/room. Every change can be undone (Ctrl+Z) and redone (Ctrl+Shift+Z).
// The chrome is the app's own light theme; the view is the room.
// A planned light (a room device not yet a registered light) is still placed in the older view: "Place a planned light".

import { lazy, Suspense, useCallback, useEffect, useMemo, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Notice, Spinner } from "@/pages/Room/bits";
import Room3DPage from "@/pages/Room3D";
import { resolveRoomUrl } from "@/pages/Room/api";
import { modesOf, type Editable, type EditChange, type GizmoMode } from "@/pages/Room3D/editor";
import { aimDirOf, keepOutOf, type KeepRegion } from "@/pages/Room3D/model";
import { explainLightsError, type LightsApi } from "./api";
import type PlannedView from "./Room3D";

const Planned = lazy(() => import("./Room3D"));
const POLL_MS = 4000;
const BAR_BOX: Vec3 = [0.3, 1.2, 0.32];          // a bar's pick box: front to back, end to end, top to bottom (m)
const LIGHT_BOX: Vec3 = [0.2, 0.2, 0.2];

type Vec3 = [number, number, number];
type PartAim = { part: string; id: string; label: string; at: Vec3 | null; lean_deg: number; turn_deg: number; own: boolean };
type BarBits = { id: string; label: string; at: Vec3; facing_deg: number; front: Vec3; editable: boolean; aims: PartAim[] };
type SpaceBits = ReturnType<typeof keepOutOf> & { size: Vec3; bars: BarBits[] };
/** The older view's own props (the page's space state, the map's things, the lamps' colours): passed straight on. */
type PlannedProps = Parameters<typeof PlannedView>[0];
type Kind = "bar" | "light" | "region";

const isV3 = (v: unknown): v is Vec3 => Array.isArray(v) && v.length === 3 && v.every((x) => typeof x === "number");
const kindOf = (key: string) => key.slice(0, key.indexOf(":")) as Kind;
const idOf = (key: string) => key.slice(key.indexOf(":") + 1);

/** The bars and their aimable parts, from GET /rig/space. */
function barsFrom(s: any): BarBits[] {
  return Object.entries(s?.bars ?? {}).flatMap(([id, b]: [string, any]) =>
    isV3(b?.at) && isV3(b?.front)
      ? [{ id, label: typeof b.label === "string" ? b.label : id, at: b.at, facing_deg: Number(b.facing_deg) || 0, front: b.front,
           editable: b.editable !== false,
           aims: Object.entries(b.parts ?? {}).flatMap(([part, p]: [string, any]) =>
             p?.aim && typeof p.aim.lean_deg === "number"
               ? [{ part, id: String(p.id), label: String(p.label ?? p.id), at: isV3(s?.appliances?.[p.id]?.at) ? s.appliances[p.id].at : null,
                    lean_deg: p.aim.lean_deg, turn_deg: Number(p.aim.turn_deg) || 0, own: p.aim.own === true }]
               : []) }]
      : []);
}

/** A tilt in words: degrees forward from straight up, said the way you would set it on the bracket. */
function tiltWords(lean: number): string {
  if (Math.abs(lean) < 0.05) return "straight up";
  if (lean < 0) return `${(-lean).toFixed(1)}° back from straight up`;
  if (lean <= 45) return `${lean.toFixed(1)}° forward of straight up`;
  if (Math.abs(lean - 90) < 0.05) return "level";
  return lean < 90 ? `${(90 - lean).toFixed(1)}° above level` : `${(lean - 90).toFixed(1)}° below level`;
}
const turnWords = (t: number) => (Math.abs(t) < 0.05 ? "straight ahead" : `${Math.abs(t).toFixed(1)}° to its ${t > 0 ? "right" : "left"}`);

/** One number in the inspector: typed, then kept on Enter or on leaving the box. */
function Num({ label, value, step = 0.05, unit, disabled, onKeep }: {
  label: string; value: number; step?: number; unit?: string; disabled?: boolean; onKeep: (v: number) => void;
}) {
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);
  const keep = () => { const v = Number(text); if (text.trim() !== "" && Number.isFinite(v) && v !== value) onKeep(v); else setText(String(value)); };
  return (
    <label className="grid grid-cols-[1fr_92px_18px] items-center gap-2 py-0.5">
      <span className="text-stone-600">{label}</span>
      <input type="number" step={step} value={text} disabled={disabled} aria-label={label}
        onChange={(e) => setText(e.target.value)} onBlur={keep}
        onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
        className="h-8 w-full rounded border border-stone-300 bg-white px-2 text-right tabular-nums text-stone-900" />
      <span className="text-stone-500">{unit ?? ""}</span>
    </label>
  );
}

const btn = "rounded-lg border border-stone-300 bg-white px-2.5 py-1.5 text-stone-800 hover:bg-stone-50 disabled:opacity-50";
const H = ({ children }: { children: ReactNode }) => <h3 className="mb-1 mt-3 text-[11px] font-semibold uppercase tracking-wide text-stone-500">{children}</h3>;
const MODE_NAMES: Record<GizmoMode, string> = { translate: "Move (W)", rotate: "Turn (E)", scale: "Scale (R)" };

export default function RoomTab({ api, space, things, lamps }: PlannedProps & { api: LightsApi }) {
  const [bits, setBits] = useState<SpaceBits | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [mode, setMode] = useState<GizmoMode>("translate");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [planned, setPlanned] = useState(false);
  const [refresh, setRefresh] = useState(0);                         // bumped after a save: the simulator reads again
  const [frame, setFrame] = useState(0);                             // bumped to turn the camera to the selection
  const [treeOpen, setTreeOpen] = useState(false);                   // the object picker's dropdown
  // what the lights show in the view: a preview (nothing sent to the lights) or the live wire
  const [show, setShow] = useState<"plain" | "colour" | "scene" | "live">("plain");
  const [colour, setColour] = useState("#ff7a2e");
  const [scene, setScene] = useState("");
  const [scenes, setScenes] = useState<{ name: string; label: string }[]>([]);
  const room = useMemo(() => resolveRoomUrl(window.location.search).replace(/\/+$/, ""), []);
  useEffect(() => {
    fetch(`${room}/show/api/v1/lights/library`, { cache: "no-store" }).then((r) => r.json()).then((d: any) => {
      const xs = (d?.items ?? []).filter((x: any) => String(x?.key ?? "").startsWith("light/"))
        .map((x: any) => ({ name: String(x.name), label: String(x.label ?? x.name) }));
      setScenes(xs);
      if (xs.length) setScene((cur) => cur || xs[0].name);
    }).catch(() => setScenes([]));
  }, [room]);
  const feed = useMemo(() => {
    if (show === "live") return { base: "", live: true };
    const q = show === "colour" ? `?what=colour&colour=${encodeURIComponent(colour)}`
      : show === "scene" && scene ? `?what=scene:${encodeURIComponent(scene)}` : "?what=plain";
    return { base: `${room}/show/api/v1/rig/preview`, query: q };
  }, [show, colour, scene, room]);
  // undo and redo: every change is a step that knows how to put things back (owner, 27 Nov: "need undo etc. in case of
  // unintended fiddles")
  type Step = { label: string; undo: () => Promise<unknown>; redo: () => Promise<unknown> };
  const [past, setPast] = useState<Step[]>([]);
  const [future, setFuture] = useState<Step[]>([]);

  const read = useCallback(async () => {
    try {
      const s: any = await api.space();
      setBits({ ...keepOutOf(s), size: isV3(s?.room?.size) ? s.room.size : [6, 6, 3], bars: barsFrom(s) });
    } catch (e) {
      setErr(explainLightsError(e));
    }
  }, [api]);
  useEffect(() => {
    void read();
    const t = window.setInterval(() => void read(), POLL_MS);
    return () => window.clearInterval(t);
  }, [read]);

  /** Save a change, then have this page and the simulator read the room again. */
  const keep = useCallback(async (write: () => Promise<unknown>) => {
    setBusy(true);
    setErr(null);
    try {
      await write();
      await read();
      setRefresh((n) => n + 1);
    } catch (e) {
      setErr(explainLightsError(e));
    } finally {
      setBusy(false);
    }
  }, [read]);

  /** Make a change that can be undone: ``redo`` makes it, ``undo`` puts things back as they were. */
  const act = useCallback(async (label: string, redo: () => Promise<unknown>, undo: () => Promise<unknown>) => {
    await keep(redo);
    setPast((p) => [...p.slice(-49), { label, undo, redo }]);
    setFuture([]);
  }, [keep]);
  const undo = useCallback(() => {
    const st = past[past.length - 1];
    if (!st || busy) return;
    setPast((p) => p.slice(0, -1));
    setFuture((f) => [...f, st]);
    void keep(st.undo);
  }, [past, busy, keep]);
  const redo = useCallback(() => {
    const st = future[future.length - 1];
    if (!st || busy) return;
    setFuture((f) => f.slice(0, -1));
    setPast((p) => [...p, st]);
    void keep(st.redo);
  }, [future, busy, keep]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (!(e.ctrlKey || e.metaKey) || (t && ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName))) return;
      const k = e.key.toLowerCase();
      if (k === "z" && !e.shiftKey) { e.preventDefault(); undo(); }
      else if ((k === "z" && e.shiftKey) || k === "y") { e.preventDefault(); redo(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [undo, redo]);

  // what can be clicked in the view
  const editables = useMemo<Editable[]>(() => {
    if (!bits) return [];
    const out: Editable[] = [];
    for (const b of bits.bars) {
      if (b.editable) out.push({ key: `bar:${b.id}`, kind: "bar", label: b.label, at: b.at, size: BAR_BOX, yaw: (b.facing_deg * Math.PI) / 180 });
      for (const p of b.aims) {
        if (p.at) out.push({ key: `light:${p.id}`, kind: "light", label: p.label, at: p.at, size: LIGHT_BOX, dir: aimDirOf(b.front, p.lean_deg, p.turn_deg), front: b.front });
      }
    }
    for (const r of bits.keepOut) if (r.editable) out.push({ key: `region:${r.name}`, kind: "region", label: r.label, at: r.centre, size: r.size });
    return out;
  }, [bits]);

  const kind = selected ? kindOf(selected) : null, id = selected ? idOf(selected) : null;
  const bar = kind === "bar" ? bits?.bars.find((b) => b.id === id) : undefined;
  const lightBar = kind === "light" ? bits?.bars.find((b) => b.aims.some((p) => p.id === id)) : undefined;
  const light = lightBar?.aims.find((p) => p.id === id);
  const region = kind === "region" ? bits?.keepOut.find((r) => r.name === id) : undefined;
  const check = light ? bits?.lasers[light.id] : undefined;

  const select = useCallback((key: string | null) => {
    setSelected(key);
    if (key) setMode(modesOf(kindOf(key))[0]);
  }, []);
  const putRegion = useCallback((r: KeepRegion, over: Partial<KeepRegion>) => {
    const n = { ...r, ...over };
    return api.putKeepOut(r.name, { centre: n.centre, size: n.size, groups: n.groups, label: n.label });
  }, [api]);
  const changeBar = useCallback((b: BarBits, body: { at?: number[]; facing_deg?: number }, label: string) =>
    act(label, () => api.putBar(b.id, body), () => api.putBar(b.id, { at: b.at, facing_deg: b.facing_deg })), [act, api]);
  const changeLight = useCallback((p: PartAim, body: { lean_deg?: number | null; turn_deg?: number | null }, label: string) =>
    act(label, () => api.setup(p.id, body),
      () => api.setup(p.id, p.own ? { lean_deg: p.lean_deg, turn_deg: p.turn_deg } : { lean_deg: null, turn_deg: null })), [act, api]);
  const changeRegion = useCallback((r: KeepRegion, over: Partial<KeepRegion>, label: string) =>
    act(label, () => putRegion(r, over), () => putRegion(r, {})), [act, putRegion]);
  const onEdit = useCallback((key: string, c: EditChange) => {
    const k = kindOf(key), i = idOf(key);
    if (k === "bar") {
      const b = bits?.bars.find((x) => x.id === i);
      if (b) void changeBar(b, { ...(c.at ? { at: c.at } : {}), ...(c.facing_deg !== undefined ? { facing_deg: c.facing_deg } : {}) },
        c.facing_deg !== undefined && c.facing_deg !== b.facing_deg ? `turn ${b.label}` : `move ${b.label}`);
    } else if (k === "light") {
      const p = bits?.bars.flatMap((b) => b.aims).find((x) => x.id === i);
      if (p) void changeLight(p, { lean_deg: c.lean_deg, turn_deg: c.turn_deg }, `aim ${p.label}`);
    } else {
      const r = bits?.keepOut.find((x) => x.name === i);
      if (r) void changeRegion(r, { centre: c.at ?? r.centre, size: c.size ?? r.size }, `change ${r.label}`);
    }
  }, [bits, changeBar, changeLight, changeRegion]);

  const addRegion = () => {
    if (!bits) return;
    const taken = new Set(bits.keepOut.map((r) => r.name));
    let n = 1;
    while (taken.has(`keep-out-${n}`)) n++;
    const name = `keep-out-${n}`;
    const body = { centre: [bits.size[0] / 2, bits.size[1] / 2, Math.max(0.4, bits.size[2] - 0.6)], size: [0.6, 0.6, 0.6],
      groups: [] as string[], label: `Keep out ${n}` };
    void act(`add ${body.label}`, () => api.putKeepOut(name, body), () => api.removeKeepOut(name)).then(() => select(`region:${name}`));
  };

  // W move, E turn, R scale, Esc let go (as in any 3D studio), while something is selected and no box is being typed in
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!selected || e.metaKey || e.ctrlKey || e.altKey) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT")) return;
      const want = ({ w: "translate", e: "rotate", r: "scale" } as Record<string, GizmoMode>)[e.key.toLowerCase()];
      if (e.key === "Escape") { if (treeOpen) setTreeOpen(false); else setSelected(null); }
      else if ((e.key === "Delete" || e.key === "Backspace") && kindOf(selected) === "region") {
        const r = bits?.keepOut.find((x) => x.name === idOf(selected));
        if (r) { e.preventDefault(); setSelected(null); void act(`remove ${r.label}`, () => api.removeKeepOut(r.name), () => putRegion(r, {})); }
      }
      else if (e.key.toLowerCase() === "f") setFrame((n) => n + 1);
      else if (want && modesOf(kindOf(selected)).includes(want)) setMode(want);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selected, bits, act, api, putRegion, treeOpen]);

  const hasPlanned = Object.values(space.space?.appliances ?? {}).some((a) => !!a.kind);
  if (planned) {
    return (
      <div className="space-y-3">
        <Button variant="outline" onClick={() => setPlanned(false)} className="h-11 rounded-xl bg-white px-4 text-[15px]">‹ Back to the room</Button>
        <Suspense fallback={<p className="flex items-center gap-2 py-3 text-base text-muted-foreground"><Spinner /> Drawing the room…</p>}>
          <Planned api={api} space={space} things={things} lamps={lamps} />
        </Suspense>
      </div>
    );
  }

  const modeRow = (k: Kind) => (
    <div className="mb-2 flex flex-wrap gap-1.5">
      {modesOf(k).map((m) => (
        <button key={m} className={`${btn} ${mode === m ? "border-teal-600 bg-teal-50 text-teal-800" : ""}`} onClick={() => setMode(m)}>{MODE_NAMES[m]}</button>
      ))}
    </div>
  );
  const row = (key: string, label: string, sub?: string) => (
    <button key={key} onClick={() => go(key)} data-testid={`pick-${key}`}
      aria-current={selected === key ? "true" : undefined}
      ref={selected === key ? (el) => el?.scrollIntoView({ block: "nearest" }) : undefined}
      className={`block w-full rounded px-2 py-1 text-left ${selected === key
        ? "bg-teal-50 font-medium text-teal-900 ring-1 ring-teal-300" : "hover:bg-stone-100"}`}>
      {label}{sub ? <span className={selected === key ? "text-teal-700" : "text-stone-500"}> · {sub}</span> : null}
    </button>
  );
  /** A light's name without its bar's ("Bar A par 1" under "Bar A": "par 1"). */
  const short = (p: PartAim, b: BarBits) => {
    const n = p.label.startsWith(b.label + " ") ? p.label.slice(b.label.length + 1) : p.label;
    return n.charAt(0).toUpperCase() + n.slice(1);
  };
  /** Everything in the outliner's order: each bar then its lights, then the keep-out regions. */
  const order = useMemo(() => !bits ? [] : [
    ...bits.bars.flatMap((b) => [...(b.editable ? [`bar:${b.id}`] : []), ...b.aims.map((p) => `light:${p.id}`)]),
    ...bits.keepOut.filter((r) => r.editable).map((r) => `region:${r.name}`)], [bits]);
  const parentOf = (key: string) => {
    if (kindOf(key) !== "light" || !bits) return null;
    const b = bits.bars.find((x) => x.aims.some((p) => p.id === idOf(key)));
    return b && b.editable ? `bar:${b.id}` : null;
  };
  const go = (key: string | null) => { select(key); setTreeOpen(false); if (key) setFrame((n) => n + 1); };
  /** Arrow keys in the panel walk the hierarchy: up / down the next thing, left its parent (or let go), right a
   *  bar's first light. */
  const onPanelKey = (e: ReactKeyboardEvent) => {
    const t = e.target as HTMLElement;
    if (["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName) || !order.length) return;
    const i = selected ? order.indexOf(selected) : -1;
    let next: string | null | undefined;
    if (e.key === "ArrowDown") next = order[Math.min(order.length - 1, i + 1)];
    else if (e.key === "ArrowUp") next = i <= 0 ? order[0] : order[i - 1];
    else if (e.key === "ArrowLeft") next = selected ? parentOf(selected) : undefined;
    else if (e.key === "ArrowRight" && selected && kindOf(selected) === "bar") {
      const b = bits?.bars.find((x) => x.id === idOf(selected));
      next = b?.aims[0] ? `light:${b.aims[0].id}` : undefined;
    } else return;
    e.preventDefault();
    if (next !== undefined) go(next);
  };

  // the outliner: everything in the room that can be edited, always there, the selected thing highlighted
  const outliner = !bits ? null : (
    <div className="overflow-auto" data-testid="outliner">
      <H>Bars</H>
      {bits.bars.length === 0 && <div className="px-2 text-stone-500">No bars in the room model.</div>}
      {bits.bars.map((b) => (
        <div key={b.id}>
          {b.editable ? row(`bar:${b.id}`, b.label, `${b.at[2].toFixed(2)} m up`) : <div className="px-2 py-1">{b.label}<span className="text-stone-500"> · set in the room file</span></div>}
          <div className="ml-3 border-l border-stone-200 pl-1">{b.aims.map((p) => row(`light:${p.id}`, short(p, b)))}</div>
        </div>
      ))}
      <H>Keep-out regions</H>
      {bits.keepOut.length === 0 && <div className="px-2 text-stone-500">None yet.</div>}
      {bits.keepOut.map((r) => (r.editable
        ? row(`region:${r.name}`, r.label, r.groups.length ? r.groups.map((g) => bits.lightGroups[g]?.label ?? g).join(", ") : "every light")
        : <div key={r.name} className="px-2 py-1">{r.label}<span className="text-stone-500"> · set in the room file</span></div>))}
      <button className={`${btn} mt-2`} disabled={busy} onClick={addRegion} data-testid="add-keep-out">+ Add a keep-out region</button>
    </div>
  );

  const panel: ReactNode = !bits ? <p className="py-2 text-stone-500">Reading the room…</p> : (
    <div className="flex h-full flex-col outline-none" data-testid="inspector-body" tabIndex={-1} onKeyDown={onPanelKey}>
      {!selected ? (
        <div className="min-h-0 flex-1 overflow-auto">{outliner}</div>
      ) : (
        // the object picker: one line (where you are, and the previous / next thing); the whole tree drops down from it
        <div className="relative mb-2 flex items-center gap-1" data-testid="picker">
          <button className={`${btn} px-2`} aria-label="The previous thing" title="The previous thing (↑)"
            onClick={() => { const i = order.indexOf(selected); if (i > 0) go(order[i - 1]); }}>‹</button>
          <button className="flex min-w-0 flex-1 items-center justify-between gap-1 rounded-lg border border-stone-300 bg-white px-2 py-1.5 text-left hover:bg-stone-50"
            onClick={() => setTreeOpen((o) => !o)} aria-expanded={treeOpen} data-testid="breadcrumb">
            <span className="truncate">
              {light && lightBar ? <><span className="text-stone-500">{lightBar.label} › </span>{short(light, lightBar)}</>
                : bar ? bar.label : region ? <><span className="text-stone-500">Keep out › </span>{region.label}</> : selected}
            </span>
            <span className="text-stone-500">▾</span>
          </button>
          <button className={`${btn} px-2`} aria-label="The next thing" title="The next thing (↓)"
            onClick={() => { const i = order.indexOf(selected); if (i < order.length - 1) go(order[i + 1]); }}>›</button>
          <button className="ml-1 rounded-lg bg-teal-600 px-2.5 py-1.5 font-medium text-white hover:bg-teal-700"
            onClick={() => setSelected(null)} title="Back to the room (Esc)" data-testid="done">✕ Done</button>
          {treeOpen && (
            <>
              <div className="fixed inset-0 z-10" onClick={() => setTreeOpen(false)} />
              <div className="absolute left-0 right-0 top-full z-20 mt-1 max-h-[55vh] overflow-auto rounded-lg border border-stone-200 bg-white p-1 shadow-lg">
                <button className="block w-full rounded px-2 py-1 text-left text-stone-600 hover:bg-stone-100" onClick={() => { setSelected(null); setTreeOpen(false); }}>
                  Nothing selected (the whole list)
                </button>
                {outliner}
              </div>
            </>
          )}
        </div>
      )}
      <div className={selected ? "min-h-0 flex-1 overflow-auto" : "shrink-0 border-t border-stone-200 pt-2"}>
      {bar && (
        <>
          <div className="text-[15px] font-semibold">{bar.label}</div>
          <div className="mb-2 text-stone-500">A bar on its stand</div>
          {modeRow("bar")}
          <H>Where it stands</H>
          <Num label="East–west" unit="m" value={bar.at[0]} disabled={busy} onKeep={(v) => void changeBar(bar, { at: [v, bar.at[1], bar.at[2]] }, `move ${bar.label}`)} />
          <Num label="North–south" unit="m" value={bar.at[1]} disabled={busy} onKeep={(v) => void changeBar(bar, { at: [bar.at[0], v, bar.at[2]] }, `move ${bar.label}`)} />
          <Num label="Height of the bar" unit="m" value={bar.at[2]} disabled={busy} onKeep={(v) => void changeBar(bar, { at: [bar.at[0], bar.at[1], v] }, `raise ${bar.label}`)} />
          <Num label="Faces (0 east, 90 north)" unit="°" step={1} value={bar.facing_deg} disabled={busy} onKeep={(v) => void changeBar(bar, { facing_deg: v }, `turn ${bar.label}`)} />
          <H>Lights on it</H>
          {bar.aims.map((p) => row(`light:${p.id}`, short(p, bar), tiltWords(p.lean_deg)))}
        </>
      )}
      {light && (
        <>
          <div className="text-[15px] font-semibold">{light.label}</div>
          <div className="mb-2 text-stone-500">A light on {lightBar?.label}: turn it to where the real one points</div>
          {modeRow("light")}
          <div className="flex items-baseline justify-between">
            <H>How it points</H>
            {light.own && <button className="text-[12px] text-teal-700 hover:underline disabled:opacity-50" disabled={busy}
              onClick={() => void changeLight(light, { lean_deg: null, turn_deg: null }, `put ${light.label} back`)}>Reset to how it ships</button>}
          </div>
          <Num label="Tilt from straight up" unit="°" step={0.5} value={light.lean_deg} disabled={busy} onKeep={(v) => void changeLight(light, { lean_deg: v }, `tilt ${light.label}`)} />
          <div className="mb-1 text-right text-stone-500">{tiltWords(light.lean_deg)}</div>
          <Num label="Turn (+ to its right)" unit="°" step={0.5} value={light.turn_deg} disabled={busy} onKeep={(v) => void changeLight(light, { turn_deg: v }, `turn ${light.label}`)} />
          <div className="mb-2 text-right text-stone-500">{turnWords(light.turn_deg)}</div>
          {check && (
            <>
              <H>Its beams and the keep-out regions</H>
              {check.crosses.length === 0 ? <div>Its beams cross no keep-out region.</div> : (
                <div className="text-rose-700">Its beams cross {check.crosses.map((n) => bits.keepOut.find((r) => r.name === n)?.label ?? n).join(" and ")}.</div>
              )}
              {check.crosses.length > 0 && (check.clear_lean_deg === null ? <div>No tilt clears it: move the region or the bar.</div> : (
                <button className={`${btn} mt-1`} disabled={busy} onClick={() => void changeLight(light, { lean_deg: check.clear_lean_deg }, `tilt ${light.label} clear`)}>
                  Tilt it clear ({tiltWords(check.clear_lean_deg)})
                </button>
              ))}
            </>
          )}

        </>
      )}
      {region && (
        <>
          <div className="mb-2 text-stone-500">A keep-out region: no beam should cross it</div>
          {modeRow("region")}
          <label className="block py-0.5">
            <span className="text-stone-600">Name</span>
            <input defaultValue={region.label} key={region.name + region.label} maxLength={40} aria-label="Name"
              onBlur={(e) => { const v = e.target.value.trim(); if (v && v !== region.label) void changeRegion(region, { label: v }, `rename ${region.label}`); }}
              onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
              className="mt-0.5 h-8 w-full rounded border border-stone-300 bg-white px-2 text-stone-900" />
          </label>
          <H>Where its middle is</H>
          {(["East–west", "North–south", "Height"] as const).map((w, i) => (
            <Num key={w} label={w} unit="m" value={region.centre[i]} disabled={busy}
              onKeep={(v) => void changeRegion(region, { centre: region.centre.map((x, k) => (k === i ? v : x)) as Vec3 }, `move ${region.label}`)} />
          ))}
          <H>How big it is</H>
          {(["Wide (east–west)", "Deep (north–south)", "Tall"] as const).map((w, i) => (
            <Num key={w} label={w} unit="m" value={region.size[i]} disabled={busy}
              onKeep={(v) => void changeRegion(region, { size: region.size.map((x, k) => (k === i ? Math.max(0.1, v) : x)) as Vec3 }, `resize ${region.label}`)} />
          ))}
          <H>Which lights keep out (none ticked: every light)</H>
          {Object.entries(bits.lightGroups).filter(([g, e]) => g !== "*" && e.members.length > 0).map(([g, e]) => (
            <label key={g} className="flex items-center gap-2 py-0.5">
              <input type="checkbox" className="h-4 w-4" checked={region.groups.includes(g)} disabled={busy}
                onChange={(ev) => void changeRegion(region, { groups: ev.target.checked ? [...region.groups, g] : region.groups.filter((x) => x !== g) }, `change ${region.label}`)} />
              {e.label}
            </label>
          ))}
          <button className={`${btn} mt-3 text-rose-700`} disabled={busy}
            onClick={() => { const r = region; setSelected(null); void act(`remove ${r.label}`, () => api.removeKeepOut(r.name), () => putRegion(r, {})); }}>Remove this region</button>
        </>
      )}
      {!bar && !light && !region && (
        <>
          <div className="text-stone-600">Click a thing in the room or the list to edit it. Arrow keys step through.</div>
          {Object.values(bits.lasers).some((c) => c.crosses.length > 0) && (
            <div className="mt-3 text-rose-700">A laser's beams cross a keep-out region: select the laser to tilt it clear.</div>
          )}
        </>
      )}
      </div>
      {/* the footer: every change is saved as it is made; undo and redo are here */}
      <div className="mt-2 flex shrink-0 items-center gap-1.5 border-t border-stone-200 pt-2" data-testid="footer">
        <button className={btn} disabled={!past.length || busy} onClick={undo} title={past.length ? `Undo: ${past[past.length - 1].label} (Ctrl+Z)` : "Nothing to undo"} data-testid="undo">↶ Undo</button>
        <button className={btn} disabled={!future.length || busy} onClick={redo} title={future.length ? `Redo: ${future[future.length - 1].label} (Ctrl+Shift+Z)` : "Nothing to redo"} data-testid="redo">↷ Redo</button>
        <span className="min-w-0 truncate text-[12px] text-stone-500" data-testid="last-change">
          {busy ? "Saving…" : past.length ? `Saved: ${past[past.length - 1].label}` : "Changes save as you make them"}
        </span>
      </div>
    </div>
  );

  const sel = "h-8 rounded-lg border border-stone-300 bg-white px-2 text-[13px] text-stone-800";
  const SHOW: [typeof show, string, string][] = [["plain", "Plain", "Every light steady in plain light: for aiming"],
    ["colour", "Colour", "Every light in one colour"], ["scene", "Scene", "A lighting scene from the library"],
    ["live", "Live", "What the room's lights are doing now"]];
  const toolbar = (
    <>
      <span className="text-stone-500">Lights</span>
      <div className="inline-flex overflow-hidden rounded-lg border border-stone-300" role="group" aria-label="What the lights show" data-testid="show-what">
        {SHOW.map(([v, label, title], i) => (
          <button key={v} title={title} aria-pressed={show === v} onClick={() => setShow(v)} data-testid={`show-${v}`}
            className={`h-8 px-3 ${i ? "border-l border-stone-300" : ""} ${show === v ? "bg-teal-600 text-white" : "bg-white hover:bg-stone-50"}`}>{label}</button>
        ))}
      </div>
      {show === "colour" && (
        <input type="color" value={colour} onChange={(e) => setColour(e.target.value)} aria-label="Colour" className="h-8 w-10 rounded-lg border border-stone-300 bg-white" />
      )}
      {show === "scene" && (
        <select value={scene} onChange={(e) => setScene(e.target.value)} className={`${sel} max-w-[240px]`} aria-label="Lighting scene">
          {scenes.map((x) => <option key={x.name} value={x.name}>{x.label}</option>)}
        </select>
      )}
    </>
  );

  return (
    <div className="space-y-3" data-testid="room-tab">
      <Room3DPage embedded refresh={refresh} editables={editables} selected={selected} mode={mode} picked={region?.name ?? null}
        onSelect={(k) => { select(k); if (k) setFrame((n) => n + 1); }} onEdit={onEdit} panel={panel} frame={frame}
        feed={feed} toolbar={toolbar} />
      <p className="text-[15px] text-muted-foreground">
        Click a thing to select it (it comes in front of you), drag its handles to change it (W move, E turn, R scale; hold
        Ctrl to snap), Esc or Done to go back to the room, Ctrl+Z to undo. Moving round: drag to orbit, right-drag to pan,
        the wheel to zoom, double-click a spot to focus there.
      </p>
      {err && <Notice tone="bad">{err}</Notice>}
      {hasPlanned && (
        <Button variant="outline" onClick={() => setPlanned(true)} className="h-11 rounded-xl bg-white px-4 text-[15px]">
          Place a planned light ›
        </Button>
      )}
    </div>
  );
}
