// The Light setup page's 3D tab (?tab=3d): the room simulator (pages/Room3D: the room as configured, every light drawn
// from the Art-Net wire) and, beside it, the room's keep-out regions (owner, 27 Nov: "we need configurable 'keep out'
// regions in the room. the whole simulator view should be integrated into the lighting set up app section so we can
// create and define those").
//   - A keep-out region is a box of the room no beam should cross: a mirror ball, the projected picture. It is for
//     the light groups ticked, or for every light when none is. Saved to the room (PUT /rig/space/keep_out/<name>).
//   - A moving light steers round a region that is for it (show_service: the look engine's aim steps aside).
//   - A bar's laser can't be steered: its burst goes where the unit is tilted. So each laser is checked against the
//     regions, and when its burst crosses one the page says the tilt that clears it and can set that tilt (the real
//     unit must then be tilted the same: the laser's row under Lights, "How it is tilted").
//   - While a region is being edited the simulator shows it live, before it is saved.
// A planned light (a room device not yet a registered light) is still placed in the older view: "Place a planned light".

import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { cn } from "@/lib/utils";
import { Notice, Spinner } from "@/pages/Room/bits";
import { CARD_CLASS } from "@/pages/Room/theme";
import Room3DPage from "@/pages/Room3D";
import { keepOutOf, type KeepRegion, type LaserCheck } from "@/pages/Room3D/model";
import { explainLightsError, type LightsApi } from "./api";
import type PlannedView from "./Room3D";

const Planned = lazy(() => import("./Room3D"));
const POLL_MS = 4000;
const AXES: [string, string][] = [["East–west", "m from the west wall"], ["North–south", "m from the south wall"], ["Height", "m above the floor"]];
const SIZES = ["Wide (east–west)", "Deep (north–south)", "Tall"];

type Vec3 = [number, number, number];
type SpaceBits = ReturnType<typeof keepOutOf> & { size: Vec3; points: { name: string; label: string; at: Vec3 }[] };

function slug(label: string, taken: Set<string>): string {
  const base = label.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "region";
  let name = base;
  for (let i = 2; taken.has(name); i++) name = `${base}-${i}`;
  return name;
}

const lean = (deg: number) => (Math.abs(deg) < 0.05 ? "straight up" : `${Math.abs(deg).toFixed(1)}° ${deg > 0 ? "forward" : "back"}`);

/** The older view's own props (the page's space state, the map's things, the lamps' colours): passed straight on. */
type PlannedProps = Parameters<typeof PlannedView>[0];

