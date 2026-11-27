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

import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from "react";
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
/** A thing in the room: a light or screen of the rig, or a PLANNED one (owner, 27 Nov: "not all of these objects are
 *  'connected' (i.e. real) ... this allows me to treat the studio like a studio"): placed and drawn, driving nothing
 *  until it is connected. ``dev``: a room device's kind (screen, hue-lamp, mirror-ball, mic, light-sensor);
 *  ``typeId``: a planned light's type; ``connect``: what a device stands for, once connected. */
type PThing = Placeable & { planned?: boolean; dev?: string | null; connect?: string | null; typeId?: string | null };
type LibItem = { id: string; label: string; group: string; what: "bar" | "light" | "device"; footprint?: number | null; note?: string };
/** Other words people use for a thing, so the search finds it by any of them (owner, 27 Nov: "where is glitterball?"). */
const ALSO: Record<string, string> = {
  "mirror-ball": "glitterball glitter ball disco ball mirrorball", "rf-glitterball": "mirror ball disco ball mirrorball glitter",
  "rf-pinspot": "pin spot pinspot glitter spot glitterspot spotlight colour light mirror ball light", screen: "tv television display projector monitor picture",
  "hue-lamp": "philips hue bulb lamp light", mic: "microphone mike audio sound", "light-sensor": "lux sensor light meter brightness",
  "z-350": "hazer haze fog smoke machine", "gigbar-move": "gigbar bar light bar chauvet",
};
/** A library thing's own name: "gigbar-par (part, 7 channels)" -> "gigbar-par"; an id alone -> words. */
const libName = (x: LibItem) => { const p = x.label.replace(/\s*\([^)]*\)\s*$/, "").trim(); return p && p !== x.id ? p : nameWords(x.id); };
type RigRow = { id: string; label: string; type: string | null; node_ip: string | null; universe: number | null; address: number | null;
  footprint: number | null; editable: boolean };
const DEVICES = new Set(["screen", "hue-lamp", "mirror-ball", "mic", "light-sensor"]);
const DEVICE_WORDS: Record<string, string> = { screen: "A screen or TV", "hue-lamp": "A Hue lamp", "mirror-ball": "A mirror ball",
  mic: "A microphone", "light-sensor": "A light sensor" };
const FRONT_VEC: Record<string, [number, number]> = { north: [0, 1], east: [1, 0], south: [0, -1], west: [-1, 0] };
type BarBits = { id: string; label: string; at: Vec3; facing_deg: number; front: Vec3; editable: boolean; aims: PartAim[]; parts: BarPartBits[];
  layout: string | null };
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
           editable: b.editable !== false, layout: typeof b.layout === "string" ? b.layout : null,
           aims: Object.entries(b.parts ?? {}).flatMap(([part, p]: [string, any]) =>
             p?.aim && typeof p.aim.lean_deg === "number"
               ? [{ part, id: String(p.id), label: typeof p.label === "string" && p.label !== p.id ? p.label : `${typeof b.label === "string" ? b.label : id} ${part.replace(/(\d+)$/, " $1")}`,
                    at: isV3(s?.appliances?.[p.id]?.at) ? s.appliances[p.id].at : null,
                    lean_deg: p.aim.lean_deg, turn_deg: Number(p.aim.turn_deg) || 0, own: p.aim.own === true, turns: p.turns !== false }]
               : []),
           parts: Object.entries(b.parts ?? {}).map(([part, p]: [string, any]) => {
             const at: Vec3 | null = isV3(s?.appliances?.[p.id]?.at) ? s.appliances[p.id].at : null;
             const ax = [-b.front[1], b.front[0]];           // space.py's: + to the right as you face the bar
             const own = typeof p.label === "string" && p.label !== p.id ? p.label : null;      // a planned part: no light's name yet
             const barName = typeof b.label === "string" ? b.label : id;
             return { part, id: String(p.id), label: own ?? `${barName} ${part.replace(/(\d+)$/, " $1")}`, at,
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
/** A U-bracket's tilt in words: degrees from straight up, towards the room (its bar's front) or away from it. */
const tiltWords = (lean: number) => {
  const l = Math.round(lean * 2) / 2;
  if (Math.abs(l) < 0.25) return "straight up";
  if (Math.abs(l - 90) < 0.25) return "level, towards the room";
  if (Math.abs(l - 180) < 0.25) return "straight down";
  if (l < 0) return `${-l}° back from straight up: away from the room`;
  return l < 90 ? `${l}° forward from straight up: towards the room` : `${l - 90}° below level, towards the room`;
};
const directionWords = (d: number) => (Math.abs(d) < 0.25 ? "straight ahead, the way the bar faces" : `${Math.abs(d)}° to the ${d > 0 ? "right" : "left"} of the way the bar faces`);
const elevationWords = (e: number) => (Math.abs(e - 90) < 0.25 ? "straight up" : Math.abs(e) < 0.25 ? "level"
  : e > 0 ? `${e}° above level (${90 - e}° from straight up)` : `${-e}° below level`);

/** One angle: a slider for the feel of it, the number for exactness; kept when the slider is let go or the number
 *  is entered. */
function Angle({ label, value, min, max, words, disabled, onKeep, onLive, testid }: {
  label: string; value: number; min: number; max: number; words: string; disabled?: boolean; onKeep: (v: number) => void; testid?: string;
  /** As the slider moves (before it is let go and kept): the view shows it at once. */
  onLive?: (v: number) => void;
}) {
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  const keep = (x: number) => { if (x !== value) onKeep(x); };
  return (
    <div className="py-1" data-testid={testid}>
      <Num label={label} unit="°" step={0.5} value={value} disabled={disabled} onKeep={(x) => onKeep(Math.max(min, Math.min(max, x)))} />
      <input type="range" min={min} max={max} step={0.5} value={v} disabled={disabled} aria-label={`${label} slider`}
        onChange={(e) => { const x = Number(e.target.value); setV(x); onLive?.(x); }}
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

/** The "Add to the room" picker (owner, 27 Nov: "'add to this room' is scrollable area inside a scrollable area??
 *  disaster and unusable. Also it should be a pinwheel search + pick list"): a search box over the view with one list
 *  under it, filtered as you type (names, kinds and the other words people use: ALSO), ↑ ↓ to move, Enter to add, Esc
 *  to close. It floats over the page (fixed), so its list is the only thing that scrolls. */
function AddPicker({ items, anchor, onPick, onClose }: {
  items: LibItem[]; anchor: DOMRect | null; onPick: (x: LibItem) => void; onClose: () => void;
}) {
  const [q, setQ] = useState("");
  const [at, setAt] = useState(0);
  const listRef = useRef<HTMLDivElement | null>(null);
  const words = (x: LibItem) => `${libName(x)} ${x.label} ${x.id} ${x.group} ${x.note ?? ""} ${ALSO[x.id] ?? ""}`.toLowerCase();
  const terms = q.toLowerCase().split(/\s+/).filter(Boolean);
  // best first: its name starts with what is typed, then has it, then another word for it has it, then the rest
  const rank = (x: LibItem) => {
    const n = libName(x).toLowerCase(), qq = terms.join(" ");
    return !qq ? 0 : n.startsWith(qq) || n.replace(/\s+/g, "").startsWith(qq.replace(/\s+/g, "")) ? 0
      : terms.every((t) => n.includes(t)) ? 1 : terms.every((t) => (ALSO[x.id] ?? "").includes(t)) ? 2 : 3;
  };
  const shown = items.filter((x) => terms.every((t) => words(x).includes(t)))
    .map((x, i) => ({ x, i, r: rank(x) })).sort((a, b) => a.r - b.r || a.i - b.i).map((e) => e.x);
  useEffect(() => setAt(0), [q]);
  useEffect(() => { listRef.current?.querySelector(`[data-i="${at}"]`)?.scrollIntoView({ block: "nearest" }); }, [at]);
  const w = 380;

  // over the view, beside the panel the button is in
  const left = anchor ? Math.max(8, anchor.left - w - 12) : 120;
  const topAt = anchor ? Math.max(8, Math.min(anchor.top, window.innerHeight - 320)) : 120;
  return (
    <>
      <div className="fixed inset-0 z-40" onClick={onClose} />
      <div className="fixed z-50 flex flex-col rounded-xl border border-stone-200 bg-white shadow-2xl" style={{ top: topAt, left, width: w, maxHeight: `calc(100vh - ${topAt + 16}px)` }}
        data-testid="add-menu" role="dialog" aria-label="Add to the room">
        <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search: a par, a screen, glitterball…" data-testid="add-search"
          className="m-2 h-9 rounded-lg border border-stone-300 px-3 text-[14px] outline-none focus:border-teal-500"
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") { e.preventDefault(); setAt((i) => Math.min(shown.length - 1, i + 1)); }
            else if (e.key === "ArrowUp") { e.preventDefault(); setAt((i) => Math.max(0, i - 1)); }
            else if (e.key === "Enter") { e.preventDefault(); if (shown[at]) onPick(shown[at]); }
            else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); onClose(); }
          }} />
        <div ref={listRef} className="min-h-0 flex-1 overflow-auto px-1 pb-1" role="listbox">
          {shown.length === 0 && <div className="px-3 py-2 text-stone-500">Nothing called that in the library.</div>}
          {shown.map((x, i) => (
            <button key={x.id} data-i={i} role="option" aria-selected={i === at} data-testid={`add-${x.id}`}
              onMouseEnter={() => setAt(i)} onClick={() => onPick(x)}
              className={`flex w-full items-baseline justify-between gap-2 rounded-lg px-3 py-1.5 text-left ${i === at ? "bg-teal-50 text-teal-900" : ""}`}>
              <span className="truncate">{libName(x)}{/\(([^)]*)\)\s*$/.test(x.label) && <span className="text-stone-500"> · {x.label.match(/\(([^)]*)\)\s*$/)![1]}</span>}</span>
              <span className="shrink-0 text-[11px] uppercase tracking-wide text-stone-400">{x.group}</span>
            </button>
          ))}
        </div>
        <div className="border-t border-stone-100 px-3 py-1.5 text-[12px] text-stone-500">It goes in planned: drawn and placed, driving nothing until you connect it.</div>
      </div>
    </>
  );
}

