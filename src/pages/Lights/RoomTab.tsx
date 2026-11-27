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
import { settingsUrl } from "@/components/nav/appNav";
import { explainLightsError, type LightsApi } from "./api";
import { nameWords, parseSpace, placeId, snapToWall, type Placeable, type PlacedThing, type RoomSpace } from "./lib";
import { AddPlace, frontMiddle, MOUNTS, RoomForm } from "./RoomMap";
import type PlannedView from "./Room3D";

const Planned = lazy(() => import("./Room3D"));
const POLL_MS = 4000;
const BAR_BOX: Vec3 = [0.2, 1.16, 0.46];         // a bar's pick box: front to back, end to end, top to bottom (m)
const LIGHT_BOX: Vec3 = [0.2, 0.2, 0.2];

type Vec3 = [number, number, number];
type PartAim = { part: string; id: string; label: string; at: Vec3 | null; lean_deg: number; turn_deg: number; own: boolean;
  /** false: a one-axis bracket (a U-yoke): it only tilts, and faces where its bar faces */
  turns: boolean };
/** Every part of a bar, in order along it (left to right as you face it): the aimable ones are in ``aims`` too. */
type BarPartBits = { part: string; id: string; label: string; at: Vec3 | null; along: number; head: boolean; hang: boolean; aimable: boolean };
type BarBits = { id: string; label: string; at: Vec3; facing_deg: number; front: Vec3; editable: boolean; aims: PartAim[]; parts: BarPartBits[] };
type SpaceBits = ReturnType<typeof keepOutOf> & { size: Vec3; bars: BarBits[]; room: RoomSpace; partIds: Set<string>;
  /** What the deployment's own space.yaml sets ("screen:<id>", "place:<name>", ...): the page can't move it. */
  locked: Set<string> };
/** The older view's own props (the page's space state, the map's things, the lamps' colours): passed straight on. */
type PlannedProps = Parameters<typeof PlannedView>[0];
type Kind = "room" | "bar" | "light" | "part" | "region" | "screen" | "thing" | "place" | "area";
/** A part of a bar that is selected but not aimed here (a moving head, a strobe LED): its pick box. */
const HEAD_BOX: Vec3 = [0.22, 0.22, 0.24], LED_BOX: Vec3 = [0.1, 0.1, 0.07];
const THING_BOX: Vec3 = [0.3, 0.3, 0.3];
const r2 = (x: number) => Math.round(x * 100) / 100;
/** What PUT /rig/space/appliances/<id> needs to put a thing back as it was. */
function placedBody(a: PlacedThing): Record<string, unknown> {
  return { at: a.at, ...(a.mount ? { mount: a.mount } : {}),
    ...(a.plane?.normal ? { plane: { normal: a.plane.normal, up: a.plane.up ?? [0, 0, 1], ...(a.plane.size ? { size: a.plane.size } : {}) } } : {}) };
}
const WALL_WORDS: Record<string, string> = { "1,0": "west", "-1,0": "east", "0,1": "south", "0,-1": "north" };
const wallOf = (n: Vec3 | null | undefined) => (n ? WALL_WORDS[`${Math.round(n[0])},${Math.round(n[1])}`] ?? null : null);

const isV3 = (v: unknown): v is Vec3 => Array.isArray(v) && v.length === 3 && v.every((x) => typeof x === "number");
const kindOf = (key: string) => key.slice(0, key.indexOf(":")) as Kind;
/** The editor's kind for a key (the room itself is not a thing in the view). */
const editKind = (key: string) => { const k = kindOf(key); return (k === "room" ? "region" : k) as Exclude<Kind, "room">; };
const idOf = (key: string) => key.slice(key.indexOf(":") + 1);
/** A bar part's key: an aimable light ("light:<id>"), else a part that is only selected ("part:<id>"). */
const partKey = (p: { id: string; aimable: boolean }) => `${p.aimable ? "light" : "part"}:${p.id}`;

/** The bars and their aimable parts, from GET /rig/space. */
function barsFrom(s: any): BarBits[] {
  return Object.entries(s?.bars ?? {}).flatMap(([id, b]: [string, any]) =>
    isV3(b?.at) && isV3(b?.front)
      ? [{ id, label: typeof b.label === "string" ? b.label : id, at: b.at, facing_deg: Number(b.facing_deg) || 0, front: b.front,
           editable: b.editable !== false,
           aims: Object.entries(b.parts ?? {}).flatMap(([part, p]: [string, any]) =>
             p?.aim && typeof p.aim.lean_deg === "number"
               ? [{ part, id: String(p.id), label: String(p.label ?? p.id), at: isV3(s?.appliances?.[p.id]?.at) ? s.appliances[p.id].at : null,
                    lean_deg: p.aim.lean_deg, turn_deg: Number(p.aim.turn_deg) || 0, own: p.aim.own === true, turns: p.turns !== false }]
               : []),
           parts: Object.entries(b.parts ?? {}).map(([part, p]: [string, any]) => {
             const at: Vec3 | null = isV3(s?.appliances?.[p.id]?.at) ? s.appliances[p.id].at : null;
             const ax = [-b.front[1], b.front[0]];           // space.py's: + to the right as you face the bar
             return { part, id: String(p.id), label: String(p.label ?? p.id), at,
                      along: at ? (at[0] - b.at[0]) * ax[0] + (at[1] - b.at[1]) * ax[1] : 0,
                      head: p.head === true, hang: p.hang === true, aimable: !!p.aim && typeof p.aim.lean_deg === "number" };
           }).sort((u, v) => u.along - v.along) }]
      : []);
}


// A light's aim as two separate angles (owner, 27 Nov: "expose angle and elevation as separate controls"):
//   Direction: degrees to the right (+) or left (-) of its bar's front, as you stand behind the bar looking out;
//   Elevation: degrees above level (+90 straight up, 0 level, - below level).
// Stored as the setup's lean_deg (forward from straight up) and turn_deg (anticlockwise seen from above): a light
// pointing more than 90° to a side leans back over its bar instead of being turned round.
function aimOf(p: { lean_deg: number; turn_deg: number }): { direction: number; elevation: number } {
  const r = (x: number) => Math.round(x * 2) / 2 || 0;
  if (p.lean_deg >= 0) return { direction: r(-p.turn_deg), elevation: r(90 - p.lean_deg) };
  let d = -p.turn_deg + 180;
  d = ((d + 180) % 360 + 360) % 360 - 180;
  return { direction: r(d), elevation: r(90 + p.lean_deg) };
}
function leanTurnOf(direction: number, elevation: number): { lean_deg: number; turn_deg: number } {
  let rel = -direction;
  rel = ((rel + 180) % 360 + 360) % 360 - 180;
  const e = Math.max(-90, Math.min(90, elevation));
  if (Math.abs(rel) <= 90) return { lean_deg: 90 - e, turn_deg: rel };
  return { lean_deg: -(90 - e), turn_deg: rel - Math.sign(rel) * 180 };
}
const directionWords = (d: number) => (Math.abs(d) < 0.25 ? "straight ahead, the way the bar faces" : `${Math.abs(d)}° to the ${d > 0 ? "right" : "left"} of the way the bar faces`);
const elevationWords = (e: number) => (Math.abs(e - 90) < 0.25 ? "straight up" : Math.abs(e) < 0.25 ? "level"
  : e > 0 ? `${e}° above level (${90 - e}° from straight up)` : `${-e}° below level`);

