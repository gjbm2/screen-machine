// A room's effects (rooms.json "effects"), shown like the lamps above them (Fixtures.tsx: a tile per device, its
// controls under the tiles on a tap; owner, 25 Oct: "vertical space is at a HIGH PREMIUM"): devices the room state drives that aren't screens or lamps, each an on/off
// target plus setting targets (the glitterball: mirror_ball.main, then mirror_ball_motor.speed and .direction).
// Nothing about a device is written here: names, values and their words come from the targets' schemas and labels
// (GET /api/v1/room/describe: the device config and the modes.json labels), and "On in Party and Disco" from the
// modes it describes. Changes go through the room state model (PUT /api/v1/room/targets), so a change by hand and a
// mode change go through the same coordinator. Nothing reads a remote-controlled device back: the page shows what
// was last sent, and an on/off toggle can be told what the device is doing (assume). A setting is drawn from its
// schema: a switch (x-ui.switch: the pinspots' pulse, flash), a slider (a number range: their brightness), else a
// button per value (wrapping past four; a value named after a colour gets its swatch). A device with a "reset" action
// (the pinspots, after they lost power at the wall) offers it under its controls.

import { useEffect, useId, useState } from "react";
import { RotateCcw, RotateCw, Sparkles } from "lucide-react";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import { explainError, type RoomApi } from "./api";
import { Spinner } from "./bits";
import { DeviceDisc, type DeviceStatus } from "./Devices";
import { LightPanel, LightTile } from "./Fixtures";
import type { RoomDescribe, RoomStatus, SiteEffect } from "./types";
import type { NewLogEntry } from "./useRoomConnection";

/** Icons for well-known value names (presentation only). */
const VALUE_ICONS: Record<string, typeof RotateCw> = { clockwise: RotateCw, anticlockwise: RotateCcw };

type Target = RoomDescribe["targets"][string];

function split(t: string): [string, string] {
  const dot = t.indexOf(".");
  return dot < 0 ? [t, "main"] : [t.slice(0, dot), t.slice(dot + 1)];
}

/** The value's words from the schema (x-ui labels), else the value itself. */
function words(d: Target | undefined, v: unknown): string {
  const s = String(v);
  return d?.schema?.["x-ui"]?.labels?.[s] ?? s.charAt(0).toUpperCase() + s.slice(1);
}

/** On and off values: the schema's switch hint, else the default is off and the other value on. */
function onOff(d: Target | undefined): { on: string; off: string } | null {
  const sw = d?.schema?.["x-ui"]?.switch;
  if (sw) return sw;
  const e = d?.schema?.enum;
  if (!e || e.length !== 2) return null;
  const off = String(d?.schema?.default ?? e[1]);
  return { on: e.find((x) => x !== off) ?? e[0], off };
}