export default function RoomTab({ api, space, things, lamps }: PlannedProps & { api: LightsApi }) {
  const [bits, setBits] = useState<SpaceBits | null>(null);
  const [draft, setDraft] = useState<KeepRegion | null>(null);       // the region being edited (unsaved)
  const [isNew, setIsNew] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [planned, setPlanned] = useState(false);

  const read = useCallback(async () => {
    try {
      const s: any = await api.space();
      const size: Vec3 = Array.isArray(s?.room?.size) && s.room.size.length === 3 ? s.room.size : [6, 6, 3];
      const points = Object.entries(s?.points ?? {}).flatMap(([name, p]: [string, any]) =>
        Array.isArray(p?.at) && p.at.length === 3 ? [{ name, label: typeof p.label === "string" ? p.label : name, at: p.at as Vec3 }] : []);
      setBits({ ...keepOutOf(s), size, points });
    } catch (e) {
      setErr(explainLightsError(e));
    }
  }, [api]);
  useEffect(() => {
    void read();
    const t = window.setInterval(() => void read(), POLL_MS);
    return () => window.clearInterval(t);
  }, [read]);

  const regions = useMemo(() => {
    const saved = bits?.keepOut ?? [];
    if (!draft) return saved;
    return saved.some((r) => r.name === draft.name) ? saved.map((r) => (r.name === draft.name ? draft : r)) : [...saved, draft];
  }, [bits, draft]);
  const hasPlanned = Object.values(space.space?.appliances ?? {}).some((a) => !!a.kind);

  const add = () => {
    if (!bits) return;
    const taken = new Set(bits.keepOut.map((r) => r.name));
    const label = "Keep out";
    setDraft({ name: slug(label, taken), label, centre: [bits.size[0] / 2, bits.size[1] / 2, Math.max(0.3, bits.size[2] - 0.5)],
      size: [0.6, 0.6, 0.6], groups: [], editable: true });
    setIsNew(true);
    setErr(null);
  };
  const save = async () => {
    if (!draft) return;
    setBusy(true);
    setErr(null);
    try {
      const name = isNew ? slug(draft.label, new Set((bits?.keepOut ?? []).map((r) => r.name))) : draft.name;
      await api.putKeepOut(name, { centre: draft.centre, size: draft.size, groups: draft.groups, label: draft.label });
      setDraft(null);
      await read();
    } catch (e) {
      setErr(explainLightsError(e));
    } finally {
      setBusy(false);
    }
  };
  const remove = async (name: string) => {
    setBusy(true);
    setErr(null);
    try {
      await api.removeKeepOut(name);
      if (draft?.name === name) setDraft(null);
      await read();
    } catch (e) {
      setErr(explainLightsError(e));
    } finally {
      setBusy(false);
    }
  };
  const setTilt = async (id: string, deg: number) => {
    setBusy(true);
    setErr(null);
    try {
      await api.setup(id, { lean_deg: deg });
      await read();
    } catch (e) {
      setErr(explainLightsError(e));
    } finally {
      setBusy(false);
    }
  };
  const vec = (v: Vec3, i: number, x: number): Vec3 => v.map((y, k) => (k === i ? Math.round(x * 100) / 100 : y)) as Vec3;
  const groupWords = (r: KeepRegion) => (r.groups.length
    ? r.groups.map((g) => bits?.lightGroups[g]?.label ?? g).join(", ")
    : "Every light");
  const lasers = Object.entries(bits?.lasers ?? {}) as [string, LaserCheck][];
  const nameOf = (id: string) => things.find((t) => t.id === id)?.label ?? id;
  const regionLabel = (n: string) => bits?.keepOut.find((r) => r.name === n)?.label ?? n;

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

  return (
    <div className="space-y-4" data-testid="room-tab">
      <Room3DPage embedded picked={draft?.name ?? null} regions={regions} />

      <section className={cn(CARD_CLASS, "space-y-3 px-3 py-3 sm:px-4")} data-testid="keep-out">
        <div>
          <h2 className="text-lg font-semibold">Keep-out regions</h2>
          <p className="text-[15px] text-muted-foreground">
            A box of the room no beam should cross: a mirror ball, the projected picture. A moving light steers round one
            that is for it. A laser can't be steered, so it is checked below and tilted clear.
          </p>
        </div>
        {!bits ? (
          <p className="flex items-center gap-2 text-[15px] text-muted-foreground"><Spinner /> Reading the room…</p>
        ) : (
          <>
            {bits.keepOut.length === 0 && !draft && <p className="text-[15px]">None yet.</p>}
            <ul className="space-y-2">
              {bits.keepOut.map((r) => (
                <li key={r.name} className="flex flex-wrap items-center gap-2 rounded-xl border border-stone-200 bg-white px-3 py-2" data-testid={`keep-${r.name}`}>
                  <div className="min-w-0 flex-1">
                    <div className="text-[15px] font-medium">{r.label}</div>
                    <div className="text-[15px] text-muted-foreground">
                      {r.size.map((x) => x.toFixed(2)).join(" × ")} m at {r.centre.map((x) => x.toFixed(2)).join(", ")} · {groupWords(r)}
                      {r.editable ? "" : " · set in the room file"}
                    </div>
                  </div>
                  {r.editable && (
                    <>
                      <Button variant="outline" disabled={busy} onClick={() => { setDraft({ ...r }); setIsNew(false); setErr(null); }}
                        className="h-11 rounded-xl bg-white px-4 text-[15px]">Change</Button>
                      <Button variant="ghost" disabled={busy} onClick={() => void remove(r.name)} aria-label={`Remove ${r.label}`}
                        className="h-11 rounded-xl px-3 text-[15px] text-rose-700 hover:bg-rose-50 hover:text-rose-800"><Trash2 aria-hidden /></Button>
                    </>
                  )}
                </li>
              ))}
            </ul>
            {!draft && (
              <Button onClick={add} disabled={busy} className="h-11 rounded-xl px-4 text-[15px]" data-testid="add-keep-out">
                <Plus aria-hidden /> Add a keep-out region
              </Button>
            )}
            {draft && (
              <div className="space-y-3 rounded-xl border border-amber-300 bg-amber-50/60 p-3" data-testid="keep-editor">
                <label className="block text-[15px]">
                  <span className="mb-1 block font-medium">Name</span>
                  <input value={draft.label} onChange={(e) => setDraft({ ...draft, label: e.target.value })} maxLength={40}
                    className="h-11 w-full rounded-xl border border-stone-300 bg-white px-3 text-[15px]" />
                </label>
                {bits.points.length > 0 && (
                  <label className="block text-[15px]">
                    <span className="mb-1 block font-medium">Put it at a place on the map</span>
                    <select defaultValue="" onChange={(e) => { const p = bits.points.find((x) => x.name === e.target.value); if (p) setDraft({ ...draft, centre: [...p.at] as Vec3 }); }}
                      className="h-11 w-full rounded-xl border border-stone-300 bg-white px-3 text-[15px]">
                      <option value="">Choose…</option>
                      {bits.points.map((p) => <option key={p.name} value={p.name}>{p.label}</option>)}
                    </select>
                  </label>
                )}
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="space-y-2">
                    <div className="text-[15px] font-medium">Where its middle is</div>
                    {AXES.map(([w, unit], i) => (
                      <div key={w} className="text-[15px]">
                        <div className="flex justify-between"><span>{w}</span><span className="tabular-nums">{draft.centre[i].toFixed(2)} {unit}</span></div>
                        <Slider min={0} max={bits.size[i]} step={0.05} value={[draft.centre[i]]} aria-label={w}
                          onValueChange={(v) => setDraft({ ...draft, centre: vec(draft.centre, i, v[0]) })} />
                      </div>
                    ))}
                  </div>
                  <div className="space-y-2">
                    <div className="text-[15px] font-medium">How big it is</div>
                    {SIZES.map((w, i) => (
                      <div key={w} className="text-[15px]">
                        <div className="flex justify-between"><span>{w}</span><span className="tabular-nums">{draft.size[i].toFixed(2)} m</span></div>
                        <Slider min={0.1} max={bits.size[i]} step={0.05} value={[draft.size[i]]} aria-label={w}
                          onValueChange={(v) => setDraft({ ...draft, size: vec(draft.size, i, v[0]) })} />
                      </div>
                    ))}
                  </div>
                </div>
                <fieldset className="text-[15px]">
                  <legend className="mb-1 font-medium">Which lights keep out (none ticked: every light)</legend>
                  <div className="flex flex-wrap gap-x-4 gap-y-1">
                    {Object.entries(bits.lightGroups).filter(([g, e]) => g !== "*" && e.members.length > 0).map(([g, e]) => (
                      <label key={g} className="inline-flex min-h-11 items-center gap-2">
                        <input type="checkbox" className="h-5 w-5" checked={draft.groups.includes(g)}
                          onChange={(ev) => setDraft({ ...draft, groups: ev.target.checked ? [...draft.groups, g] : draft.groups.filter((x) => x !== g) })} />
                        {e.label}
                      </label>
                    ))}
                  </div>
                </fieldset>
                <div className="flex flex-wrap gap-2">
                  <Button onClick={() => void save()} disabled={busy || !draft.label.trim()} className="h-11 rounded-xl px-4 text-[15px]" data-testid="save-keep-out">
                    {busy ? <Spinner /> : null} Save
                  </Button>
                  <Button variant="ghost" onClick={() => setDraft(null)} disabled={busy} className="h-11 rounded-xl px-4 text-[15px]">Cancel</Button>
                </div>
              </div>
            )}
          </>
        )}
        {err && <Notice tone="bad">{err}</Notice>}
      </section>

      {lasers.length > 0 && (
        <section className={cn(CARD_CLASS, "space-y-2 px-3 py-3 sm:px-4")} data-testid="laser-checks">
          <h2 className="text-lg font-semibold">Lasers</h2>
          <p className="text-[15px] text-muted-foreground">
            A laser's burst goes where the unit is tilted. Forward is towards the middle of the room.
          </p>
          <ul className="space-y-2">
            {lasers.map(([id, c]) => (
              <li key={id} className="rounded-xl border border-stone-200 bg-white px-3 py-2 text-[15px]" data-testid={`laser-${id}`}>
                <div className="font-medium">{nameOf(id)}</div>
                <div>Tilted {lean(c.lean_deg)}.{" "}
                  {c.crosses.length === 0 ? "Its beams cross no keep-out region." : (
                    <span className="text-rose-700">Its beams cross {c.crosses.map(regionLabel).join(" and ")}.</span>
                  )}
                </div>
                {c.crosses.length > 0 && (c.clear_lean_deg === null ? (
                  <div>No tilt clears it: move the region or the bar.</div>
                ) : (
                  <div className="mt-1 flex flex-wrap items-center gap-2">
                    <span>Tilted {lean(c.clear_lean_deg)} it is clear.</span>
                    <Button variant="outline" disabled={busy} onClick={() => void setTilt(id, c.clear_lean_deg as number)}
                      className="h-11 rounded-xl bg-white px-4 text-[15px]">Set that tilt</Button>
                    <span className="text-muted-foreground">Then tilt the real laser the same.</span>
                  </div>
                ))}
              </li>
            ))}
          </ul>
        </section>
      )}

      {hasPlanned && (
        <Button variant="outline" onClick={() => setPlanned(true)} className="h-11 rounded-xl bg-white px-4 text-[15px]">
          Place a planned light ›
        </Button>
      )}
    </div>
  );
}