/** One angle: a slider for the feel of it, the number for exactness; kept when the slider is let go or the number
 *  is entered. */
function Angle({ label, value, min, max, words, disabled, onKeep, testid }: {
  label: string; value: number; min: number; max: number; words: string; disabled?: boolean; onKeep: (v: number) => void; testid?: string;
}) {
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  const keep = (x: number) => { if (x !== value) onKeep(x); };
  return (
    <div className="py-1" data-testid={testid}>
      <Num label={label} unit="°" step={0.5} value={value} disabled={disabled} onKeep={(x) => onKeep(Math.max(min, Math.min(max, x)))} />
      <input type="range" min={min} max={max} step={0.5} value={v} disabled={disabled} aria-label={`${label} slider`}
        onChange={(e) => setV(Number(e.target.value))}
        onPointerUp={() => keep(v)} onKeyUp={() => keep(v)}
        className="w-full accent-teal-600" />
      <div className="flex justify-between text-[11px] text-stone-400"><span>{min}°</span><span>0°</span><span>{max}°</span></div>
      <div className="text-stone-500">{words}</div>
    </div>
  );
}

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

/** A thing's name, at the top of its panel: click it to rename it, Enter (or leaving the box) keeps it, Esc puts
 *  it back (owner, 27 Nov: "need to be able to rename objects"). */
function NameField({ value, disabled, onKeep }: { value: string; disabled?: boolean; onKeep: (v: string) => void }) {
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);
  const keep = () => { const v = text.trim(); if (v && v !== value) onKeep(v.slice(0, 60)); else setText(value); };
  return (
    <input value={text} disabled={disabled} aria-label="Name" title="Click to rename" data-testid="name" maxLength={60}
      onChange={(e) => setText(e.target.value)} onBlur={keep}
      onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); if (e.key === "Escape") { e.stopPropagation(); setText(value); (e.target as HTMLInputElement).blur(); } }}
      className="-mx-1 w-full rounded border border-transparent bg-transparent px-1 py-0.5 text-[15px] font-semibold text-stone-900 hover:border-stone-300 focus:border-teal-500 focus:bg-white focus:outline-none" />
  );
}

/** A compact slider for the preview's music: it moves freely and the preview follows when it is let go. */
/** The preview's energy and tempo folded into one button (a narrow header): the sliders open under it. */
function PreviewFeel({ energy, bpm, setEnergy, setBpm }: {
  energy: number; bpm: number; setEnergy: (v: number) => void; setBpm: (v: number) => void;
}) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    const close = (e: PointerEvent) => { if (!(e.target as Element | null)?.closest?.("[data-feel]")) setOpen(false); };
    window.addEventListener("pointerdown", close);
    return () => window.removeEventListener("pointerdown", close);
  }, [open]);
  return (
    <div className="relative shrink-0" data-feel>
      <button className="h-8 whitespace-nowrap rounded-lg border border-stone-300 bg-white px-2.5 tabular-nums hover:bg-stone-50" aria-expanded={open}
        title="The preview's energy and tempo" data-testid="preview-feel" onClick={() => setOpen((o) => !o)}>
        <span className="hidden [@container(min-width:860px)]:inline">{Math.round(energy * 100)}% · {Math.round(bpm)} bpm </span>
        <span className="[@container(min-width:860px)]:hidden">♪ </span>▾
      </button>
      {open && (
        <div className="absolute left-0 top-full z-30 mt-1 space-y-2 rounded-lg border border-stone-200 bg-white p-3 shadow-lg">
          <PreviewKnob label="Energy" value={energy} min={0} max={1} step={0.05} show={(v) => `${Math.round(v * 100)}%`} onCommit={setEnergy} />
          <PreviewKnob label="Tempo" value={bpm} min={60} max={180} step={2} show={(v) => `${Math.round(v)} bpm`} onCommit={setBpm} />
        </div>
      )}
    </div>
  );
}

function PreviewKnob({ label, value, min, max, step, show, onCommit }: {
  label: string; value: number; min: number; max: number; step: number; show: (v: number) => string; onCommit: (v: number) => void;
}) {
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  return (
    <label className="flex shrink-0 items-center gap-1.5 text-stone-600" title={`The preview's ${label.toLowerCase()}`}>
      <span className="w-12">{label}</span>
      <input type="range" min={min} max={max} step={step} value={v} aria-label={label} className="w-24 accent-teal-600"
        onChange={(e) => setV(Number(e.target.value))} onPointerUp={() => onCommit(v)} onKeyUp={() => onCommit(v)} />
      <span className="w-16 whitespace-nowrap tabular-nums text-stone-800">{show(v)}</span>
    </label>
  );
}

const btn = "rounded-lg border border-stone-300 bg-white px-2.5 py-1.5 text-stone-800 hover:bg-stone-50 disabled:opacity-50";
const H = ({ children }: { children: ReactNode }) => <h3 className="mb-1 mt-3 text-[11px] font-semibold uppercase tracking-wide text-stone-500">{children}</h3>;
const MODE_NAMES: Record<GizmoMode, string> = { translate: "Move (W)", rotate: "Turn (E)", scale: "Scale (R)" };