/** What a planned device can stand for, read from where each kind lives: a microphone a sound source (show_service's
 *  senders), a light sensor a room's sensor, a mirror ball a remote-controlled device (room_service). Screens and Hue
 *  lamps are things of the rig: those are offered by the panel itself. */
function DeviceConnect({ kind, room, value, busy, onConnect }: {
  kind: string; room: string; value: string | null; busy: boolean; onConnect: (to: string | null) => void;
}) {
  const [opts, setOpts] = useState<{ id: string; label: string }[] | null>(null);
  useEffect(() => {
    let dead = false;
    void (async () => {
      try {
        let out: { id: string; label: string }[] = [];
        if (kind === "mic") {
          const st: any = await (await fetch(`${room}/show/api/v1/state`, { cache: "no-store" })).json();
          out = Object.entries(st?.senders ?? {}).flatMap(([sid, x]: [string, any]) =>
            (Array.isArray(x?.streams) ? x.streams : []).map((st2: string) => ({ id: String(st2), label: `${st2} (from ${sid})` })));
        } else if (kind === "light-sensor") {
          const st: any = await (await fetch(`${room}/api/v1/status`, { cache: "no-store" })).json();
          out = (st?.rooms ?? []).flatMap((r: any) => (Array.isArray(r?.sensors) ? r.sensors : []).map((x: any) =>
            ({ id: String(x?.id ?? x), label: String(x?.label ?? x?.id ?? x) })));
        } else if (kind === "mirror-ball" || kind.startsWith("rf-")) {
          const d: any = await (await fetch(`${room}/api/v1/remotes`, { cache: "no-store" })).json();
          const model = kind.startsWith("rf-") ? kind.slice(3) : null;
          out = (d?.remotes ?? []).filter((x: any) => !model || x?.model === model || x?.type === model)
            .map((x: any) => ({ id: String(x?.id), label: String(x?.label ?? x?.id) }));
        }
        if (!dead) setOpts(out);
      } catch { if (!dead) setOpts([]); }
    })();
    return () => { dead = true; };
  }, [kind, room]);
  const where = kind === "mic" ? "a microphone sending to the room (Settings › Listening)" : kind === "light-sensor"
    ? "a light sensor in a room (Settings)" : "a remote-controlled device (Light setup › Appliances worked by remote)";
  if (opts === null) return <div className="text-stone-500">Looking for what it can be…</div>;
  return (
    <div className="space-y-1.5">
      {value && <div>Connected to <b>{opts.find((o) => o.id === value)?.label ?? value}</b>.</div>}
      {opts.length ? (
        <select className="h-8 w-full rounded border border-stone-300 bg-white px-2" value={value ?? ""} disabled={busy} data-testid="device-connect"
          onChange={(e) => onConnect(e.target.value || null)}>
          <option value="">{value ? "Disconnect (planned again)" : "Connect it to…"}</option>
          {opts.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
        </select>
      ) : <div className="text-stone-500">There is no {where} to connect it to yet.</div>}
    </div>
  );
}

/** Where a light (or a bar's first channel) is to be on the DMX wire: an Art-Net box, its universe, the address. */
function DmxPick({ nodes, suggest, footprint, busy, onConnect, words }: {
  nodes: { ip: string; universe: number }[]; suggest: number; footprint: number; busy: boolean;
  onConnect: (ip: string, universe: number, address: number) => void; words: string;
}) {
  const [node, setNode] = useState(nodes[0] ? `${nodes[0].ip}|${nodes[0].universe}` : "");
  const [ip, setIp] = useState(nodes[0]?.ip ?? "");
  const [uni, setUni] = useState(String(nodes[0]?.universe ?? 1));
  const [addr, setAddr] = useState(String(suggest));
  useEffect(() => setAddr(String(suggest)), [suggest]);
  const a = Number(addr), u = Number(uni);
  const ok = /^\d{1,3}(\.\d{1,3}){3}$/.test(ip) && Number.isInteger(u) && u >= 0 && Number.isInteger(a) && a >= 1 && a + footprint - 1 <= 512;
  const field = "h-8 w-full rounded border border-stone-300 bg-white px-2 text-stone-900";
  return (
    <div className="space-y-1.5" data-testid="dmx-pick">
      {nodes.length > 0 && (
        <select className={field} value={node} aria-label="Art-Net box"
          onChange={(e) => { setNode(e.target.value); const [i, uu] = e.target.value.split("|"); if (i) { setIp(i); setUni(uu); } }}>
          {nodes.map((n) => <option key={`${n.ip}|${n.universe}`} value={`${n.ip}|${n.universe}`}>Box {n.ip}, universe {n.universe}</option>)}
          <option value="">Another box…</option>
        </select>
      )}
      {(!nodes.length || !node) && (
        <div className="grid grid-cols-[1fr_70px] gap-1.5">
          <input className={field} value={ip} onChange={(e) => setIp(e.target.value)} placeholder="Box IP (192.168.1.50)" aria-label="Box IP" />
          <input className={field} value={uni} onChange={(e) => setUni(e.target.value)} aria-label="Universe" placeholder="Universe" />
        </div>
      )}
      <label className="grid grid-cols-[1fr_90px] items-center gap-2"><span className="text-stone-600">{words}</span>
        <input className={field} value={addr} onChange={(e) => setAddr(e.target.value)} aria-label="DMX address" inputMode="numeric" /></label>
      <div className="text-[12px] text-stone-500">Channels {a || "?"}–{a ? a + footprint - 1 : "?"} ({footprint}).</div>
      <button className="w-full rounded-lg bg-teal-600 px-2.5 py-1.5 font-medium text-white hover:bg-teal-700 disabled:opacity-50" disabled={!ok || busy}
        onClick={() => onConnect(ip, u, a)} data-testid="connect">Connect: make it a real light</button>
    </div>
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
  const [show, setShow] = useState<"mode" | "plain" | "colour" | "scene" | "live">("mode");
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
  // Mode (owner, 27 Nov: "It's ... confusing if it is meant to simulate a mode, but it doesn't do that. fix it"): the
  // whole rig as the mode drives it (show_service preview what=mode): its feel (state.show.main.vibe; a mode that
  // doesn't set one leaves the room's feel as it is: "@current"), its light scene tags, and the lights it sets itself.
  const [modes, setModes] = useState<{ id: string; label: string; lights: Record<string, number>; vibe: string; tags: string[] }[]>([]);
  const [asMode, setAsMode] = useState<string>(() => { try { return localStorage.getItem("room3d.previewMode") ?? ""; } catch { return ""; } });
  useEffect(() => {
    let dead = false;
    void (async () => {
      try {
        const d: any = await (await fetch(`${room}/api/v1/modes`, { cache: "no-store" })).json();
        const st: any = await (await fetch(`${room}/api/v1/status`, { cache: "no-store" })).json().catch(() => ({}));
        const ms = Object.entries(d?.modes ?? {}).map(([id, m]: [string, any]) => ({ id, label: String(m?.label ?? id),
          lights: Object.fromEntries(Object.entries(m?.state?.light ?? {}).map(([lid, v]: [string, any]) =>
            [lid, v === "off" ? 0 : v === "on" ? 1 : typeof v?.bri === "number" ? Math.max(0, Math.min(1, v.bri / 100)) : 1])),
          vibe: m?.state?.show && "main" in m.state.show ? String(m.state.show.main?.vibe ?? "") : "@current",
          tags: (m?.state?.director?.light_scenes?.scene_tags ?? []).filter((t: any) => typeof t === "string") }));
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
    const m = modes.find((x) => x.id === asMode);
    const sets = Object.entries(m?.lights ?? {}).map(([id, v]) => `${id}:${v}`).join(",");
    // Mode: the room as that mode drives it; Plain, Colour, Scene: aiming views, every light, no mode
    const q = show === "mode" && m
      ? `?what=mode&vibe=${encodeURIComponent(m.vibe)}&tags=${encodeURIComponent(m.tags.join(","))}&energy=${energy}&bpm=${bpm}`
        + (sets ? `&set=${encodeURIComponent(sets)}` : "")
      : show === "colour" ? `?what=colour&colour=${encodeURIComponent(colour)}`
      : show === "scene" && scene ? `?what=scene:${encodeURIComponent(scene)}&energy=${energy}&bpm=${bpm}` : "?what=plain";
    return { base: `${room}/show/api/v1/rig/preview`, query: q };
  }, [show, colour, scene, room, energy, bpm, modes, asMode]);
  // undo and redo: every change is a step that knows how to put things back (owner, 27 Nov: "need undo etc. in case of
  // unintended fiddles")
  type Step = { label: string; undo: () => Promise<unknown>; redo: () => Promise<unknown>; group?: string };
  const [past, setPast] = useState<Step[]>([]);
  const [future, setFuture] = useState<Step[]>([]);

  const read = useCallback(async () => {
    try {
      const s: any = await api.space();
      try {
        const rr: any = await (await fetch(`${room}/show/api/v1/rig`, { cache: "no-store" })).json();
        setRig((Array.isArray(rr?.fixtures) ? rr.fixtures : []).map((f: any) => ({ id: String(f.id), label: String(f.label ?? f.id),
          type: f.type ?? null, node_ip: f.node_ip ?? null, universe: typeof f.universe === "number" ? f.universe : null,
          address: typeof f.address === "number" ? f.address : null, footprint: typeof f.footprint === "number" ? f.footprint : null,
          editable: f.editable === true })));
      } catch { /* the rig's rows: next time */ }
      layoutsOf.current = Object.fromEntries(Object.entries(s?.layouts ?? {}).map(([k, v]: [string, any]) => [k, v?.parts ?? {}]));
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
  // one undo step for each thing being edited (owner, 27 Nov: "undo points should be taken before all moves, not each
  // move on the light"): changes to the selected thing join the step it began with, until something else is selected;
  // undo then puts it back as it was before the first of them
  const editing = useRef<{ key: string | null; seq: number }>({ key: null, seq: 0 });
  const act = useCallback(async (label: string, redo: () => Promise<unknown>, undo: () => Promise<unknown>) => {
    await keep(redo);
    const g = editing.current;
    setPast((p) => {
      const top = p[p.length - 1];
      if (top && g.key && top.group === `${g.key}#${g.seq}`)      // the same thing, still selected: one step
        return [...p.slice(0, -1), { ...top, label, redo }];
      return [...p.slice(-49), { label, undo, redo, group: g.key ? `${g.key}#${g.seq}` : undefined }];
    });
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
    if (!bits) return { placedThings: [] as PThing[], unplacedThings: [] as PThing[] };
    const mine: PThing[] = things.filter((t) => !bits.partIds.has(t.id));
    const known = new Set(mine.map((t) => t.id));
    const extra: PThing[] = Object.entries(bits.room.appliances)
      .filter(([id, a]) => a.at && !known.has(id) && !bits.partIds.has(id))
      .map(([id, a]) => {
        const k = a.kind ?? null, dev = k && (DEVICES.has(k) || k.startsWith("rf-")) ? k : null;
        return { id, label: a.label ?? nameWords(id), kind: k === "screen" || (!k && a.plane) ? "screen" : "lamp", zone: null, screen: null,
                 planned: !!k && !(dev && a.connect), dev, connect: a.connect ?? null, typeId: k && !dev ? k : null };
      });
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
        out.push({ key: `screen:${t.id}`, kind: "screen", label: t.planned ? `${t.label} (planned)` : t.label, at: a.at, size: [0.08, sz[0], sz[1]], yaw: Math.atan2(n[1], n[0]), normal: n });
      } else out.push({ key: `thing:${t.id}`, kind: "thing", label: t.planned ? `${t.label} (planned)` : t.label, at: a.at, size: THING_BOX });
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
  const thing: PThing | undefined = (kind === "screen" || kind === "thing") ? placedThings.find((t) => t.id === id) : undefined;
  const thingAt = thing ? bits?.room.appliances[thing.id] : undefined;
  const place = kind === "place" ? bits?.room.points.find((p) => p.name === id) : undefined;
  const area = kind === "area" ? bits?.room.regions.find((a) => a.name === id) : undefined;
  const [roomName, setRoomName] = useState("The room");
  const [liveAim, setLiveAim] = useState<{ id: string; dir: Vec3 } | null>(null);   // a light's aim as its slider moves
  // the light being edited, in the view: on (bright, the default), off, or as the scene has it (owner, 27 Nov: "i should
  // be able to turn a selected light object on and off etc."); drawn only, never sent
  const [editShow, setEditShow] = useState<"on" | "off" | "show">("on");
  // "Point at…" (owner, 27 Nov: "when we're pointing lights, we need a 'point at' option and then user can use
  // crosshairs to click another object or a spot on the wall / floor / ceiling"): the next click in the view aims it
  const [pointing, setPointing] = useState(false);
  // a new thing from the library waits to be put where it is clicked (owner, 27 Nov: "you're dropping new items in
  // the middle of the plan which is a pain ... better to let me place them")
  const [placing, setPlacing] = useState<LibItem | null>(null);
  // the library (GET /rig/library): what can be added; the rig's rows: which lights are real, where they are patched
  const [lib, setLib] = useState<LibItem[]>([]);
  const [rig, setRig] = useState<RigRow[]>([]);
  const [adding, setAdding] = useState(false);
  const addAnchor = useRef<DOMRect | null>(null);
  useEffect(() => {
    void (async () => {
      const got: LibItem[] = [];
      try {
        const d: any = await (await fetch(`${room}/show/api/v1/rig/library`, { cache: "no-store" })).json();
        if (Array.isArray(d?.items)) got.push(...d.items);
      } catch { /* the room devices below still list */ }
      try {                                                 // things worked by a remote (room_service's device types)
        const d: any = await (await fetch(`${room}/api/v1/remotes`, { cache: "no-store" })).json();
        for (const t of d?.types ?? []) if (typeof t?.id === "string")
          got.push({ id: `rf-${t.id}`, label: String(t.label ?? t.id), group: "Worked by a remote", what: "device", note: String(t.description ?? "") });
      } catch { /* none */ }
      setLib(got);
    })();
  }, [room, refresh]);
  // the list shows the selected row: scrolled to once when the selection changes (not on every redraw, which fought
  // the owner's own scrolling: "scrolling hierarchy jumps up while I'm still scrolling"), and not when it was clicked
  // in the list (it is in view already)
  const fromList = useRef(false);
  useEffect(() => { editing.current = { key: selected, seq: editing.current.seq + 1 }; setLiveAim(null); setEditShow("on"); setPointing(false); }, [selected]);
  useEffect(() => {
    if (fromList.current) { fromList.current = false; return; }
    if (!selected) return;
    const t = window.setTimeout(() => {
      // only the panel's own list scrolls, never the page: a page that scrolled under a drag in the view moved the
      // view from under the pointer (owner, 27 Nov: "dragging / moving mirror ball isn't working ... interrupted")
      const el = document.querySelector(`[data-testid="pick-${CSS.escape(selected)}"]`) as HTMLElement | null;
      let box = el?.parentElement ?? null;
      while (box && !(box.scrollHeight > box.clientHeight + 1 && /(auto|scroll)/.test(getComputedStyle(box).overflowY))) box = box.parentElement;
      if (el && box && box !== document.scrollingElement && box !== document.body) {
        const r = el.getBoundingClientRect(), b = box.getBoundingClientRect();
        if (r.top < b.top) box.scrollTop -= b.top - r.top + 8;
        else if (r.bottom > b.bottom) box.scrollTop += r.bottom - b.bottom + 8;
      }
    }, 50);
    return () => window.clearTimeout(t);
  }, [selected]);     // room_service's room (one per deployment here)
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
  /** Point the selected light at a place in the room: a bar's light by its tilt (and turn, if its bracket turns); a
   *  light on its own or a pinspot by keeping the place it points at. */
  const pointAt = (to: Vec3) => {
    setPointing(false);
    if (!bits || !selected) return;
    const k = kindOf(selected), i = idOf(selected);
    if (k === "light") {
      const b = bits.bars.find((x) => x.aims.some((p) => p.id === i)), p = b?.aims.find((x) => x.id === i);
      if (!b || !p?.at) return;
      const d = [to[0] - p.at[0], to[1] - p.at[1], to[2] - p.at[2]], n = Math.hypot(...d) || 1;
      const f = b.front, fwd = (d[0] * f[0] + d[1] * f[1]) / n, side = (d[0] * -f[1] + d[1] * f[0]) / n, up = d[2] / n;
      if (!p.turns) {                                      // a U-bracket: as near as its one axis gets
        const lean = Math.max(-90, Math.min(180, (Math.atan2(fwd, up) * 180) / Math.PI));
        void changeLight(p, { lean_deg: Math.round(lean * 2) / 2, turn_deg: null }, `point ${p.label}`);
      } else {
        let lean = (Math.acos(Math.max(-1, Math.min(1, up))) * 180) / Math.PI, turn = (Math.atan2(side, fwd) * 180) / Math.PI;
        if (Math.abs(turn) > 90) { lean = -lean; turn -= Math.sign(turn) * 180; }
        void changeLight(p, { lean_deg: Math.round(lean * 2) / 2, turn_deg: Math.round(turn * 2) / 2 }, `point ${p.label}`);
      }
    } else if (k === "thing" || k === "screen") {
      const a = bits.room.appliances[i], was = a?.aim ?? null;
      void act(`point ${placedThings.find((t) => t.id === i)?.label ?? i}`, () => api.place(i, { aim: to }), () => api.place(i, { aim: was }));
    }
  };

  /** Rename a light, screen or part: its setup's label (the file's stays as it was); undo puts the old name back. */
  const renameLight = (id: string, was: string, to: string) => {
    const a = bits?.room.appliances[id];
    if (a?.kind && !rig.some((r) => r.id === id))             // planned: its placement's label (it has no setup yet)
      return act(`rename ${was}`, () => api.place(id, { label: to }), () => api.place(id, { label: was }));
    return act(`rename ${was}`, () => api.setup(id, { label: to }), () => api.setup(id, { label: was }));
  };
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
  // -- the room as a studio: adding, duplicating, connecting ------------------------------------------------------
  const rigIds = useMemo(() => new Set(rig.map((r) => r.id)), [rig]);
  /** A bar none of whose parts is a light of the rig yet. */
  const barPlanned = (b: BarBits) => rig.length > 0 && !b.parts.some((p) => rigIds.has(p.id));
  /** An id not yet used by a light, a placed thing or a bar: ``base``-1, -2, ... */
  const freeId = (base: string) => {
    const taken = new Set([...rigIds, ...Object.keys(bits?.room.appliances ?? {}), ...(bits?.bars.map((b) => b.id) ?? [])]);
    const b = base.toLowerCase().replace(/[^a-z0-9_.-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 56) || "thing";
    for (let n = 1; ; n++) if (!taken.has(`${b}-${n}`)) return { id: `${b}-${n}`, n };
  };
  /** Put a library thing in the room, planned (owner, 27 Nov: "I can create new objects easily from this view (of any
   *  class -- tvs, light objects, mirror balls, ... hazers, etc.)"), at ``at`` or a sensible place for its kind. */
  const addThing = async (x: LibItem, at?: Vec3, extra: Record<string, unknown> = {}, label?: string) => {
    if (!bits) return;
    const [X, Y, Z] = bits.size, fv = FRONT_VEC[bits.room.front] ?? [0, 1];
    const mid: Vec3 = [r2(X / 2), r2(Y / 2), 0];
    if (x.what === "bar") {
      const n = bits.bars.length;
      const letter = String.fromCharCode(65 + (n % 26));
      const taken = new Set([...rigIds, ...Object.keys(bits.room.appliances), ...bits.bars.map((b) => b.id)]);
      const id = !taken.has(`bar-${letter.toLowerCase()}`) ? `bar-${letter.toLowerCase()}` : freeId(`bar-${letter.toLowerCase()}`).id;
      const depth = Math.abs(fv[0]) * X + Math.abs(fv[1]) * Y;
      const keepBarZ = extra.keepZ === true;
      delete extra.keepZ;
      const where: Vec3 = at ? [r2(at[0]), r2(at[1]), keepBarZ ? r2(at[2]) : 2.5] : [r2(X / 2 + fv[0] * (depth / 2 - 0.6)), r2(Y / 2 + fv[1] * (depth / 2 - 0.6)), 2.5];
      const facing = Math.round((Math.atan2(-fv[1], -fv[0]) * 180) / Math.PI);
      const lab = label ?? `Bar ${letter}`;
      await act(`add ${lab}`, () => api.putBar(id, { at: where, facing_deg: facing, layout: x.id, label: lab, ...extra }), () => api.removeBar(id));
      setSelected(`bar:${id}`); setFrame((k) => k + 1);
      return;
    }
    // the first of a room device takes its plain name ("mirror-ball"): the name the scenes aim at
    const taken = new Set([...rigIds, ...Object.keys(bits.room.appliances), ...bits.bars.map((b) => b.id), ...bits.room.points.map((p) => p.name)]);
    const { id, n } = x.what === "device" && !taken.has(x.id) ? { id: x.id, n: 1 } : freeId(x.id);
    const plain = x.label.replace(/\s*\([^)]*\)\s*$/, "").trim();                 // "gigbar-par (part, 7 channels)" -> "gigbar-par"
    const lab = label ?? `${plain && plain !== x.id ? plain : nameWords(x.id)} ${n}`;
    let body: Record<string, unknown>;
    const keepZ = extra.keepZ === true;
    delete extra.keepZ;
    if (x.what === "device" && x.id === "screen") {
      // on the wall nearest the click, at the height clicked (a wall), else 1.6 m
      const w = at ? snapToWall(bits.size, at[0], at[1]) : snapToWall(bits.size, ...frontMiddle(bits.size, bits.room.front));
      const z = at && at[2] > 0.3 && at[2] < Z - 0.3 ? r2(at[2]) : 1.6;
      body = { at: [w.x, w.y, z], plane: { normal: w.normal, up: [0, 0, 1], size: [1.2, 0.68] } };
    } else {
      const z = x.id === "mic" ? 1.5 : x.id === "light-sensor" ? Z - 0.02 : x.id === "mirror-ball" || /glitter|mirror/.test(x.id) ? Z - 0.6
        : x.id.startsWith("rf-") ? Z - 0.3
        : x.id === "hue-lamp" ? Z - 0.1 : /hazer|z-350/.test(x.id) ? 0.2 : 2.2;
      // where it was clicked, at the height its kind sits at (a copy: the height it was dropped at)
      body = { at: at ? [r2(at[0]), r2(at[1]), keepZ ? r2(at[2]) : r2(z)] : [mid[0], mid[1], r2(z)] };
    }
    await act(`add ${lab}`, () => api.place(id, { ...body, kind: x.id, label: lab, ...extra }), () => api.unplace(id));
    setSelected(`${x.id === "screen" ? "screen" : "thing"}:${id}`); setFrame((k) => k + 1);
  };
  /** Ctrl+drag (the editor): a copy of the thing, planned, where it was dropped. */
  const duplicate = useCallback((key: string, at: Vec3) => {
    if (!bits) return;
    const k = kindOf(key), i = idOf(key);
    if (k === "bar") {
      const b = bits.bars.find((x) => x.id === i), lay = b?.layout ?? lib.find((x) => x.what === "bar")?.id;
      if (b && lay) void addThing({ id: lay, label: "", group: "", what: "bar" }, [r2(at[0]), r2(at[1]), b.at[2]], { facing_deg: b.facing_deg, keepZ: true }, `${b.label} copy`);
    } else if (k === "thing" || k === "screen") {
      const a = bits.room.appliances[i], t = placedThings.find((x) => x.id === i);
      const typeId = a?.kind ?? rig.find((r) => r.id === i)?.type ?? null;
      if (!typeId || !t) return;
      const what = DEVICES.has(typeId) || typeId.startsWith("rf-") ? "device" : "light";
      void addThing({ id: typeId, label: t.label, group: "", what }, at.map(r2) as Vec3, { ...(a?.plane?.normal ? { plane: placedBody(a).plane } : {}), keepZ: true }, `${t.label} copy`);
    } else if (k === "place") {
      const p = bits.room.points.find((x) => x.name === i);
      if (!p) return;
      const { id: nm } = freeId(p.name);
      void act(`copy ${p.label}`, () => api.setPlace(nm, { at: at.map(r2) as Vec3, label: `${p.label} copy` }), () => api.removePlace(nm)).then(() => setSelected(`place:${nm}`));
    } else if (k === "region") {
      const r = bits.keepOut.find((x) => x.name === i);
      if (!r) return;
      const { id: nm } = freeId(r.name);
      void act(`copy ${r.label}`, () => api.putKeepOut(nm, { centre: at.map(r2), size: r.size, groups: r.groups, label: `${r.label} copy` }),
        () => api.removeKeepOut(nm)).then(() => setSelected(`region:${nm}`));
    }
  }, [bits, lib, placedThings, rig, act, api]);

  /** Connect a planned light: register it as a real light at a DMX address (it keeps its id, place and setup). */
  const connectLight = async (t: PThing, ip: string, universe: number, address: number) => {
    const a = bits?.room.appliances[t.id];
    if (!a || !t.typeId) return;
    const placed = placedBody(a);
    await act(`connect ${t.label}`, async () => {
      await api.unplace(t.id);
      await api.add({ id: t.id, label: t.label, type: t.typeId!, node_ip: ip, universe, address });
      await api.place(t.id, placed);
    }, async () => {
      await api.remove(t.id);
      await api.place(t.id, { ...placed, kind: t.typeId, label: t.label });
    });
  };
  /** Connect a planned bar: register each of its parts at the bar's start address plus the part's offset. */
  const connectBar = async (b: BarBits, ip: string, universe: number, start: number) => {
    const parts = (layoutsOf.current[b.layout ?? ""] ?? {}) as Record<string, any>;
    const todo = b.parts.filter((p) => typeof parts[p.part]?.type === "string" && typeof parts[p.part]?.offset === "number" && !rigIds.has(p.id));
    if (!todo.length) { setErr("Its layout doesn't say each part's type and DMX offset (space.yaml layouts)."); return; }
    await act(`connect ${b.label}`, async () => {
      for (const p of todo) await api.add({ id: p.id, label: `${b.label} ${p.part.replace(/(\d+)$/, " $1")}`, type: parts[p.part].type, node_ip: ip, universe, address: start + parts[p.part].offset });
    }, async () => { for (const p of todo) await api.remove(p.id); });
  };
  /** Disconnect a light registered on this page: it goes back to planned, in the same place (owner: connect "only when
   *  I'm ready"). A light in the deployment's own fixtures.yaml stays as it is. */
  const disconnectLight = async (t: PThing) => {
    const r = rig.find((x) => x.id === t.id), a = bits?.room.appliances[t.id];
    if (!r || !r.editable || !a) return;
    const placed = placedBody(a);
    await act(`disconnect ${t.label}`, async () => {
      await api.remove(t.id);
      await api.place(t.id, { ...placed, kind: r.type, label: t.label });
    }, async () => {
      await api.unplace(t.id);
      await api.add({ id: t.id, label: t.label, type: r.type ?? undefined, node_ip: r.node_ip ?? undefined, universe: r.universe ?? undefined, address: r.address ?? undefined });
      await api.place(t.id, placed);
    });
  };
  /** Connect (or disconnect, null) a planned device to the real thing it stands for. */
  const connectDevice = (t: PThing, to: string | null) =>
    act(to ? `connect ${t.label}` : `disconnect ${t.label}`, () => api.place(t.id, { connect: to }), () => api.place(t.id, { connect: t.connect ?? null }));
  const layoutsOf = useRef<Record<string, any>>({});
  /** The Art-Net boxes and universes the rig's lights are on (to connect another there), and the next free address. */
  const nodes = useMemo(() => {
    const seen = new Map<string, { ip: string; universe: number }>();
    for (const r of rig) if (r.node_ip && r.universe !== null) seen.set(`${r.node_ip}|${r.universe}`, { ip: r.node_ip, universe: r.universe });
    return [...seen.values()];
  }, [rig]);
  const nextFree = (n?: { ip: string; universe: number }) =>
    !n ? 1 : Math.max(1, ...rig.filter((r) => r.node_ip === n.ip && r.universe === n.universe && r.address !== null)
      .map((r) => r.address! + (r.footprint ?? 1)));
  const footprintOf = (typeId: string | null | undefined) => lib.find((x) => x.id === typeId)?.footprint ?? 1;
  const barFootprint = (b: BarBits) => {
    const parts = (layoutsOf.current[b.layout ?? ""] ?? {}) as Record<string, any>;
    return Math.max(1, ...Object.values(parts).map((p: any) => (typeof p?.offset === "number" ? p.offset + footprintOf(p.type) : 0)));
  };
  /** Connect a planned screen or Hue lamp to one of the rig's: the real one takes its place in the room. */
  const connectReal = async (t: PThing, realId: string) => {
    const a = bits?.room.appliances[t.id];
    if (!a) return;
    const placed = placedBody(a);
    await act(`connect ${t.label}`, async () => { await api.place(realId, placed); await api.unplace(t.id); },
      async () => { await api.unplace(realId); await api.place(t.id, { ...placed, kind: a.kind, label: t.label }); });
    setSelected(`${t.dev === "screen" ? "screen" : "thing"}:${realId}`);
  };

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
    if (bits.room.regions.some((a) => a.name === name)) { setSelected(`area:${name}`); return; }   // an area is one already
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
        if (pointing || placing) { setPointing(false); setPlacing(null); return; }
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
  }, [selected, bits, act, api, putRegion, treeOpen, pointing, placing]);

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
  const go = (key: string | null) => { fromList.current = true; select(key); setTreeOpen(false); if (key) setFrame((n) => n + 1); };
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
      <button className={`${btn} mb-1 w-full border-teal-600 text-teal-800`} aria-expanded={adding} data-testid="add"
        onClick={(e) => { addAnchor.current = (e.currentTarget as HTMLElement).getBoundingClientRect(); setAdding((x) => !x); }}>
        ＋ Add to the room…</button>
      {row("room:main", roomName, `${bits.size[0]} × ${bits.size[1]} m, ${bits.size[2]} m high · faces ${bits.room.front}`)}
      <div className="ml-3 border-l border-stone-200 pl-1">
      <H>Bars</H>
      {bits.bars.length === 0 && <div className="px-2 text-stone-500">No bars in the room model.</div>}
      {bits.bars.map((b) => (
        <div key={b.id}>
          {b.editable ? row(`bar:${b.id}`, b.label, barPlanned(b) ? "planned" : `${b.at[2].toFixed(2)} m up`) : <div className="px-2 py-1">{b.label}<span className="text-stone-500"> · set in the room file</span></div>}
          <div className="ml-3 border-l border-stone-200 pl-1">{b.parts.filter((p) => p.at).map((p) => row(partKey(p), short(p, b)))}</div>
        </div>
      ))}
      {placedThings.some((t) => t.kind === "screen") && <H>Screens</H>}
      {placedThings.filter((t) => t.kind === "screen").map((t) => row(`screen:${t.id}`, t.label, t.planned ? "planned" : wallOf(bits.room.appliances[t.id]?.plane?.normal) ? `${wallOf(bits.room.appliances[t.id]?.plane?.normal)} wall` : undefined))}
      {placedThings.some((t) => t.kind !== "screen") && <H>Other lights and devices</H>}
      {placedThings.filter((t) => t.kind !== "screen").map((t) => row(`thing:${t.id}`, t.label, t.planned ? "planned" : t.dev ? DEVICE_WORDS[t.dev]?.replace(/^An? /, "").toLowerCase() : t.kind === "mover" ? "moving light" : undefined))}
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
                : thing ? <><span className="text-stone-500">{thing.kind === "screen" ? "Screens" : thing.dev ? "Room" : "Lights"} › </span>{thing.label}</>
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
      {(bar || light || part || (thing && (thing.typeId || rig.some((r) => r.id === thing.id && r.node_ip) || /pinspot/.test(thing.dev ?? "")))) && (
        <div className="mb-2 flex items-center gap-1.5" data-testid="edit-show">
          <div className="inline-flex overflow-hidden rounded-lg border border-stone-300" role="group" aria-label="In the view">
            {([["on", "On", "Lit bright while you edit it"], ["off", "Off", "Dark, to see the room without it"], ["show", "As the scene", "As the preview's scene has it"]] as const).map(([v, w, t], i) => (
              <button key={v} title={`${t} (in the view only)`} aria-pressed={editShow === v} onClick={() => setEditShow(v)} data-testid={`edit-show-${v}`}
                className={`h-7 px-2 text-[12px] ${i ? "border-l border-stone-300" : ""} ${editShow === v ? "bg-teal-600 text-white" : "bg-white hover:bg-stone-50"}`}>{w}</button>
            ))}
          </div>
          {(light || (thing && !thing.dev && (thing.typeId || rig.some((r) => r.id === thing.id && r.node_ip))) || (thing?.dev && /pinspot/.test(thing.dev))) && (
            <button className={`${btn} h-7 px-2 py-0 text-[12px] ${pointing ? "border-teal-600 bg-teal-50 text-teal-800" : ""}`} data-testid="point-at"
              title="Click a thing, or a spot on a wall, the floor or the ceiling, in the view: it points there" onClick={() => setPointing((x) => !x)}>⌖ Point at…</button>
          )}
          {(() => {
            const rid = light?.id ?? part?.id ?? thing?.id ?? null;
            const real = rid && rig.some((r) => r.id === rid && r.node_ip);
            return real ? <button className={`${btn} h-7 px-2 py-0 text-[12px]`} title="Flash the real light for a few seconds, to find it in the room"
              onClick={() => void api.identify(rid!, 5).catch(() => setErr("It didn't answer."))} data-testid="identify">Identify</button> : null;
          })()}
        </div>
      )}
      {bar && (
        <>
          <NameField value={bar.label} disabled={busy} onKeep={(v) => void act(`rename ${bar.label}`, () => api.putBar(bar.id, { label: v }), () => api.putBar(bar.id, { label: bar.label }))} />
          <div className="mb-2 text-stone-500">A bar on its stand{bar.layout ? ` (${nameWords(bar.layout)})` : ""}</div>
          {barPlanned(bar) && (
            <div className="mb-2 space-y-1.5" data-testid="connection">
              <div className="rounded-lg bg-amber-50 px-2 py-1.5 text-amber-900" data-testid="planned-state">Planned: drawn and placed here, its lights drive nothing yet.</div>
              <DmxPick nodes={nodes} suggest={nextFree(nodes[0])} footprint={barFootprint(bar)} busy={busy} words="The bar's start address"
                onConnect={(ip, u, ad) => void connectBar(bar, ip, u, ad)} />
              <button className={`${btn} text-rose-700`} disabled={busy} data-testid="remove-bar"
                onClick={() => { const b = bar; setSelected(null); void act(`remove ${b.label}`, () => api.removeBar(b.id),
                  () => api.putBar(b.id, { at: b.at, facing_deg: b.facing_deg, layout: b.layout ?? undefined, label: b.label })); }}>Take this bar out of the room</button>
            </div>
          )}
          {modeRow("bar")}
          <H>Where it stands</H>
          <Num label="East–west" unit="m" value={bar.at[0]} disabled={busy} onKeep={(v) => void changeBar(bar, { at: [v, bar.at[1], bar.at[2]] }, `move ${bar.label}`)} />
          <Num label="North–south" unit="m" value={bar.at[1]} disabled={busy} onKeep={(v) => void changeBar(bar, { at: [bar.at[0], v, bar.at[2]] }, `move ${bar.label}`)} />
          <Num label="Height of the bar" unit="m" value={bar.at[2]} disabled={busy} onKeep={(v) => void changeBar(bar, { at: [bar.at[0], bar.at[1], v] }, `raise ${bar.label}`)} />
          <Num label="Faces (0 east, 90 north)" unit="°" step={1} value={bar.facing_deg} disabled={busy} onKeep={(v) => void changeBar(bar, { facing_deg: v }, `turn ${bar.label}`)} />
          <H>Lights on it</H>
          {bar.parts.filter((p) => p.at).map((p) => {
            const a = bar.aims.find((x) => x.id === p.id);
            return row(partKey(p), short(p, bar), a ? (a.turns ? elevationWords(aimOf(a).elevation) : tiltWords(a.lean_deg)) : p.head ? "moving head" : "strobe LED");
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
                  <>
                    <Angle label="Direction (+ right, − left)" min={-180} max={180} value={a.direction} words={directionWords(a.direction)} disabled={busy} testid="aim-direction"
                      onLive={(v) => { const lt = leanTurnOf(v, a.elevation); setLiveAim({ id: light.id, dir: aimDirOf(lightBar!.front, lt.lean_deg, lt.turn_deg) }); }}
                      onKeep={(v) => void changeLight(light, leanTurnOf(v, a.elevation), `turn ${light.label}`)} />
                    <Angle label="Elevation (+ up, − down)" min={-90} max={90} value={a.elevation} words={elevationWords(a.elevation)} disabled={busy} testid="aim-elevation"
                      onLive={(v) => { const lt = leanTurnOf(a.direction, v); setLiveAim({ id: light.id, dir: aimDirOf(lightBar!.front, lt.lean_deg, lt.turn_deg) }); }}
                      onKeep={(v) => void changeLight(light, leanTurnOf(a.direction, v), `tilt ${light.label}`)} />
                  </>
                ) : (
                  // a U-bracket: one angle, as you set it on the real bracket (owner, 27 Nov: "still don't understand
                  // why lasers don't face the same way??": two angles hid that one was tilted back over its bar)
                  <>
                    <Angle label="Tilt (− back, + forward)" min={-90} max={180} value={Math.round(light.lean_deg * 2) / 2} words={tiltWords(light.lean_deg)} disabled={busy} testid="aim-tilt"
                      onLive={(v) => setLiveAim({ id: light.id, dir: aimDirOf(lightBar!.front, v, 0) })}
                      onKeep={(v) => void changeLight(light, { lean_deg: v, turn_deg: null }, `tilt ${light.label}`)} />
                    <div className="text-[12px] text-stone-500">It sits in a U-bracket, so it only tilts, about the bar's length: it faces where {lightBar?.label ?? "its bar"} faces. Turn the bar to turn it.</div>
                  </>
                )}
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
                  Tilt it clear ({tiltWords(check.clear_lean_deg)})
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
            {thing.dev ? DEVICE_WORDS[thing.dev] ?? `${libName(lib.find((x) => x.id === thing.dev) ?? { id: thing.dev, label: thing.dev, group: "", what: "device" })}, worked by a remote` : thing.kind === "screen" ? `A screen on the ${wallOf(thingAt.plane?.normal) ?? "front"} wall: drag it along the walls` : thing.kind === "mover" ? "A moving light" : "A light"}
            {thing.typeId ? ` (${lib.find((x) => x.id === thing.typeId)?.label ?? thing.typeId})` : ""}
          </div>
          {(() => {
            const r = rig.find((x) => x.id === thing.id);
            const real = !!r && !thing.planned;
            return (
              <div className="mb-2 space-y-1.5" data-testid="connection">
                <div className={`rounded-lg px-2 py-1.5 ${thing.planned ? "bg-amber-50 text-amber-900" : "bg-teal-50 text-teal-900"}`} data-testid="planned-state">
                  {thing.planned ? "Planned: drawn and placed here, it drives nothing and listens to nothing yet."
                    : r ? (r.node_ip ? `Connected: a real light, DMX ${r.address}–${(r.address ?? 0) + (r.footprint ?? 1) - 1} on box ${r.node_ip}, universe ${r.universe}.` : "Connected: a real one.")
                    : "Connected."}
                </div>
                {thing.planned && thing.typeId && (
                  <DmxPick nodes={nodes} suggest={nextFree(nodes[0])} footprint={footprintOf(thing.typeId)} busy={busy} words="Its DMX address"
                    onConnect={(ip, u, ad) => void connectLight(thing, ip, u, ad)} />
                )}
                {thing.dev && (thing.dev === "screen" || thing.dev === "hue-lamp") && thing.planned && (() => {
                  const cands = unplacedThings.filter((u) => !u.planned && (thing.dev === "screen" ? u.kind === "screen" : u.kind === "lamp" && !rig.find((x) => x.id === u.id)?.node_ip));
                  return cands.length ? (
                    <select className="h-8 w-full rounded border border-stone-300 bg-white px-2" value="" disabled={busy} data-testid="device-connect"
                      onChange={(e) => { if (e.target.value) void connectReal(thing, e.target.value); }}>
                      <option value="">Connect it to…</option>
                      {cands.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
                    </select>
                  ) : <div className="text-stone-500">Every {thing.dev === "screen" ? "screen" : "Hue lamp"} the room has is placed already: add one in Settings, then connect this to it.</div>;
                })()}
                {thing.dev && thing.dev !== "screen" && thing.dev !== "hue-lamp" && (
                  <DeviceConnect kind={thing.dev} room={room} value={thing.connect ?? null} busy={busy} onConnect={(to) => void connectDevice(thing, to)} />
                )}
                {real && r?.editable && r.node_ip && (
                  <button className={btn} disabled={busy} onClick={() => void disconnectLight(thing)} data-testid="disconnect">Disconnect: back to planned</button>
                )}
                {real && r && !r.editable && r.node_ip && <div className="text-[12px] text-stone-500">Connected in the deployment's own fixtures.yaml: change it there.</div>}
              </div>
            );
          })()}
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
  const SHOW: [typeof show, string, string][] = [["mode", "Mode", "The room as a mode runs it: its feel's scenes and colours, and the lights it sets"],
    ["plain", "Plain", "Every light steady in plain light: for aiming"],
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
      {show === "colour" && (
        <input type="color" value={colour} onChange={(e) => setColour(e.target.value)} aria-label="Colour" className="h-8 w-10 rounded-lg border border-stone-300 bg-white" />
      )}
      {show === "mode" && modes.length > 0 && (
        <select value={asMode} aria-label="Which mode" data-testid="as-mode" className={`${sel} w-[150px] min-w-[90px] shrink`}
          title="The room as this mode runs it"
          onChange={(e) => { setAsMode(e.target.value); try { localStorage.setItem("room3d.previewMode", e.target.value); } catch { /* this visit */ } }}>
          {modes.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
        </select>
      )}
      {show === "scene" && (
          <select value={scene} onChange={(e) => setScene(e.target.value)} className={`${sel} w-[180px] min-w-[90px] shrink`} aria-label="Lighting scene">
            {scenes.map((x) => <option key={x.name} value={x.name}>{x.label}</option>)}
          </select>
      )}
      {(show === "scene" || (show === "mode" && !!modes.find((x) => x.id === asMode && x.vibe))) && (
        <>
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
      {adding && <AddPicker items={lib} anchor={addAnchor.current} onClose={() => setAdding(false)}
        onPick={(x) => { setAdding(false); setPointing(false); setPlacing(x); }} />}
      <Room3DPage embedded refresh={refresh} liveAim={liveAim} editShow={editShow} pointing={pointing || !!placing}
        pointWords={placing ? `Click where ${libName(placing)} goes · Esc: cancel` : undefined}
        pointThings={!placing}
        onPointAt={(to) => { if (placing) { const x = placing; setPlacing(null); void addThing(x, to); } else pointAt(to); }} editables={editables} selected={selected} mode={mode} picked={region?.name ?? null} startNav={plan ? "plan" : undefined}
        onSelect={(k) => { select(k); if (k) setFrame((n) => n + 1); }} onEdit={onEdit} onDuplicate={duplicate} panel={panel} frame={frame}
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
