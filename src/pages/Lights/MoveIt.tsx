// Move it (docs/LIGHTS.md §8.3): part of adding or editing a light, not an expert screen. While it is open the show
// leaves this light alone (POST .../jog {} holds it; closing hands it back), and the person drives the real light:
//   - the pad turns and tilts a moving light; the controls below set what else it can do (lib.controlsFor its caps);
//   - "Store it": Set as home, Set limits (two taps per axis, nothing by default), Save as… a named position,
//     Point it at… places on the Room map so it learns where it hangs (3 places), Keep this beam as its normal look;
//   - the preview shows where its beam lands and the flow the show is following.
// show_service hands it back after lights.jog.idle_s with no moves; from 15 s before, the banner counts down with
// "Keep it". Moves go through jog.ts (latest wins, one in flight, at most send_hz). Anything stored waits for the last
// move to land first, because show_service reads "here" from what it holds.

import { useCallback, useEffect, useRef, useState } from "react";
import { Crosshair, Home, MapPin, RotateCcw, Save, SlidersHorizontal, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { Notice, Spinner } from "@/pages/Room/bits";
import { TAP_BUTTON_CLASS } from "@/pages/Room/theme";
import type { Tone } from "@/pages/Room/tones";
import { explainLightsError, type FitResult, type JogResult, type LightsApi, type TeachResult } from "./api";
import { CapControls } from "./CapControls";
import { JogPad } from "./JogPad";
import { JogSender } from "./jog";
import {
  hasBeam,
  holdWords,
  limitWords,
  nameWords,
  parseSetup,
  poseWords,
  problemWords,
  type Axis,
  type HoldState,
  type LightRow,
  type LightsUi,
  type PanTilt,
  type Sample,
  type Setup,
  type Target,
} from "./lib";
import { Preview } from "./Preview";
import { useLive, useSpace } from "./useRig";

const CHIP =
  "inline-flex min-h-11 items-center gap-1.5 rounded-full border px-3.5 text-[15px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
const CHIP_OFF = "border-stone-200 bg-white text-stone-700 hover:bg-stone-50";
const ACTION = "h-11 justify-start rounded-xl bg-white px-3 text-[15px]";
const TEACH_NEEDS = 3;

type Panel = null | "limits" | "save" | "point";
type JogMsg = { values: Record<string, unknown>; free: boolean };

function samplesFrom(raw: unknown): Sample[] {
  return Array.isArray(raw)
    ? raw.flatMap((s) =>
        s && typeof s === "object" && typeof (s as Sample).pan === "number" && typeof (s as Sample).tilt === "number"
          ? [{ pan: (s as Sample).pan, tilt: (s as Sample).tilt, point: typeof (s as Sample).point === "string" ? (s as Sample).point : null }]
          : [],
      )
    : [];
}

export function MoveIt({
  api,
  row,
  ui,
  onChanged,
}: {
  api: LightsApi;
  row: LightRow;
  ui: LightsUi;
  /** Something about the light was kept: read the rig again. */
  onChanged?: () => void;
}) {
  const id = row.id;
  const caps = row.caps;
  const moves = !!caps?.moves;
  const [hold, setHold] = useState<HoldState>("starting");
  const [why, setWhy] = useState<string | null>(null);
  const [deadline, setDeadline] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [pos, setPos] = useState<PanTilt>(() => row.setup.home ?? { pan: 0, tilt: 0 });
  const [setup, setSetup] = useState<Setup>(row.setup);
  const [fine, setFine] = useState(false);
  const [panel, setPanel] = useState<Panel>(null);
  const [note, setNote] = useState<{ tone: Tone; text: string } | null>(null);
  const [working, setWorking] = useState(false);
  const sp = useSpace(api, moves);
  const live = useLive(api, moves);
  const free = panel === "limits";
  const freeRef = useRef(free);
  freeRef.current = free;

  const onResult = useCallback(
    (res: JogResult | undefined) => {
      if (!res) return;
      if (res.held) {
        setHold("holding");
        setWhy(null);
        setDeadline(Date.now() + (typeof res.idle_s === "number" ? res.idle_s : ui.idle_s) * 1000);
        setNow(Date.now());
      } else {
        setHold(res.why ? "cant" : "released");
        setWhy(res.why ?? null);
        setDeadline(null);
      }
    },
    [ui.idle_s],
  );
  const onResultRef = useRef(onResult);
  onResultRef.current = onResult;

  // One sender per hold (made and stopped with it, so a remount never keeps a stopped one).
  const sender = useRef<JogSender<JogMsg> | null>(null);
  const hzRef = useRef(ui.send_hz);
  hzRef.current = ui.send_hz;
  useEffect(() => sender.current?.setHz(ui.send_hz), [ui.send_hz]);
  const whenIdle = useCallback(() => sender.current?.whenIdle() ?? Promise.resolve(), []);

  const adopt = useCallback((res: JogResult) => {
    onResultRef.current(res);
    const t = res?.target ?? {};
    if (typeof t.pan === "number" && typeof t.tilt === "number") setPos({ pan: t.pan, tilt: t.tilt });
    const rest = Object.fromEntries(Object.entries(t).filter(([k]) => k !== "pan" && k !== "tilt"));
    setValues((v) => ({ ...rest, ...v }));
  }, []);

  // Hold it while open; hand it back on the way out.
  useEffect(() => {
    let alive = true;
    const s = new JogSender<JogMsg>(
      (m) =>
        api.jog(id, m.values, { free: m.free }).then(
          (res) => alive && onResultRef.current(res),
          (e) => alive && setNote({ tone: "bad", text: explainLightsError(e) }),
        ),
      { hz: hzRef.current, merge: (a, b) => ({ values: { ...a.values, ...b.values }, free: b.free }) },
    );
    sender.current = s;
    api.jog(id, {}).then(
      (res) => alive && adopt(res),
      (e) => {
        if (!alive) return;
        setHold("cant");
        setWhy(explainLightsError(e));
      },
    );
    return () => {
      alive = false;
      s.stop();
      if (sender.current === s) sender.current = null;
      void api.jog(id, {}, { release: true }).catch(() => {});
    };
  }, [api, id, adopt]);

  // The hand-back countdown.
  useEffect(() => {
    if (hold !== "holding") return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [hold]);
  const leftS = hold === "holding" && deadline !== null ? (deadline - now) / 1000 : null;
  useEffect(() => {
    if (leftS !== null && leftS <= 0) setHold("released");
  }, [leftS]);

  const send = (vals: Record<string, unknown>) => sender.current?.push({ values: vals, free: freeRef.current });
  const setAttr = (attr: string, v: unknown) => {
    setValues((x) => ({ ...x, [attr]: v }));
    send({ [attr]: v });
  };
  const moveTo = (p: PanTilt) => {
    setPos(p);
    send({ pan: p.pan, tilt: p.tilt });
  };
  const regrab = () => {
    setHold("starting");
    api.jog(id, {}).then(adopt, (e) => {
      setHold("cant");
      setWhy(explainLightsError(e));
    });
  };

  /** POST .../setup once the last move has landed; the answer is the light's whole setup. */
  const keep = async (body: Record<string, unknown>, done?: string): Promise<Setup | null> => {
    setWorking(true);
    try {
      await whenIdle();
      const res = await api.setup(id, body);
      const next = parseSetup(res?.setup);
      setSetup(next);
      const probs = problemWords(res?.problems);
      setNote(probs.length ? { tone: "warn", text: probs.join(" ") } : done ? { tone: "ok", text: done } : null);
      onChanged?.();
      return next;
    } catch (e) {
      setNote({ tone: "bad", text: explainLightsError(e) });
      return null;
    } finally {
      setWorking(false);
    }
  };

  const banner = holdWords(hold, leftS, why);
  const title = moves ? null : "Check it lights";

  return (
    <div className="space-y-4" data-testid="move-it">
      {title && <h4 className="text-[17px] font-semibold">{title}</h4>}
      {hold === "released" ? (
        <button type="button" onClick={regrab} className="w-full rounded-xl border border-stone-200 bg-stone-50 px-3 py-2.5 text-left text-[15px]" data-testid="hold-banner">
          {banner.text}
        </button>
      ) : (
        <div
          role="status"
          className={cn("flex flex-wrap items-center gap-2 rounded-xl border px-3 py-2.5 text-[15px]", banner.warn ? "border-amber-200 bg-amber-50 text-amber-900" : "border-teal-200 bg-teal-50 text-teal-900")}
          data-testid="hold-banner"
        >
          {hold === "starting" && <Spinner />}
          <span className="min-w-0 flex-1">{banner.text}</span>
          {hold === "holding" && banner.warn && (
            <Button variant="outline" className="h-10 rounded-xl bg-white px-3 text-[15px]" onClick={() => send({})}>
              Keep it
            </Button>
          )}
        </div>
      )}

      {moves && (
        <JogPad
          pos={pos}
          caps={caps}
          setup={setup}
          ui={ui}
          fine={fine}
          free={free}
          onFine={setFine}
          onMove={moveTo}
          onFlip={(axis, flipped) => void keep({ jog_flip: { ...setup.jog_flip, [axis]: flipped } })}
        />
      )}

      <CapControls
        caps={caps}
        values={values}
        onChange={setAttr}
        onRename={(wheel, slot, name) => {
          const hex = wheel === "colour" ? caps?.colour_slots.find((s) => s.slot === slot)?.hex ?? null : null;
          void keep({ slots: { [wheel]: { [String(slot)]: wheel === "colour" ? { name, ...(hex ? { hex } : {}) } : name } } }, "Renamed.");
        }}
      />

      {moves && (
        <section className="space-y-2">
          <h4 className="text-[15px] font-semibold">Store it</h4>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <Button variant="outline" className={ACTION} disabled={working} onClick={() => void keep({ home: "here" }, "Saved as its home: it rests here when the show isn't moving it.")}>
              <Home aria-hidden /> Set as home
            </Button>
            <Button variant="outline" className={cn(ACTION, panel === "limits" && "border-teal-300 bg-teal-50")} aria-expanded={panel === "limits"} onClick={() => setPanel(panel === "limits" ? null : "limits")}>
              <SlidersHorizontal aria-hidden /> Set limits
            </Button>
            <Button variant="outline" className={cn(ACTION, panel === "save" && "border-teal-300 bg-teal-50")} aria-expanded={panel === "save"} onClick={() => setPanel(panel === "save" ? null : "save")}>
              <Save aria-hidden /> Save as…
            </Button>
            <Button variant="outline" className={cn(ACTION, panel === "point" && "border-teal-300 bg-teal-50")} aria-expanded={panel === "point"} onClick={() => setPanel(panel === "point" ? null : "point")}>
              <Crosshair aria-hidden /> Point it at…
            </Button>
            {hasBeam(caps) && (
              <Button variant="outline" className={cn(ACTION, "sm:col-span-2")} disabled={working} onClick={() => void keep({ beam: "here" }, "Kept as its normal beam.")}>
                Keep this beam as its normal look
              </Button>
            )}
          </div>
          {panel === "limits" && <LimitsPanel setup={setup} pos={pos} working={working} keep={keep} />}
          {panel === "save" && (
            <SavePanel
              api={api}
              id={id}
              setup={setup}
              targets={sp.space?.targets ?? []}
              whenIdle={whenIdle}
              onSetup={(s) => {
                setSetup(s);
                onChanged?.();
              }}
              onGo={moveTo}
              onNote={setNote}
              onPlaced={sp.reload}
            />
          )}
          {panel === "point" && (
            <PointPanel api={api} id={id} sp={sp} whenIdle={whenIdle} onNote={setNote} onTaught={() => sp.reload()} />
          )}
        </section>
      )}

      {note && (
        <Notice tone={note.tone} className="text-[15px]">
          {note.text}
        </Notice>
      )}

      {moves && <Preview fid={id} space={sp.space} live={live} />}
    </div>
  );
}

function LimitsPanel({
  setup,
  pos,
  working,
  keep,
}: {
  setup: Setup;
  pos: PanTilt;
  working: boolean;
  keep: (body: Record<string, unknown>, done?: string) => Promise<Setup | null>;
}) {
  // After the first "stop here" on an axis, the second sets the other end (so two taps make the span between them).
  const [second, setSecond] = useState<Partial<Record<Axis, "low" | "high">>>({});
  const stopHere = async (axis: Axis) => {
    const end = second[axis];
    const next = await keep({ limit: { axis, end: end ?? "here" } });
    const lim = next?.limits[axis];
    if (!next || !lim) return;
    if (end) {
      setSecond(({ [axis]: _done, ...rest }) => rest);
      return;
    }
    const moved = Math.abs(lim[0] - pos[axis]) <= Math.abs(lim[1] - pos[axis]) ? "low" : "high";
    setSecond((s) => ({ ...s, [axis]: moved === "low" ? "high" : "low" }));
  };
  return (
    <div className="space-y-3 rounded-xl border border-stone-200 bg-white p-3" data-testid="limits-panel">
      <p className="text-[15px] text-muted-foreground">
        While this is open it moves past its limits. Take it to one end of where it may go and tap Stop here, then to the other end and tap again.
      </p>
      {(["pan", "tilt"] as Axis[]).map((axis) => (
        <div key={axis} className="space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" className="h-11 rounded-xl bg-white px-3 text-[15px]" disabled={working} onClick={() => void stopHere(axis)}>
              {axis === "pan" ? "Turn: stop here" : "Tilt: stop here"}
            </Button>
            <span className="text-[15px]">{limitWords(setup, axis)}</span>
          </div>
          {second[axis] && <p className="text-sm text-teal-800">Now {axis === "pan" ? "turn" : "tilt"} it to the other end and tap Stop here again.</p>}
        </div>
      ))}
      <Button
        variant="ghost"
        className="h-11 rounded-xl px-3 text-[15px] text-rose-700 hover:bg-rose-50"
        disabled={working || (!setup.limits.pan && !setup.limits.tilt)}
        onClick={() => {
          setSecond({});
          void keep({ limits: null }, "No limits: it may go anywhere it can.");
        }}
      >
        No limits
      </Button>
    </div>
  );
}

function SavePanel({
  api,
  id,
  setup,
  targets,
  whenIdle,
  onSetup,
  onGo,
  onNote,
  onPlaced,
}: {
  api: LightsApi;
  id: string;
  setup: Setup;
  targets: Target[];
  /** Resolves once the last move has landed. */
  whenIdle: () => Promise<void>;
  onSetup: (s: Setup) => void;
  onGo: (p: PanTilt) => void;
  onNote: (n: { tone: Tone; text: string } | null) => void;
  onPlaced: () => void;
}) {
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);
  const labelOf = (n: string) => targets.find((t) => t.name === n)?.label ?? nameWords(n);
  const suggestions = targets.filter((t) => (t.kind === "point" || t.kind === "region") && !setup.positions[t.name]).slice(0, 8);
  const save = async (n: string, label?: string) => {
    if (!n.trim()) return;
    setSaving(true);
    try {
      await whenIdle();
      const res = await api.savePosition(id, n.trim(), label);
      onSetup({ ...setup, positions: parseSetup({ positions: res?.positions }).positions });
      const probs = problemWords(res?.problems);
      onNote(
        res?.point
          ? { tone: "ok", text: `Saved. It's on the Room map as a place now, so every calibrated light can point there.` }
          : probs.length
            ? { tone: "warn", text: `Saved. ${probs.join(" ")}` }
            : { tone: "ok", text: "Saved." },
      );
      if (res?.point) onPlaced();
      setName("");
    } catch (e) {
      onNote({ tone: "bad", text: explainLightsError(e) });
    } finally {
      setSaving(false);
    }
  };
  const saved = Object.entries(setup.positions);
  return (
    <div className="space-y-3 rounded-xl border border-stone-200 bg-white p-3" data-testid="save-panel">
      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void save(name);
        }}
      >
        <Input aria-label="Name this position" placeholder="Over the piano" value={name} onChange={(e) => setName(e.target.value)} className="h-11 rounded-xl bg-white text-base" />
        <Button type="submit" className={TAP_BUTTON_CLASS} disabled={!name.trim() || saving}>
          {saving && <Spinner />} Save
        </Button>
      </form>
      {suggestions.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {suggestions.map((t) => (
            <button key={t.name} type="button" className={cn(CHIP, CHIP_OFF)} disabled={saving} onClick={() => void save(t.name, t.label)}>
              <MapPin className="h-4 w-4" aria-hidden /> {t.label}
            </button>
          ))}
        </div>
      )}
      {saved.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-sm text-muted-foreground">Saved positions (tap one to go there)</p>
          <div className="flex flex-wrap gap-2">
            {saved.map(([n, p]) => (
              <span key={n} className="inline-flex items-center rounded-full border border-stone-200 bg-white">
                <button type="button" className="min-h-11 rounded-l-full px-3.5 text-[15px] font-medium" onClick={() => onGo(p)}>
                  {labelOf(n)}
                </button>
                <button
                  type="button"
                  aria-label={`Forget ${labelOf(n)}`}
                  className="grid h-11 w-10 place-items-center rounded-r-full text-muted-foreground hover:bg-stone-100"
                  onClick={async () => {
                    try {
                      const res = await api.removePosition(id, n);
                      onSetup({ ...setup, positions: parseSetup({ positions: res?.positions }).positions });
                    } catch (e) {
                      onNote({ tone: "bad", text: explainLightsError(e) });
                    }
                  }}
                >
                  <X className="h-4 w-4" aria-hidden />
                </button>
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function PointPanel({
  api,
  id,
  sp,
  whenIdle,
  onNote,
  onTaught,
}: {
  api: LightsApi;
  id: string;
  sp: ReturnType<typeof useSpace>;
  whenIdle: () => Promise<void>;
  onNote: (n: { tone: Tone; text: string } | null) => void;
  onTaught: () => void;
}) {
  const space = sp.space;
  const [aiming, setAiming] = useState<Target | null>(null);
  const [samples, setSamples] = useState<Sample[] | null>(null);
  const [fit, setFit] = useState<FitResult | null>(null);
  const [busy, setBusy] = useState(false);
  const known = samples ?? space?.appliances[id]?.teach ?? [];
  const pose = space?.appliances[id]?.pose ?? null;
  const targets = (space?.targets ?? []).filter((t) => t.name !== id && t.at);
  const labelOf = (n: string | null) => (n ? targets.find((t) => t.name === n)?.label ?? nameWords(n) : "A place");

  const answer = (res: TeachResult | undefined) => {
    const s = samplesFrom(res?.samples);
    setSamples(s);
    setFit(res?.fit ?? null);
    const probs = problemWords(res?.problems);
    if (probs.length) onNote({ tone: "warn", text: probs.join(" ") });
    onTaught();
  };
  const run = async (fn: () => Promise<TeachResult>) => {
    setBusy(true);
    try {
      answer(await fn());
    } catch (e) {
      onNote({ tone: "bad", text: explainLightsError(e) });
    } finally {
      setBusy(false);
    }
  };

  const words =
    known.length === 0
      ? poseWords(null, 0)
      : known.length < TEACH_NEEDS
        ? `Learnt ${known.length} of ${TEACH_NEEDS}.`
        : fit?.words ?? poseWords(pose, known.length);
  const tone: Tone = fit && (fit.level === "rough" || fit.level === "bad") ? "warn" : known.length >= TEACH_NEEDS ? "ok" : "info";

  if (!space) {
    return sp.error ? <Notice tone="warn" className="text-[15px]">{sp.error}</Notice> : <p className="flex items-center gap-2 text-[15px] text-muted-foreground"><Spinner /> Reading the room…</p>;
  }
  return (
    <div className="space-y-3 rounded-xl border border-stone-200 bg-white p-3" data-testid="point-panel">
      {aiming ? (
        <div className="space-y-2">
          <p className="text-[15px] font-medium">Put the middle of the beam on {aiming.label}, then tap Done.</p>
          <div className="flex gap-2">
            <Button
              className={TAP_BUTTON_CLASS}
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  await whenIdle();
                  const res = await api.teach(id, aiming.name);
                  setAiming(null);
                  return res;
                })
              }
            >
              {busy && <Spinner />} Done
            </Button>
            <Button variant="outline" className={cn(TAP_BUTTON_CLASS, "bg-white")} onClick={() => setAiming(null)}>
              Cancel
            </Button>
          </div>
        </div>
      ) : targets.length === 0 ? (
        <p className="text-[15px] text-muted-foreground">Add places on the Room map first (the piano, the mirror ball, a corner): then point it at 3 of them.</p>
      ) : (
        <div className="space-y-1.5">
          <p className="text-[15px]">Pick a place, point the beam at it, then tap Done. Three places far apart are enough.</p>
          <div className="flex flex-wrap gap-2">
            {targets.map((t) => (
              <button key={t.name} type="button" className={cn(CHIP, CHIP_OFF)} onClick={() => setAiming(t)}>
                <MapPin className="h-4 w-4" aria-hidden /> {t.label}
              </button>
            ))}
          </div>
        </div>
      )}
      <Notice tone={tone} className="text-[15px]">
        {words}
      </Notice>
      {known.length > 0 && (
        <div className="space-y-1">
          <ol className="space-y-1">
            {known.map((s, i) => (
              <li key={`${i}-${s.point}`} className={cn("flex items-center gap-2 text-[15px]", fit?.worst_index === i && known.length >= TEACH_NEEDS && "text-amber-800")}>
                <span className="min-w-0 flex-1">
                  {i + 1}. {labelOf(s.point)}
                  {fit?.worst_index === i && known.length >= TEACH_NEEDS && fit.level !== "good" ? " (fits worst)" : ""}
                </span>
                <Button variant="ghost" size="icon" className="h-10 w-10 rounded-full" aria-label={`Forget ${labelOf(s.point)}`} disabled={busy} onClick={() => void run(() => api.forget(id, i))}>
                  <X className="h-4 w-4" aria-hidden />
                </Button>
              </li>
            ))}
          </ol>
          <Button variant="ghost" className="h-10 rounded-xl px-3 text-[15px] text-rose-700 hover:bg-rose-50" disabled={busy} onClick={() => void run(() => api.forget(id))}>
            <RotateCcw aria-hidden /> Start again
          </Button>
        </div>
      )}
    </div>
  );
}