export default function RoomTab({ api, space, things, lamps, selectId = null, plan = false, onSettings }: PlannedProps & {
  api: LightsApi;
  /** Open a light's (or screen's) full settings: the Lights page's own sheet. */
  onSettings?: (id: string) => void;
  /** A light or screen to select on arrival ("Show it on the map"). */
  selectId?: string | null;
  /** Open in the plan view (the old Room map's links). */
  plan?: boolean;
}) {
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
  const [energy, setEnergy] = useState(0.6);                        // the preview's music: how hard it drives
  const [bpm, setBpm] = useState(120);                               // ... and its tempo
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
  // The room as a mode leaves it (owner, 27 Nov: "why are house lights up in all of them? ... surely default in a live
  // mode is that they would be off"): the lights a mode sets itself (light.<id> in modes.json: the house lights) show
  // as the chosen mode sets them. Default: the mode the room is in when that is a show mode, else the first mode that
  // turns lights off for a show; then the last one picked here.
  const [modes, setModes] = useState<{ id: string; label: string; lights: Record<string, number> }[]>([]);
  const [asMode, setAsMode] = useState<string>(() => { try { return localStorage.getItem("room3d.previewMode") ?? ""; } catch { return ""; } });
  useEffect(() => {
    let dead = false;
    void (async () => {
      try {
        const d: any = await (await fetch(`${room}/api/v1/modes`, { cache: "no-store" })).json();
        const st: any = await (await fetch(`${room}/api/v1/status`, { cache: "no-store" })).json().catch(() => ({}));
        const ms = Object.entries(d?.modes ?? {}).map(([id, m]: [string, any]) => ({ id, label: String(m?.label ?? id),
          lights: Object.fromEntries(Object.entries(m?.state?.light ?? {}).map(([lid, v]: [string, any]) =>
            [lid, v === "off" ? 0 : v === "on" ? 1 : typeof v?.bri === "number" ? Math.max(0, Math.min(1, v.bri / 100)) : 1])) }));
        if (dead) return;
        setModes(ms);
        const rs = Array.isArray(st?.rooms) ? st.rooms : [];
        if (rs.length === 1) setRoomName(String(rs[0]?.label ?? nameWords(String(rs[0]?.id ?? "room"))));
        const darkens = (m?: { lights: Record<string, number> }) => !!m && Object.values(m.lights).some((x) => x === 0);
        const cur = ms.find((m) => m.id === st?.mode?.current);
        setAsMode((was) => (was && ms.some((m) => m.id === was) ? was : darkens(cur) ? cur!.id : ms.find(darkens)?.id ?? cur?.id ?? ""));
      } catch { if (!dead) setModes([]); }
    })();
    return () => { dead = true; };
  }, [room]);
  const feed = useMemo(() => {
    if (show === "live") return { base: "", live: true };
    const lights = modes.find((m) => m.id === asMode)?.lights ?? {};
    const sets = Object.entries(lights).map(([id, v]) => `${id}:${v}`).join(",");
    const q = (show === "colour" ? `?what=colour&colour=${encodeURIComponent(colour)}`
      : show === "scene" && scene ? `?what=scene:${encodeURIComponent(scene)}&energy=${energy}&bpm=${bpm}` : "?what=plain")
      + (sets ? `&set=${encodeURIComponent(sets)}` : "");
    return { base: `${room}/show/api/v1/rig/preview`, query: q };
  }, [show, colour, scene, room, energy, bpm, modes, asMode]);
  // undo and redo: every change is a step that knows how to put things back (owner, 27 Nov: "need undo etc. in case of
  // unintended fiddles")
  type Step = { label: string; undo: () => Promise<unknown>; redo: () => Promise<unknown> };
  const [past, setPast] = useState<Step[]>([]);
  const [future, setFuture] = useState<Step[]>([]);

  const read = useCallback(async () => {
    try {
      const s: any = await api.space();
      const partIds = new Set<string>(Object.values(s?.bars ?? {}).flatMap((b: any) => Object.values(b?.parts ?? {}).map((p: any) => String(p?.id ?? ""))));
      const locked = new Set<string>([
        ...Object.entries(s?.appliances ?? {}).filter(([, a]: [string, any]) => a?.editable === false).flatMap(([id]) => [`screen:${id}`, `thing:${id}`]),
        ...Object.entries(s?.points ?? {}).filter(([, p]: [string, any]) => p?.from === "hand").map(([n]) => `place:${n}`),
        ...Object.entries(s?.regions ?? {}).filter(([, r]: [string, any]) => r?.editable === false || r?.from === "hand").map(([n]) => `area:${n}`)]);
      setBits({ ...keepOutOf(s), size: isV3(s?.room?.size) ? s.room.size : [6, 6, 3], bars: barsFrom(s), room: parseSpace(s), partIds, locked });
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

  // the room's other things (the old Room map's): every light and screen the rig knows that is not a part of a bar,
  // and any planned thing placed in the room; which of them are in the room and which are not yet
  const { placedThings, unplacedThings } = useMemo(() => {
    if (!bits) return { placedThings: [] as Placeable[], unplacedThings: [] as Placeable[] };
    const mine = things.filter((t) => !bits.partIds.has(t.id));
    const known = new Set(mine.map((t) => t.id));
    const extra: Placeable[] = Object.entries(bits.room.appliances)
      .filter(([id, a]) => a.at && !known.has(id) && !bits.partIds.has(id) && !a.kind)
      .map(([id, a]) => ({ id, label: a.label ?? nameWords(id), kind: a.plane ? "screen" : "lamp", zone: null, screen: null }));
    const all = [...mine, ...extra];
    return { placedThings: all.filter((t) => bits.room.appliances[t.id]?.at), unplacedThings: all.filter((t) => !bits.room.appliances[t.id]?.at) };
  }, [bits, things]);

  // what can be clicked in the view
  const editables = useMemo<Editable[]>(() => {
    if (!bits) return [];
    const out: Editable[] = [];
    for (const b of bits.bars) {
      if (b.editable) out.push({ key: `bar:${b.id}`, kind: "bar", label: b.label, at: b.at, size: BAR_BOX, yaw: (b.facing_deg * Math.PI) / 180 });
      for (const p of b.aims) {
        if (p.at) out.push({ key: `light:${p.id}`, kind: "light", label: p.label, at: p.at, size: LIGHT_BOX, dir: aimDirOf(b.front, p.lean_deg, p.turn_deg), front: b.front,
          tiltOnly: !p.turns, parent: b.editable ? `bar:${b.id}` : undefined });
      }
      // its other parts (the moving heads, the strobe LEDs): selected to see what they are, not aimed here
      for (const p of b.parts) {
        if (p.aimable || !p.at) continue;
        const led = !p.head;
        const at: Vec3 = led ? [p.at[0] + b.front[0] * 0.03, p.at[1] + b.front[1] * 0.03, p.at[2]] : p.at;
        out.push({ key: `part:${p.id}`, kind: "part", label: p.label, at, size: led ? LED_BOX : HEAD_BOX,
          yaw: (b.facing_deg * Math.PI) / 180, parent: b.editable ? `bar:${b.id}` : undefined });
      }
    }
    for (const r of bits.keepOut) if (r.editable) out.push({ key: `region:${r.name}`, kind: "region", label: r.label, at: r.centre, size: r.size });
    // the rest of the room (what the Room map placed): screens on walls, lights not on a bar, named places, floor areas
    for (const t of placedThings) {
      const a = bits.room.appliances[t.id];
      if (!a?.at) continue;
      if (t.kind === "screen") {
        const n = (a.plane?.normal ?? [0, -1, 0]) as Vec3, sz = a.plane?.size ?? [1, 0.6];
        out.push({ key: `screen:${t.id}`, kind: "screen", label: t.label, at: a.at, size: [0.08, sz[0], sz[1]], yaw: Math.atan2(n[1], n[0]), normal: n });
      } else out.push({ key: `thing:${t.id}`, kind: "thing", label: t.label, at: a.at, size: THING_BOX });
    }
    for (const p of bits.room.points) out.push({ key: `place:${p.name}`, kind: "place", label: p.label, at: p.at, size: [0.3, 0.3, 0.3] });
    for (const a of bits.room.regions) {
      if (a.surface === "floor") out.push({ key: `area:${a.name}`, kind: "area", label: a.label, at: [a.centre[0], a.centre[1], 0.02], size: [a.size[0], a.size[1], 0.04] });
    }
    return out.filter((e) => !bits.locked.has(e.key));                 // set in the room file: not moved here
  }, [bits, placedThings]);

  const kind = selected ? kindOf(selected) : null, id = selected ? idOf(selected) : null;
  const bar = kind === "bar" ? bits?.bars.find((b) => b.id === id) : undefined;
  const lightBar = kind === "light" || kind === "part" ? bits?.bars.find((b) => b.parts.some((p) => p.id === id)) : undefined;
  const part = kind === "part" ? lightBar?.parts.find((p) => p.id === id) : undefined;
  const roomSel = kind === "room";
  const light = lightBar?.aims.find((p) => p.id === id);
  const region = kind === "region" ? bits?.keepOut.find((r) => r.name === id) : undefined;
  const check = light ? bits?.lasers[light.id] : undefined;
  const thing = (kind === "screen" || kind === "thing") ? placedThings.find((t) => t.id === id) : undefined;
  const thingAt = thing ? bits?.room.appliances[thing.id] : undefined;
  const place = kind === "place" ? bits?.room.points.find((p) => p.name === id) : undefined;
  const area = kind === "area" ? bits?.room.regions.find((a) => a.name === id) : undefined;
  const [roomName, setRoomName] = useState("The room");     // room_service's room (one per deployment here)
  // arriving from "Show it on the map": select it once the room is read
  const [arrived, setArrived] = useState(false);
  useEffect(() => {
    if (arrived || !selectId || !bits) return;
    const key = [`screen:${selectId}`, `thing:${selectId}`, `light:${selectId}`].find((k) => editables.some((e) => e.key === k));
    setArrived(true);
    if (key) { setSelected(key); setFrame((n) => n + 1); }
  }, [arrived, selectId, bits, editables]);

  const select = useCallback((key: string | null) => {
    setSelected(key);
    if (key) setMode(modesOf(editKind(key))[0]);
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
  /** Rename a light, screen or part: its setup's label (the file's stays as it was); undo puts the old name back. */
  const renameLight = (id: string, was: string, to: string) =>
    act(`rename ${was}`, () => api.setup(id, { label: to }), () => api.setup(id, { label: was }));
  /** A light or screen's "Settings ›": its full settings on the Lights page's sheet. */
  const settingsBtn = (id: string) => onSettings ? (
    <button className={`${btn} mt-3`} onClick={() => onSettings(id)} data-testid="settings">Settings ›</button>
  ) : null;

  /** A screen or light not on a bar: placed anew, put back as it was on undo (or taken out, if it was not in). */
  const moveThing = useCallback((id: string, body: Record<string, unknown>, was: PlacedThing | undefined, label: string) =>
    act(label, () => api.place(id, body), () => (was?.at ? api.place(id, placedBody(was)) : api.unplace(id))), [act, api]);
  type AreaT = RoomSpace["regions"][number];
  const changeArea = useCallback((a: AreaT, over: Partial<AreaT>, label: string) => {
    const n = { ...a, ...over };
    return act(label, () => api.setArea(a.name, { surface: n.surface, centre: n.centre, size: n.size, label: n.label }),
      () => api.setArea(a.name, { surface: a.surface, centre: a.centre, size: a.size, label: a.label }));
  }, [act, api]);
  /** Bring a thing into the room: the middle (a screen on the middle of the front wall), to be dragged where it is. */
  const putInRoom = (t: Placeable) => {
    if (!bits) return;
    const [X, Y, Z] = bits.size;
    let body: Record<string, unknown>;
    if (t.kind === "screen") {
      const w = snapToWall(bits.size, ...frontMiddle(bits.size, bits.room.front));
      body = { at: [w.x, w.y, r2(Z / 2)], plane: { normal: w.normal, up: [0, 0, 1] } };
    } else body = { at: [r2(X / 2), r2(Y / 2), t.kind === "mover" ? Z : r2(Z / 2)], ...(t.kind === "mover" ? { mount: "ceiling" } : {}) };
    void act(`put ${t.label} in the room`, () => api.place(t.id, body), () => api.unplace(t.id))
      .then(() => { setSelected(`${t.kind === "screen" ? "screen" : "thing"}:${t.id}`); setFrame((n) => n + 1); });
  };
  const addPlace = (label: string) => {
    if (!bits) return;
    const name = placeId(label);
    if (!name) return;
    if (bits.room.points.some((p) => p.name === name)) { setSelected(`place:${name}`); return; }
    const [X, Y] = bits.size;
    void act(`add ${label.trim()}`, () => api.setPlace(name, { at: [r2(X / 2), r2(Y / 2), 0], label: label.trim() }), () => api.removePlace(name))
      .then(() => setSelected(`place:${name}`));
  };
  const onEdit = useCallback((key: string, c: EditChange) => {
    const k = kindOf(key), i = idOf(key);
    if (k === "bar") {
      const b = bits?.bars.find((x) => x.id === i);
      if (b) void changeBar(b, { ...(c.at ? { at: c.at } : {}), ...(c.facing_deg !== undefined ? { facing_deg: c.facing_deg } : {}) },
        c.facing_deg !== undefined && c.facing_deg !== b.facing_deg ? `turn ${b.label}` : `move ${b.label}`);
    } else if (k === "light") {
      const p = bits?.bars.flatMap((b) => b.aims).find((x) => x.id === i);
      if (p) void changeLight(p, { lean_deg: c.lean_deg, turn_deg: c.turn_deg }, `aim ${p.label}`);
    } else if (k === "screen" || k === "thing") {
      const a = bits?.room.appliances[i], t = placedThings.find((x) => x.id === i);
      if (!a || !c.at) return;
      const body: Record<string, unknown> = { at: c.at };
      if (c.normal) body.plane = { normal: c.normal, up: [0, 0, 1], ...(a.plane?.size ? { size: a.plane.size } : {}) };
      void moveThing(i, body, a, `move ${t?.label ?? i}`);
    } else if (k === "place") {
      const p = bits?.room.points.find((x) => x.name === i);
      if (p && c.at) void act(`move ${p.label}`, () => api.setPlace(p.name, { at: c.at!, label: p.label }), () => api.setPlace(p.name, { at: p.at, label: p.label }));
    } else if (k === "area") {
      const a = bits?.room.regions.find((x) => x.name === i);
      if (a && c.at) void changeArea(a, { centre: [c.at[0], c.at[1], a.centre[2]] }, `move ${a.label}`);
    } else {
      const r = bits?.keepOut.find((x) => x.name === i);
      if (r) void changeRegion(r, { centre: c.at ?? r.centre, size: c.size ?? r.size }, `change ${r.label}`);
    }
  }, [bits, changeBar, changeLight, changeRegion, placedThings, act, api, moveThing, changeArea]);

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
      if (e.key === "Escape") {                    // up one level: a light to its bar, anything else to the room
        if (treeOpen) { setTreeOpen(false); return; }
        const up = kindOf(selected) === "light" || kindOf(selected) === "part"
          ? bits?.bars.find((b) => b.editable && b.parts.some((p) => p.id === idOf(selected))) : undefined;
        setSelected(up ? `bar:${up.id}` : null);
      }
      else if ((e.key === "Delete" || e.key === "Backspace") && kindOf(selected) === "region") {
        const r = bits?.keepOut.find((x) => x.name === idOf(selected));
        if (r) { e.preventDefault(); setSelected(null); void act(`remove ${r.label}`, () => api.removeKeepOut(r.name), () => putRegion(r, {})); }
      }
      else if (e.key.toLowerCase() === "f") setFrame((n) => n + 1);
      else if (want && modesOf(editKind(selected)).includes(want)) setMode(want);
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

  const modeRow = (k: Exclude<Kind, "room">) => (
    <div className="mb-2 flex flex-wrap gap-1.5">
      {modesOf(k).map((m) => (
        <button key={m} className={`${btn} ${mode === m ? "border-teal-600 bg-teal-50 text-teal-800" : ""}`} onClick={() => setMode(m)}>{MODE_NAMES[m]}</button>
      ))}
    </div>
  );
  const row = (key: string, label: string, sub?: string) => bits?.locked.has(key) ? (
    <div key={key} className="px-2 py-1 text-stone-500" title="Set in the deployment's space.yaml: change it there">{label} · set in the room file</div>
  ) : (
    <button key={key} onClick={() => go(key)} data-testid={`pick-${key}`}
      aria-current={selected === key ? "true" : undefined}
      ref={selected === key ? (el) => el?.scrollIntoView({ block: "nearest" }) : undefined}
      className={`block w-full rounded px-2 py-1 text-left ${selected === key
        ? "bg-teal-50 font-medium text-teal-900 ring-1 ring-teal-300" : "hover:bg-stone-100"}`}>
      {label}{sub ? <span className={selected === key ? "text-teal-700" : "text-stone-500"}> · {sub}</span> : null}
    </button>
  );
  /** A light's name without its bar's ("Bar A par 1" under "Bar A": "par 1"). */
  const short = (p: { label: string }, b: BarBits) => {
    const n = p.label.startsWith(b.label + " ") ? p.label.slice(b.label.length + 1) : p.label;
    return n.charAt(0).toUpperCase() + n.slice(1);
  };
  /** Everything in the outliner's order: each bar then its lights, then the keep-out regions. */
  const order: string[] = !bits ? [] : [
    ...bits.bars.flatMap((b) => [...(b.editable ? [`bar:${b.id}`] : []), ...b.parts.filter((p) => p.at).map((p) => partKey(p))]),
    ...placedThings.filter((t) => t.kind === "screen").map((t) => `screen:${t.id}`),
    ...placedThings.filter((t) => t.kind !== "screen").map((t) => `thing:${t.id}`),
    ...bits.room.points.map((p) => `place:${p.name}`),
    ...bits.room.regions.filter((a) => a.surface === "floor").map((a) => `area:${a.name}`),
    ...bits.keepOut.filter((r) => r.editable).map((r) => `region:${r.name}`)];
  const parentOf = (key: string) => {
    if ((kindOf(key) !== "light" && kindOf(key) !== "part") || !bits) return null;
    const b = bits.bars.find((x) => x.parts.some((p) => p.id === idOf(key)));
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
      const first = b?.parts.find((p) => p.at);
      next = first ? partKey(first) : undefined;
    } else return;
    e.preventDefault();
    if (next !== undefined) go(next);
  };

  // the outliner: everything in the room that can be edited, always there, the selected thing highlighted
  const outliner = !bits ? null : (
    <div className="overflow-auto" data-testid="outliner">
      {row("room:main", roomName, `${bits.size[0]} × ${bits.size[1]} m, ${bits.size[2]} m high · faces ${bits.room.front}`)}
      <div className="ml-3 border-l border-stone-200 pl-1">
      <H>Bars</H>
      {bits.bars.length === 0 && <div className="px-2 text-stone-500">No bars in the room model.</div>}
      {bits.bars.map((b) => (
        <div key={b.id}>
          {b.editable ? row(`bar:${b.id}`, b.label, `${b.at[2].toFixed(2)} m up`) : <div className="px-2 py-1">{b.label}<span className="text-stone-500"> · set in the room file</span></div>}
          <div className="ml-3 border-l border-stone-200 pl-1">{b.parts.filter((p) => p.at).map((p) => row(partKey(p), short(p, b)))}</div>
        </div>
      ))}
      {placedThings.some((t) => t.kind === "screen") && <H>Screens</H>}
      {placedThings.filter((t) => t.kind === "screen").map((t) => row(`screen:${t.id}`, t.label, wallOf(bits.room.appliances[t.id]?.plane?.normal) ? `${wallOf(bits.room.appliances[t.id]?.plane?.normal)} wall` : undefined))}
      {placedThings.some((t) => t.kind !== "screen") && <H>Other lights</H>}
      {placedThings.filter((t) => t.kind !== "screen").map((t) => row(`thing:${t.id}`, t.label, t.kind === "mover" ? "moving light" : undefined))}
      <H>Places</H>
      {bits.room.points.length === 0 && <div className="px-2 text-stone-500">None yet.</div>}
      {bits.room.points.map((p) => row(`place:${p.name}`, p.label))}
      {bits.room.regions.some((a) => a.surface === "floor") && <H>Floor areas</H>}
      {bits.room.regions.filter((a) => a.surface === "floor").map((a) => row(`area:${a.name}`, a.label, `${a.size[0]} × ${a.size[1]} m`))}
      <H>Keep-out regions</H>
      {bits.keepOut.length === 0 && <div className="px-2 text-stone-500">None yet.</div>}
      {bits.keepOut.map((r) => (r.editable
        ? row(`region:${r.name}`, r.label, r.groups.length ? r.groups.map((g) => bits.lightGroups[g]?.label ?? g).join(", ") : "every light")
        : <div key={r.name} className="px-2 py-1">{r.label}<span className="text-stone-500"> · set in the room file</span></div>))}
      <button className={`${btn} mt-2`} disabled={busy} onClick={addRegion} data-testid="add-keep-out">+ Add a keep-out region</button>
      {unplacedThings.length > 0 && (
        <>
          <H>Not in the room yet</H>
          <div className="mb-1 text-stone-500">Click one: it goes in the middle (a screen on the front wall), then drag it where it is.</div>
          <div className="flex flex-wrap gap-1.5" data-testid="unplaced">
            {unplacedThings.map((t) => (
              <button key={t.id} className={btn} disabled={busy} onClick={() => putInRoom(t)}>+ {t.label}</button>
            ))}
          </div>
        </>
      )}
      <div className="mt-3 [&_h3]:text-[11px] [&_h3]:uppercase [&_h3]:tracking-wide [&_h3]:text-stone-500 [&_p]:text-[12px]" data-testid="add-place">
        <AddPlace onAdd={addPlace} />
      </div>
      </div>
    </div>
  );

  const panel: ReactNode = !bits ? <p className="py-2 text-stone-500">Reading the room…</p> : (
    <div className="flex h-full flex-col outline-none" data-testid="inspector-body" tabIndex={-1} onKeyDown={onPanelKey}>
      {!selected ? (
        <div className="min-h-0 flex-1 overflow-auto">{outliner}</div>
      ) : (
        // the object picker: one line (where you are, and the previous / next thing); the whole tree drops down from it
        <div className="relative mb-2 flex items-center gap-1" data-testid="picker">
          <button className="mr-1 rounded-lg bg-teal-600 px-2.5 py-1.5 font-medium text-white hover:bg-teal-700"
            onClick={() => setSelected(null)} title="Back to the whole room (Esc)" data-testid="done">← Room</button>
          <button className={`${btn} px-2`} aria-label="The previous thing" title="The previous thing (↑)"
            onClick={() => { const i = order.indexOf(selected); if (i > 0) go(order[i - 1]); }}>‹</button>
          <button className="flex min-w-0 flex-1 items-center justify-between gap-1 rounded-lg border border-stone-300 bg-white px-2 py-1.5 text-left hover:bg-stone-50"
            onClick={() => setTreeOpen((o) => !o)} aria-expanded={treeOpen} data-testid="breadcrumb">
            <span className="truncate">
              {(light || part) && lightBar ? <><span className="text-stone-500">{lightBar.label} › </span>{short((light ?? part)!, lightBar)}</>
                : roomSel ? roomName : bar ? bar.label : region ? <><span className="text-stone-500">Keep out › </span>{region.label}</>
                : thing ? <><span className="text-stone-500">{thing.kind === "screen" ? "Screens" : "Lights"} › </span>{thing.label}</>
                : place ? <><span className="text-stone-500">Places › </span>{place.label}</>
                : area ? <><span className="text-stone-500">Floor areas › </span>{area.label}</> : selected}
            </span>
            <span className="text-stone-500">▾</span>
          </button>
          <button className={`${btn} px-2`} aria-label="The next thing" title="The next thing (↓)"
            onClick={() => { const i = order.indexOf(selected); if (i < order.length - 1) go(order[i + 1]); }}>›</button>

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
          <NameField value={bar.label} disabled={busy} onKeep={(v) => void act(`rename ${bar.label}`, () => api.putBar(bar.id, { label: v }), () => api.putBar(bar.id, { label: bar.label }))} />
          <div className="mb-2 text-stone-500">A bar on its stand</div>
          {modeRow("bar")}
          <H>Where it stands</H>
          <Num label="East–west" unit="m" value={bar.at[0]} disabled={busy} onKeep={(v) => void changeBar(bar, { at: [v, bar.at[1], bar.at[2]] }, `move ${bar.label}`)} />
          <Num label="North–south" unit="m" value={bar.at[1]} disabled={busy} onKeep={(v) => void changeBar(bar, { at: [bar.at[0], v, bar.at[2]] }, `move ${bar.label}`)} />
          <Num label="Height of the bar" unit="m" value={bar.at[2]} disabled={busy} onKeep={(v) => void changeBar(bar, { at: [bar.at[0], bar.at[1], v] }, `raise ${bar.label}`)} />
          <Num label="Faces (0 east, 90 north)" unit="°" step={1} value={bar.facing_deg} disabled={busy} onKeep={(v) => void changeBar(bar, { facing_deg: v }, `turn ${bar.label}`)} />
          <H>Lights on it</H>
          {bar.parts.filter((p) => p.at).map((p) => {
            const a = bar.aims.find((x) => x.id === p.id);
            return row(partKey(p), short(p, bar), a ? elevationWords(aimOf(a).elevation) : p.head ? "moving head" : "strobe LED");
          })}
        </>
      )}
      {light && (
        <>
          <NameField value={light.label} disabled={busy} onKeep={(v) => void renameLight(light.id, light.label, v)} />
          <div className="mb-2 text-stone-500">A light on {lightBar?.label}: turn it to where the real one points</div>
          {modeRow("light")}
          <div className="flex items-baseline justify-between">
            <H>How it points</H>
            {light.own && <button className="text-[12px] text-teal-700 hover:underline disabled:opacity-50" disabled={busy}
              onClick={() => void changeLight(light, { lean_deg: null, turn_deg: null }, `put ${light.label} back`)}>Reset to how it ships</button>}
          </div>
          {(() => {
            const a = aimOf(light);
            return (
              <>
                {light.turns ? (
                  <Angle label="Direction (+ right, − left)" min={-180} max={180} value={a.direction} words={directionWords(a.direction)} disabled={busy} testid="aim-direction"
                    onKeep={(v) => void changeLight(light, leanTurnOf(v, a.elevation), `turn ${light.label}`)} />
                ) : (
                  <div className="py-1" data-testid="aim-direction">
                    <div className="text-stone-600">Direction</div>
                    <div className="text-stone-500">It sits in a U-bracket, so it only tilts: it faces where {lightBar?.label ?? "its bar"} faces. Turn the bar to turn it.</div>
                    {Math.abs(light.turn_deg) > 0.05 && (
                      <button className={`${btn} mt-1`} disabled={busy} onClick={() => void changeLight(light, { turn_deg: null }, `straighten ${light.label}`)}>
                        Straighten it (it is set {Math.abs(a.direction)}° {a.direction > 0 ? "right" : "left"})</button>
                    )}
                  </div>
                )}
                <Angle label="Elevation (+ up, − down)" min={-90} max={90} value={a.elevation} words={elevationWords(a.elevation)} disabled={busy} testid="aim-elevation"
                  onKeep={(v) => void changeLight(light, leanTurnOf(a.direction, v), `tilt ${light.label}`)} />
                <div className="mb-2 text-[12px] text-stone-500">{light.turns ? "In the view: drag the blue ring to turn it, the amber arc to raise or lower it." : "In the view: drag the amber arc to tilt it."}</div>
              </>
            );
          })()}
          {check && (
            <>
              <H>Its beams and the keep-out regions</H>
              {check.crosses.length === 0 ? <div>Its beams cross no keep-out region.</div> : (
                <div className="text-rose-700">Its beams cross {check.crosses.map((n) => bits.keepOut.find((r) => r.name === n)?.label ?? n).join(" and ")}.</div>
              )}
              {check.crosses.length > 0 && (check.clear_lean_deg === null ? <div>No tilt clears it: move the region or the bar.</div> : (
                <button className={`${btn} mt-1`} disabled={busy} onClick={() => void changeLight(light, { lean_deg: check.clear_lean_deg }, `tilt ${light.label} clear`)}>
                  Tilt it clear ({elevationWords(aimOf({ lean_deg: check.clear_lean_deg, turn_deg: light.turn_deg }).elevation)})
                </button>
              ))}
            </>
          )}

        </>
      )}
      {light && settingsBtn(light.id)}
      {roomSel && (
        <>
          <NameField value={roomName} disabled onKeep={() => {}} />
          <div className="mb-2 text-stone-500">The room: everything in it is in the list below it. Its size and the wall people face:</div>
          <div className="[&_section]:border-0 [&_section]:p-0 [&_h3]:text-[13px] [&_input]:h-9" data-testid="room-form-inline">
            <RoomForm key={`${bits?.size.join(",")}${bits?.room.front}`} space={bits!.room}
              onSave={async (body) => {
                const was = { size: bits!.room.size, front: bits!.room.front };
                await act("change the room", () => api.setRoom(body), () => api.setRoom(was));
              }} />
          </div>
          <H>The room's other settings</H>
          <a className="block py-0.5 text-teal-700 hover:underline" href={settingsUrl("modes")}>Modes: what each mode does to the room ›</a>
          <a className="block py-0.5 text-teal-700 hover:underline" href={settingsUrl("listening")}>Listening: microphones ›</a>
          <a className="block py-0.5 text-teal-700 hover:underline" href={settingsUrl()}>All settings: screens, sound, schedules ›</a>
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
      {part && lightBar && (
        <>
          <NameField value={part.label} disabled={busy} onKeep={(v) => void renameLight(part.id, part.label, v)} />
          <div className="mb-2 text-stone-500">
            {part.head
              ? `A moving head ${part.hang ? "hanging under" : "standing on"} ${lightBar.label}: it aims itself, so there is nothing to point here.`
              : `A strobe LED in the face of ${lightBar.label}: it points where the bar faces. The scenes can run the four in order.`}
          </div>
          <div className="text-stone-600">{part.along < -0.01 ? `${Math.abs(part.along).toFixed(2)} m left of the bar's middle` : part.along > 0.01 ? `${part.along.toFixed(2)} m right of the bar's middle` : "in the bar's middle"} (as you face it)</div>
          {settingsBtn(part.id) ?? <a className="mt-3 inline-block text-teal-700 hover:underline" href={`/settings/lights?light=${encodeURIComponent(part.id)}`}>Settings ›</a>}
          {part.head && <div className="mt-1 text-[12px] text-stone-500">Its rest, limits, named positions and wheels are there.</div>}
          <div className="mt-2 text-[12px] text-stone-500">It moves with its bar: select the bar (↑ or Esc) to move or turn it.</div>
        </>
      )}
      {thing && thingAt?.at && (
        <>
          <NameField value={thing.label} disabled={busy} onKeep={(v) => void renameLight(thing.id, thing.label, v)} />
          <div className="mb-2 text-stone-500">
            {thing.kind === "screen" ? `A screen on the ${wallOf(thingAt.plane?.normal) ?? "front"} wall: drag it along the walls` : thing.kind === "mover" ? "A moving light" : "A light"}
          </div>
          <H>Where it is</H>
          {(["East–west", "North–south", "Height"] as const).map((w, i) => (
            <Num key={w} label={w} unit="m" value={thingAt.at![i]} disabled={busy}
              onKeep={(v) => void moveThing(thing.id, { ...placedBody(thingAt), at: thingAt.at!.map((x, k) => (k === i ? v : x)) }, thingAt, `move ${thing.label}`)} />
          ))}
          {thing.kind === "screen" && (
            <>
              <H>Its picture</H>
              {(["Width", "Height"] as const).map((w, i) => (
                <Num key={w} label={w} unit="m" value={thingAt.plane?.size?.[i] ?? 0} disabled={busy}
                  onKeep={(v) => {
                    const sz: [number, number] = [...(thingAt.plane?.size ?? [1, 0.6])] as [number, number];
                    sz[i] = Math.max(0.1, v);
                    void moveThing(thing.id, { plane: { normal: thingAt.plane?.normal ?? [0, -1, 0], up: [0, 0, 1], size: sz } }, thingAt, `resize ${thing.label}`);
                  }} />
              ))}
            </>
          )}
          {thing.kind === "mover" && (
            <>
              <H>How it hangs</H>
              <select className="h-8 w-full rounded border border-stone-300 bg-white px-2" value={thingAt.mount ?? ""} disabled={busy}
                onChange={(e) => void moveThing(thing.id, { mount: e.target.value }, thingAt, `hang ${thing.label}`)}>
                {!thingAt.mount && <option value="">Not said</option>}
                {MOUNTS.map(([m, w]) => <option key={m} value={m}>{w}</option>)}
              </select>
            </>
          )}
          {settingsBtn(thing.id)}
          <button className={`${btn} mt-3 block text-rose-700`} disabled={busy}
            onClick={() => { const t = thing, was = thingAt; setSelected(null); void act(`take ${t.label} out`, () => api.unplace(t.id), () => api.place(t.id, placedBody(was))); }}>
            Take it out of the room</button>
        </>
      )}
      {place && (
        <>
          <NameField value={place.label} disabled={busy} onKeep={(v) => void act(`rename ${place.label}`, () => api.setPlace(place.name, { at: place.at, label: v }), () => api.setPlace(place.name, { at: place.at, label: place.label }))} />
          <div className="mb-2 text-stone-500">A named place: somewhere lights can be pointed at</div>
          <H>Where it is</H>
          {(["East–west", "North–south", "Height"] as const).map((w, i) => (
            <Num key={w} label={w} unit="m" value={place.at[i]} disabled={busy}
              onKeep={(v) => { const at = place.at.map((x, k) => (k === i ? v : x)) as Vec3; void act(`move ${place.label}`, () => api.setPlace(place.name, { at, label: place.label }), () => api.setPlace(place.name, { at: place.at, label: place.label })); }} />
          ))}
          <button className={`${btn} mt-3`} disabled={busy} data-testid="make-area"
            onClick={() => { const p = place; void act(`make ${p.label} an area`, async () => { await api.setArea(p.name, { surface: "floor", centre: [p.at[0], p.at[1], 0], size: [1, 1], label: p.label }); await api.removePlace(p.name); },
              async () => { await api.removeArea(p.name); await api.setPlace(p.name, { at: p.at, label: p.label }); }).then(() => setSelected(`area:${p.name}`)); }}>
            Make it a floor area (a size on the floor)</button>
          <button className={`${btn} mt-2 block text-rose-700`} disabled={busy}
            onClick={() => { const p = place; setSelected(null); void act(`remove ${p.label}`, () => api.removePlace(p.name), () => api.setPlace(p.name, { at: p.at, label: p.label })); }}>
            Remove this place</button>
        </>
      )}
      {area && (
        <>
          <NameField value={area.label} disabled={busy} onKeep={(v) => void changeArea(area, { label: v }, `rename ${area.label}`)} />
          <div className="mb-2 text-stone-500">A floor area: drag it across the floor</div>
          <H>Its middle</H>
          {(["East–west", "North–south"] as const).map((w, i) => (
            <Num key={w} label={w} unit="m" value={area.centre[i]} disabled={busy}
              onKeep={(v) => void changeArea(area, { centre: area.centre.map((x, k) => (k === i ? v : x)) as Vec3 }, `move ${area.label}`)} />
          ))}
          <H>How big it is</H>
          {(["Across (east–west)", "Deep (north–south)"] as const).map((w, i) => (
            <Num key={w} label={w} unit="m" value={area.size[i]} disabled={busy}
              onKeep={(v) => void changeArea(area, { size: area.size.map((x, k) => (k === i ? Math.max(0.1, v) : x)) as [number, number] }, `resize ${area.label}`)} />
          ))}
          <button className={`${btn} mt-3 text-rose-700`} disabled={busy}
            onClick={() => { const a = area; setSelected(null); void act(`remove ${a.label}`, () => api.removeArea(a.name), () => api.setArea(a.name, { surface: a.surface, centre: a.centre, size: a.size, label: a.label })); }}>
            Remove this area</button>
        </>
      )}
      {!roomSel && !bar && !light && !part && !region && !thing && !place && !area && (
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
      <span className="hidden shrink-0 text-stone-500 [@container(min-width:1250px)]:inline">Lights</span>
      <select value={show} onChange={(e) => setShow(e.target.value as typeof show)} aria-label="What the lights show"
        className={`${sel} shrink-0 [@container(min-width:1100px)]:hidden`}>
        {SHOW.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
      </select>
      <div className="hidden shrink-0 overflow-hidden rounded-lg border border-stone-300 [@container(min-width:1100px)]:inline-flex" role="group" aria-label="What the lights show" data-testid="show-what">
        {SHOW.map(([v, label, title], i) => (
          <button key={v} title={title} aria-pressed={show === v} onClick={() => setShow(v)} data-testid={`show-${v}`}
            className={`h-8 whitespace-nowrap px-3 ${i ? "border-l border-stone-300" : ""} ${show === v ? "bg-teal-600 text-white" : "bg-white hover:bg-stone-50"}`}>{label}</button>
        ))}
      </div>
      {show !== "live" && modes.length > 0 && (
        <select value={asMode} aria-label="The room as in" data-testid="as-mode" className={`${sel} w-[150px] min-w-[90px] shrink`}
          title="The lights a mode sets itself (the house lights) show as this mode sets them"
          onChange={(e) => { setAsMode(e.target.value); try { localStorage.setItem("room3d.previewMode", e.target.value); } catch { /* this visit */ } }}>
          {modes.map((m) => <option key={m.id} value={m.id}>Room as in {m.label}</option>)}
        </select>
      )}
      {show === "colour" && (
        <input type="color" value={colour} onChange={(e) => setColour(e.target.value)} aria-label="Colour" className="h-8 w-10 rounded-lg border border-stone-300 bg-white" />
      )}
      {show === "scene" && (
        <>
          <select value={scene} onChange={(e) => setScene(e.target.value)} className={`${sel} w-[180px] min-w-[90px] shrink`} aria-label="Lighting scene">
            {scenes.map((x) => <option key={x.name} value={x.name}>{x.label}</option>)}
          </select>
          <span className="hidden items-center gap-3 [@container(min-width:1650px)]:flex">
            <PreviewKnob label="Energy" value={energy} min={0} max={1} step={0.05} show={(v) => `${Math.round(v * 100)}%`} onCommit={setEnergy} />
            <PreviewKnob label="Tempo" value={bpm} min={60} max={180} step={2} show={(v) => `${Math.round(v)} bpm`} onCommit={setBpm} />
          </span>
          <span className="[@container(min-width:1650px)]:hidden"><PreviewFeel energy={energy} bpm={bpm} setEnergy={setEnergy} setBpm={setBpm} /></span>
        </>
      )}
    </>
  );

  return (
    <div className="space-y-3" data-testid="room-tab">
      <Room3DPage embedded refresh={refresh} editables={editables} selected={selected} mode={mode} picked={region?.name ?? null} startNav={plan ? "plan" : undefined}
        onSelect={(k) => { select(k); if (k) setFrame((n) => n + 1); }} onEdit={onEdit} panel={panel} frame={frame}
        feed={feed} toolbar={toolbar} />
      <p className="text-[15px] text-muted-foreground">
        Everything in the room is edited here: bars and their lights, screens, other lights, named places, floor areas
        and keep-out boxes. Click one to edit it, then drag it (a bar also has a ring to turn it, most things an arrow to
        raise them); a light on a bar has a blue ring for its direction and an amber arc for its elevation. Plan shows
        the room from above, as a map: drag things straight away. Walk puts you in the room; Orbit turns round a point.
        ← Room (or Esc) goes back; Ctrl+Z undoes.
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