/** "Party and Disco": the modes that set `t` to `value`, by label, in mode order. */
function modesThatSet(describe: RoomDescribe, t: string, value: string): string {
  const names = Object.entries(describe.modes ?? {})
    .filter(([, m]) => !m.abstract && ["set", "store+set", "set+restore"].includes(m.entries?.[t]?.verb ?? "") && m.entries?.[t]?.value === value)
    .sort(([, a], [, b]) => (a.order ?? 0) - (b.order ?? 0))
    .map(([id, m]) => m.label || id);
  if (names.length <= 1) return names.join("");
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/** A value named after a colour ("Red"), as a CSS colour for its swatch; null for any other word. */
function swatch(word: string): string | null {
  const w = word.trim().toLowerCase().replace(/\s+/g, "");
  return w && typeof CSS !== "undefined" && CSS.supports("color", w) ? w : null;
}

/** A number range (a light's brightness steps): moves locally, sends once on letting go. */
function RangeRow({
  label,
  name,
  d,
  value,
  disabled,
  busy,
  onCommit,
}: {
  label: string;
  name: string;
  d: Target | undefined;
  value: unknown;
  disabled: boolean;
  busy: boolean;
  onCommit: (v: number) => void;
}) {
  const lo = d?.schema?.minimum ?? 0;
  const hi = d?.schema?.maximum ?? 100;
  const known = typeof value === "number";
  const [n, setN] = useState<number>(known ? (value as number) : hi);
  useEffect(() => {
    if (typeof value === "number") setN(value);
  }, [value]);
  const ends = d?.schema?.["x-ui"]?.ends;
  return (
    <div>
      <div className="flex items-center justify-between">
        <span className="text-base font-medium">{label}</span>
        <span className="flex items-center gap-2 font-mono text-[15px] tabular-nums text-stone-700">
          {busy && <Spinner />}
          {known || n !== hi ? `${n} of ${hi}` : "Not sure"}
        </span>
      </div>
      <div className="mt-1 flex items-center gap-3">
        {ends?.min && <span className="shrink-0 text-sm text-muted-foreground">{ends.min}</span>}
        <Slider
          aria-label={`${name} ${label.toLowerCase()}`}
          className="flex-1 py-3 [&_[role=slider]]:h-6 [&_[role=slider]]:w-6"
          min={lo}
          max={hi}
          step={1}
          value={[n]}
          disabled={disabled}
          onValueChange={([v]) => setN(v)}
          onValueCommit={([v]) => onCommit(v)}
        />
        {ends?.max && <span className="shrink-0 text-sm text-muted-foreground">{ends.max}</span>}
      </div>
    </div>
  );
}

/** A setting's row heading: its label without the device's name ("Glitterball speed" -> "Speed"). */
function rowLabel(label: string | undefined, name: string, fallback: string): string {
  const l = (label ?? fallback).trim();
  const rest = l.toLowerCase().startsWith(name.toLowerCase()) ? l.slice(name.length).trim() : l;
  return rest ? rest.charAt(0).toUpperCase() + rest.slice(1) : l;
}

function EffectCard({
  effect,
  status,
  describe,
  api,
  connected,
  addLog,
  reportError,
  open,
  onToggle,
}: {
  effect: SiteEffect;
  status: RoomStatus;
  describe: RoomDescribe;
  api: RoomApi;
  connected: boolean;
  addLog: (e: NewLogEntry) => void;
  reportError: (e: unknown) => void;
  open: boolean;
  onToggle: () => void;
}) {
  const panelId = useId();
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  const target = effect.power;
  const [sub, member] = split(target);
  const d = describe.targets?.[target];
  const s = status.subsystems?.[sub];
  const vals = onOff(d);
  if (!s || !d || !vals) return null;
  const m = s.members?.[member];
  const name = d.label || sub;
  const onWord = words(d, vals.on);
  const offWord = words(d, vals.off);

  // No adapter (a placeholder) or no codes learned yet (dry run): listed, but nothing can be sent.
  const setUp = !s.dry_run && s.last_error !== "no adapter";
  const reachable = s.available !== false;
  const known = !!m && m.confidence !== undefined && m.confidence !== "unknown";
  const on = m?.believed === vals.on;
  const isToggle = s.kind === "edge_toggle";
  const toggleUnknown = isToggle && !known;
  const disabled = !connected || !setUp || !reachable || !!busy;
  const onIn = modesThatSet(describe, target, vals.on);

  let pill: DeviceStatus;
  if (!setUp) pill = { tone: "muted", text: "Not set up yet" };
  else if (!reachable) pill = { tone: "bad", text: "Can't reach it" };
  else if (busy) pill = { tone: "info", text: "Sending…" };
  else if (!known && m?.believed == null) pill = { tone: "warn", text: "Not sure" };
  else pill = on ? { tone: "live", text: onWord } : { tone: "muted", text: offWord };

  const run = async (what: string, fn: () => Promise<unknown>) => {
    setBusy(what);
    setErr(null);
    try {
      await fn();
      addLog({ type: "effect", level: "info", summary: `${name}: ${what}` });
    } catch (e) {
      reportError(e);
      setErr(explainError(e));
    } finally {
      setBusy(null);
    }
  };

  const showQuestion = setUp && reachable && isToggle && (toggleUnknown || asking);   // inside the unrolled row
  const isRange = (x: Target | undefined) => typeof x?.schema?.minimum === "number" && typeof x?.schema?.maximum === "number";
  const settings = effect.settings
    .map((t) => ({ t, d: describe.targets?.[t], s: status.subsystems?.[split(t)[0]] }))
    .filter((x) => (x.d?.schema?.enum?.length || isRange(x.d)) && x.s && !x.s.dry_run && x.s.last_error !== "no adapter");
  const canReset = setUp && !!describe.subsystems?.[sub]?.actions?.reset;

  const summary = !setUp ? "Its remote codes aren't learned yet." : onIn ? `${onWord} in ${onIn}.` : `${offWord} unless you turn it on.`;
  return (
    <>
      <LightTile
        testId={`effect-${target}`}
        visual={
          <DeviceDisc active={on && setUp} className="h-7 w-7">
            <Sparkles className={cn("h-4 w-4", on && setUp && "motion-safe:animate-pulse")} />
          </DeviceDisc>
        }
        name={name}
        status={{ ...pill, pulse: pill.tone === "live" }}
        hint={summary}
        open={open}
        controls={panelId}
        onToggle={onToggle}
      />
      {open && (
        <LightPanel id={panelId} name={name} summary={summary}>
          <div className="flex min-h-11 items-center justify-between gap-3">
            <span className="text-base font-medium">On</span>
            {busy === "on" || busy === "off" ? (
              <Spinner className="shrink-0" />
            ) : (
              <Switch
                aria-label={`${name} on`}
                checked={on}
                disabled={disabled}
                onCheckedChange={(v) => void run(v ? "on" : "off", () => api.setTargets({ [target]: v ? vals.on : vals.off }))}
              />
            )}
          </div>

          {showQuestion && (
            <div className="space-y-2 rounded-xl bg-amber-50/60 p-3">
              <p className="text-base">Is it {onWord.toLowerCase()} right now?</p>
              <div className="grid grid-cols-2 gap-2">
                {[
                  [vals.on, `Yes, ${onWord.toLowerCase()}`],
                  [vals.off, `No, ${offWord.toLowerCase()}`],
                ].map(([v, w]) => (
                  <button
                    key={v}
                    type="button"
                    disabled={!connected || !!busy}
                    onClick={() =>
                      void run(`it's ${words(d, v).toLowerCase()}`, async () => {
                        await api.assume(target, v);
                        setAsking(false);
                      })
                    }
                    className="min-h-11 rounded-xl border border-stone-200 bg-white px-3 text-base hover:bg-stone-50 disabled:opacity-50"
                  >
                    {w}
                  </button>
                ))}
              </div>
              <p className="text-sm text-muted-foreground">This only tells the room; nothing is sent.</p>
            </div>
          )}

          {settings.map(({ t, d: sd, s: ss }) => {
            const label = rowLabel(sd?.label, name, split(t)[1]);
            const cur = ss?.members?.[split(t)[1]]?.believed;
            const options = sd?.schema?.enum ?? [];
            const off = !connected || ss?.available === false || !!busy;
            if (isRange(sd)) {
              const what = label.toLowerCase();
              return (
                <RangeRow
                  key={t}
                  label={label}
                  name={name}
                  d={sd}
                  value={cur}
                  disabled={off}
                  busy={busy === what}
                  onCommit={(v) => void run(what, () => api.setTargets({ [t]: v }))}
                />
              );
            }
            const sw = sd?.schema?.["x-ui"]?.switch;
            if (sw) {
              const what = `${label.toLowerCase()} ${cur === sw.on ? "off" : "on"}`;
              return (
                <div key={t} className="flex min-h-11 items-center justify-between gap-3">
                  <span className="text-base font-medium">{label}</span>
                  {busy?.startsWith(`${label.toLowerCase()} `) ? (
                    <Spinner className="shrink-0" />
                  ) : (
                    <Switch
                      aria-label={`${name} ${label.toLowerCase()}`}
                      checked={cur === sw.on}
                      disabled={off}
                      onCheckedChange={(v) => void run(what, () => api.setTargets({ [t]: v ? sw.on : sw.off }))}
                    />
                  )}
                </div>
              );
            }
            return (
              <div key={t}>
                <span className="text-base font-medium">{label}</span>
                <div
                  role="radiogroup"
                  aria-label={`${name} ${label.toLowerCase()}`}
                  className="mt-2 grid gap-2"
                  style={{ gridTemplateColumns: `repeat(${Math.min(options.length, 4)}, minmax(0, 1fr))` }}
                >
                  {options.map((v) => {
                    const sel = cur === v;
                    const Icon = VALUE_ICONS[v];
                    const hue = swatch(words(sd, v));
                    const what = `${label.toLowerCase()} ${words(sd, v).toLowerCase()}`;
                    return (
                      <button
                        key={v}
                        type="button"
                        role="radio"
                        aria-checked={sel}
                        disabled={!connected || ss?.available === false || !!busy}
                        onClick={() => void run(what, () => api.setTargets({ [t]: v }))}
                        className={cn(
                          "flex min-h-11 items-center justify-center gap-1.5 rounded-xl border px-2 text-[15px] transition-colors disabled:opacity-60",
                          sel ? "border-teal-300 bg-teal-50 font-semibold text-teal-900" : "border-stone-200 bg-white hover:bg-stone-50",
                        )}
                      >
                        {busy === what ? (
                          <Spinner />
                        ) : Icon ? (
                          <Icon className="h-4 w-4 shrink-0" aria-hidden />
                        ) : (
                          hue && <span aria-hidden className="h-3.5 w-3.5 shrink-0 rounded-full border border-stone-300" style={{ background: hue }} />
                        )}
                        <span className="truncate">{words(sd, v)}</span>
                      </button>
                    );
                  })}
                </div>
              </div>
            );
          })}

          {setUp && reachable && isToggle && known && !asking && (
            <button
              type="button"
              onClick={() => setAsking(true)}
              className="min-h-9 text-sm text-stone-600 underline underline-offset-2 hover:text-stone-900"
            >
              Not what it's doing? Tell the room
            </button>
          )}

          {canReset && (
            <button
              type="button"
              disabled={!connected || !!busy}
              onClick={() =>
                void run("reset", async () => {
                  const dev = (await api.remotes())?.remotes.find((r) => r.subsystems.includes(sub));
                  if (!dev) throw new Error("The room doesn't know this device's remote.");
                  await api.resetLight(dev.id);
                })
              }
              className="flex min-h-9 items-center gap-2 text-sm text-stone-600 underline underline-offset-2 hover:text-stone-900 disabled:opacity-50"
            >
              {busy === "reset" && <Spinner />} Was it off at the wall? Reset it (dims right down, then off)
            </button>
          )}

          {setUp && !reachable && s.last_error && <p className="text-sm text-rose-700">{s.last_error}</p>}
          {err && <p className="text-sm text-rose-700">{err}</p>}
        </LightPanel>
      )}
    </>
  );
}

export function EffectsSection({
  effects,
  status,
  describe,
  api,
  connected,
  addLog,
  reportError,
}: {
  /** The room's device cards (rooms.json); ones the room doesn't have are left out. */
  effects: SiteEffect[];
  status: RoomStatus;
  describe: RoomDescribe | null;
  api: RoomApi;
  connected: boolean;
  addLog: (e: NewLogEntry) => void;
  reportError: (e: unknown) => void;
}) {
  const [openId, setOpenId] = useState<string | null>(null);
  const shown = effects.filter((e) => !!status.subsystems?.[split(e.power)[0]] && !!describe?.targets?.[e.power]);
  if (!describe || !shown.length) return null;
  // tiles like the lamps', two across (its name has room; no heading: its name says what it is)
  return (
    <section aria-label="Room effects" className="grid grid-cols-2 gap-2">
      {shown.map((e) => (
        <EffectCard
          key={e.power}
          effect={e}
          status={status}
          describe={describe}
          api={api}
          connected={connected}
          addLog={addLog}
          reportError={reportError}
          open={openId === e.power}
          onToggle={() => setOpenId((o) => (o === e.power ? null : e.power))}
        />
      ))}
    </section>
  );
}
