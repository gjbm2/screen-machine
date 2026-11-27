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
// A planned light (a room device not yet a registered light) is still placed in the older view: "Place a planned light".

import { lazy, Suspense, useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Notice, Spinner } from "@/pages/Room/bits";
import Room3DPage from "@/pages/Room3D";
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
      <span className="text-[#aab0c0]">{label}</span>
      <input type="number" step={step} value={text} disabled={disabled} aria-label={label}
        onChange={(e) => setText(e.target.value)} onBlur={keep}
        onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
        className="h-8 w-full rounded border border-[#2c3346] bg-[#141824] px-2 text-right tabular-nums text-[#e3e6ee]" />
      <span className="text-[#8d93a5]">{unit ?? ""}</span>
    </label>
  );
}

const btn = "rounded border border-[#2c3346] bg-[#1a1e2a] px-2.5 py-1.5 text-[#e3e6ee] hover:bg-[#232838] disabled:opacity-50";
const H = ({ children }: { children: ReactNode }) => <h3 className="mb-1 mt-3 text-[11px] font-semibold uppercase tracking-wide text-[#8d93a5]">{children}</h3>;
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

  // what can be clicked in the view
  const editables = useMemo<Editable[]>(() => {
    if (!bits) return [];
    const out: Editable[] = [];
    for (const b of bits.bars) {
      if (b.editable) out.push({ key: `bar:${b.id}`, kind: "bar", at: b.at, size: BAR_BOX, yaw: (b.facing_deg * Math.PI) / 180 });
      for (const p of b.aims) {
        if (p.at) out.push({ key: `light:${p.id}`, kind: "light", at: p.at, size: LIGHT_BOX, dir: aimDirOf(b.front, p.lean_deg, p.turn_deg), front: b.front });
      }
    }
    for (const r of bits.keepOut) if (r.editable) out.push({ key: `region:${r.name}`, kind: "region", at: r.centre, size: r.size });
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
  const onEdit = useCallback((key: string, c: EditChange) => {
    const k = kindOf(key), i = idOf(key);
    if (k === "bar") void keep(() => api.putBar(i, { ...(c.at ? { at: c.at } : {}), ...(c.facing_deg !== undefined ? { facing_deg: c.facing_deg } : {}) }));
    else if (k === "light") void keep(() => api.setup(i, { lean_deg: c.lean_deg, turn_deg: c.turn_deg }));
    else {
      const r = bits?.keepOut.find((x) => x.name === i);
      if (r) void keep(() => putRegion(r, { centre: c.at ?? r.centre, size: c.size ?? r.size }));
    }
  }, [api, bits, keep, putRegion]);

  const addRegion = () => {
    if (!bits) return;
    const taken = new Set(bits.keepOut.map((r) => r.name));
    let n = 1;
    while (taken.has(`keep-out-${n}`)) n++;
    const name = `keep-out-${n}`;
    void keep(() => api.putKeepOut(name, { centre: [bits.size[0] / 2, bits.size[1] / 2, Math.max(0.4, bits.size[2] - 0.6)],
      size: [0.6, 0.6, 0.6], groups: [], label: `Keep out ${n}` })).then(() => select(`region:${name}`));
  };

  // W move, E turn, R scale, Esc let go (as in any 3D studio), while something is selected and no box is being typed in
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!selected || e.metaKey || e.ctrlKey || e.altKey) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT")) return;
      const want = ({ w: "translate", e: "rotate", r: "scale" } as Record<string, GizmoMode>)[e.key.toLowerCase()];
      if (e.key === "Escape") setSelected(null);
      else if (e.key.toLowerCase() === "f") setFrame((n) => n + 1);
      else if (want && modesOf(kindOf(selected)).includes(want)) setMode(want);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selected]);

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
        <button key={m} className={`${btn} ${mode === m ? "border-[#35e0ff] text-[#35e0ff]" : ""}`} onClick={() => setMode(m)}>{MODE_NAMES[m]}</button>
      ))}
      <button className={btn} onClick={() => setSelected(null)}>Done (Esc)</button>
    </div>
  );
  const row = (key: string, label: string, sub?: string) => (
    <button key={key} onClick={() => { select(key); setFrame((n) => n + 1); }} data-testid={`pick-${key}`}
      className="block w-full rounded px-2 py-1.5 text-left hover:bg-[#1a1e2a]">
      {label}{sub ? <span className="text-[#8d93a5]"> · {sub}</span> : null}
    </button>
  );

  const panel: ReactNode = !bits ? <p className="py-2 text-[#8d93a5]">Reading the room…</p> : (
    <div data-testid="inspector-body">
      {bar && (
        <>
          <div className="text-[15px] font-semibold">{bar.label}</div>
          <div className="mb-2 text-[#8d93a5]">A bar on its stand</div>
          {modeRow("bar")}
          <H>Where it stands</H>
          <Num label="East–west" unit="m" value={bar.at[0]} disabled={busy} onKeep={(v) => void keep(() => api.putBar(bar.id, { at: [v, bar.at[1], bar.at[2]] }))} />
          <Num label="North–south" unit="m" value={bar.at[1]} disabled={busy} onKeep={(v) => void keep(() => api.putBar(bar.id, { at: [bar.at[0], v, bar.at[2]] }))} />
          <Num label="Height of the bar" unit="m" value={bar.at[2]} disabled={busy} onKeep={(v) => void keep(() => api.putBar(bar.id, { at: [bar.at[0], bar.at[1], v] }))} />
          <Num label="Faces (0 east, 90 north)" unit="°" step={1} value={bar.facing_deg} disabled={busy} onKeep={(v) => void keep(() => api.putBar(bar.id, { facing_deg: v }))} />
          <H>Lights on it</H>
          {bar.aims.map((p) => row(`light:${p.id}`, p.label, tiltWords(p.lean_deg)))}
        </>
      )}
      {light && (
        <>
          <div className="text-[15px] font-semibold">{light.label}</div>
          <div className="mb-2 text-[#8d93a5]">A light on {lightBar?.label}: turn it to where the real one points</div>
          {modeRow("light")}
          <H>How it points</H>
          <Num label="Tilt from straight up" unit="°" step={0.5} value={light.lean_deg} disabled={busy} onKeep={(v) => void keep(() => api.setup(light.id, { lean_deg: v }))} />
          <div className="mb-1 text-right text-[#8d93a5]">{tiltWords(light.lean_deg)}</div>
          <Num label="Turn (+ to its right)" unit="°" step={0.5} value={light.turn_deg} disabled={busy} onKeep={(v) => void keep(() => api.setup(light.id, { turn_deg: v }))} />
          <div className="mb-2 text-right text-[#8d93a5]">{turnWords(light.turn_deg)}</div>
          {light.own && <button className={btn} disabled={busy} onClick={() => void keep(() => api.setup(light.id, { lean_deg: null, turn_deg: null }))}>Back to how it ships</button>}
          {check && (
            <>
              <H>Its beams and the keep-out regions</H>
              {check.crosses.length === 0 ? <div>Its beams cross no keep-out region.</div> : (
                <div className="text-[#ff8080]">Its beams cross {check.crosses.map((n) => bits.keepOut.find((r) => r.name === n)?.label ?? n).join(" and ")}.</div>
              )}
              {check.crosses.length > 0 && (check.clear_lean_deg === null ? <div>No tilt clears it: move the region or the bar.</div> : (
                <button className={`${btn} mt-1`} disabled={busy} onClick={() => void keep(() => api.setup(light.id, { lean_deg: check.clear_lean_deg }))}>
                  Tilt it clear ({tiltWords(check.clear_lean_deg)})
                </button>
              ))}
            </>
          )}
          {lightBar && <div className="mt-3">{row(`bar:${lightBar.id}`, `‹ ${lightBar.label}`)}</div>}
        </>
      )}
      {region && (
        <>
          <div className="mb-2 text-[#8d93a5]">A keep-out region: no beam should cross it</div>
          {modeRow("region")}
          <label className="block py-0.5">
            <span className="text-[#aab0c0]">Name</span>
            <input defaultValue={region.label} key={region.name + region.label} maxLength={40} aria-label="Name"
              onBlur={(e) => { const v = e.target.value.trim(); if (v && v !== region.label) void keep(() => putRegion(region, { label: v })); }}
              onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
              className="mt-0.5 h-8 w-full rounded border border-[#2c3346] bg-[#141824] px-2 text-[#e3e6ee]" />
          </label>
          <H>Where its middle is</H>
          {(["East–west", "North–south", "Height"] as const).map((w, i) => (
            <Num key={w} label={w} unit="m" value={region.centre[i]} disabled={busy}
              onKeep={(v) => void keep(() => putRegion(region, { centre: region.centre.map((x, k) => (k === i ? v : x)) as Vec3 }))} />
          ))}
          <H>How big it is</H>
          {(["Wide (east–west)", "Deep (north–south)", "Tall"] as const).map((w, i) => (
            <Num key={w} label={w} unit="m" value={region.size[i]} disabled={busy}
              onKeep={(v) => void keep(() => putRegion(region, { size: region.size.map((x, k) => (k === i ? Math.max(0.1, v) : x)) as Vec3 }))} />
          ))}
          <H>Which lights keep out (none ticked: every light)</H>
          {Object.entries(bits.lightGroups).filter(([g, e]) => g !== "*" && e.members.length > 0).map(([g, e]) => (
            <label key={g} className="flex items-center gap-2 py-0.5">
              <input type="checkbox" className="h-4 w-4" checked={region.groups.includes(g)} disabled={busy}
                onChange={(ev) => void keep(() => putRegion(region, { groups: ev.target.checked ? [...region.groups, g] : region.groups.filter((x) => x !== g) }))} />
              {e.label}
            </label>
          ))}
          <button className={`${btn} mt-3 text-[#ff8080]`} disabled={busy}
            onClick={() => { const n = region.name; setSelected(null); void keep(() => api.removeKeepOut(n)); }}>Remove this region</button>
        </>
      )}
      {!bar && !light && !region && (
        <>
          <div className="text-[#aab0c0]">Click a bar, a light or a keep-out box in the room, or pick one here.</div>
          <H>Bars</H>
          {bits.bars.length === 0 && <div className="text-[#8d93a5]">No bars in the room model.</div>}
          {bits.bars.map((b) => (
            <div key={b.id}>
              {b.editable ? row(`bar:${b.id}`, b.label, `${b.at[2].toFixed(2)} m up`) : <div className="px-2 py-1.5">{b.label}<span className="text-[#8d93a5]"> · set in the room file</span></div>}
              <div className="ml-3 border-l border-[#252a38] pl-1">{b.aims.map((p) => row(`light:${p.id}`, p.label))}</div>
            </div>
          ))}
          <H>Keep-out regions</H>
          {bits.keepOut.length === 0 && <div className="px-2 text-[#8d93a5]">None yet.</div>}
          {bits.keepOut.map((r) => (r.editable
            ? row(`region:${r.name}`, r.label, r.groups.length ? r.groups.map((g) => bits.lightGroups[g]?.label ?? g).join(", ") : "every light")
            : <div key={r.name} className="px-2 py-1.5">{r.label}<span className="text-[#8d93a5]"> · set in the room file</span></div>))}
          <button className={`${btn} mt-2`} disabled={busy} onClick={addRegion} data-testid="add-keep-out">+ Add a keep-out region</button>
          {Object.values(bits.lasers).some((c) => c.crosses.length > 0) && (
            <div className="mt-3 text-[#ff8080]">A laser's beams cross a keep-out region: select the laser to tilt it clear.</div>
          )}
        </>
      )}
      {busy && <div className="mt-2 text-[#8d93a5]">Saving…</div>}
    </div>
  );

  return (
    <div className="space-y-3" data-testid="room-tab">
      <Room3DPage embedded refresh={refresh} editables={editables} selected={selected} mode={mode} picked={region?.name ?? null}
        onSelect={select} onEdit={onEdit} panel={panel} frame={frame} />
      <p className="text-[15px] text-muted-foreground">
        Click a bar, a light or a keep-out box to select it, then drag its handles: W moves, E turns, R scales, F
        frames it, Esc lets go. Drag empty space to look round; the wheel zooms. Set things to match the real room:
        the show aims by them.
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
