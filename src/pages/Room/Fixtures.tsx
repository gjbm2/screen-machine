// The room's lights. Hue lamps (room_service dmx/api.py): each one device that either follows the music
// (Art-Net, streamed to the bridge) or is under house control. DMX lights (show_service's rig, registered on
// the Lights setup page, read through room_service's /show pass-through): each follows the music or is off.
// Both are set through the room state model (PUT /api/v1/room/targets: hue.<id>, light.<id>), so a mode
// change and a hand change go through the same coordinator.

import { useCallback, useEffect, useRef, useState } from "react";
import { Lightbulb, Moon, Radio, Sparkles } from "lucide-react";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import { explainError, RoomApiError, type RoomApi } from "./api";
import { Pill, Spinner } from "./bits";
import { DeviceList, DeviceRow, DeviceSection } from "./Devices";
import type { Tone } from "./tones";
import type { Fixture, HueValue } from "./types";
import { type FixturesState, useFixtures } from "./useFixtures";
import type { NewLogEntry } from "./useRoomConnection";

const STATUS_TONE: Record<string, Tone> = {
  following: "live",
  armed: "info",
  house: "muted",
  off: "muted",
  taken_over: "warn",
  error: "bad",
  unreachable: "bad",
  unavailable: "bad",
};

/** Quick colours for a lamp under house control. */
const PRESETS: { name: string; hex: string }[] = [
  { name: "Warm", hex: "#ffc367" },
  { name: "Soft white", hex: "#ffe2b8" },
  { name: "Daylight", hex: "#f4f6ff" },
  { name: "Amber", hex: "#ff9a2e" },
  { name: "Red", hex: "#ff2a1a" },
  { name: "Pink", hex: "#ff4fa0" },
  { name: "Purple", hex: "#8a3dff" },
  { name: "Blue", hex: "#2f6bff" },
  { name: "Teal", hex: "#17c3b2" },
  { name: "Green", hex: "#3ddc5a" },
];


function placementText(f: Fixture): string | null {
  if (!f.screen) return null;
  const screen = f.screen.replace(/-screen$/, "");
  if (f.placement === "above") return `Above the ${screen} screen`;
  if (f.placement === "opposite") return `Opposite the ${screen} screen`;
  return `By the ${screen} screen`;
}

function lampName(f: Fixture): string {
  return f.label.replace(/^Drawing room\s*/i, "") ? `${f.label.replace(/^Drawing room\s*/i, "")} lamp` : f.label;
}

/** What the lamp is showing now: the show's colour while following, else the bridge's reading. */
function swatch(f: Fixture): { hex: string | null; dim: number } {
  if (f.status === "following") {
    const level = typeof f.dmx?.level === "number" ? (f.dmx.level as number) : 1;
    return { hex: f.dmx_hex, dim: level };
  }
  const l = f.light;
  if (!l || !l.on || !l.hex) return { hex: null, dim: 0 };
  return { hex: l.hex, dim: Math.max(0.25, (l.bri ?? 100) / 100) };
}

function Swatch({ hex, dim, size = "h-11 w-11" }: { hex: string | null; dim: number; size?: string }) {
  return (
    <span
      aria-hidden
      className={cn("relative inline-block shrink-0 rounded-full border border-stone-300/80 bg-stone-200", size)}
      style={
        hex
          ? { background: hex, opacity: 0.35 + 0.65 * Math.min(1, Math.max(0, dim)), boxShadow: `0 0 14px -2px ${hex}` }
          : undefined
      }
    />
  );
}

function stateOf(f: Fixture): { on: boolean; bri: number; hex: string | null } {
  const v = (f.light?.value ?? f.house_value) as HueValue | undefined;
  if (v && typeof v === "object") return { on: v.on, bri: v.bri, hex: f.light?.hex ?? null };
  return { on: false, bri: f.light?.bri ?? 50, hex: f.light?.hex ?? null };
}

function LampRow({
  f,
  api,
  connected,
  addLog,
  reportError,
  onChanged,
}: {
  f: Fixture;
  api: RoomApi;
  connected: boolean;
  addLog: (e: NewLogEntry) => void;
  reportError: (e: unknown) => void;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const st = stateOf(f);
  const [bri, setBri] = useState(st.bri);
  const dragging = useRef(false);
  useEffect(() => {
    if (!dragging.current) setBri(st.bri);
  }, [st.bri]);

  const target = f.target ?? `hue.${f.id.split(".")[1]}`;
  const following = f.control === "dmx";
  const disabled = !connected || !f.writable || !!busy;

  const send = useCallback(
    async (what: string, value: HueValue) => {
      setBusy(what);
      setErr(null);
      try {
        await api.setTargets({ [target]: value });
        addLog({ type: "fixture", level: "info", summary: `${f.label}: ${what}` });
        onChanged();
      } catch (e) {
        reportError(e);
        setErr(explainError(e));
      } finally {
        setBusy(null);
      }
    },
    [api, target, f.label, addLog, reportError, onChanged],
  );

  const house = (): HueValue => {
    const hv = f.house_value;
    return hv && typeof hv === "object" ? hv : { on: true, bri: 60, xy: [0.4573, 0.41] };
  };

  const identify = async () => {
    setBusy("identify");
    setErr(null);
    try {
      await api.identify(f.id);
    } catch (e) {
      reportError(e);
      setErr(explainError(e));
    } finally {
      setBusy(null);
    }
  };

  const sw = swatch(f);
  const where = placementText(f);
  return (
    <DeviceRow
      testId={`fixture-${f.id}`}
      visual={<Swatch hex={sw.hex} dim={sw.dim} />}
      name={lampName(f)}
      status={{ tone: STATUS_TONE[f.status ?? ""] ?? "muted", text: f.status_text ?? "", pulse: f.status === "following" }}
      summary={where ?? f.label}
    >
      {/* Who drives it */}
      <div role="radiogroup" aria-label={`${f.label}: who controls it`} className="grid grid-cols-2 gap-2">
        {(
          [
            ["house", "House", "Normal lighting"],
            ["dmx", "Follows the music", "Colours from the show"],
          ] as const
        ).map(([k, title, sub]) => {
          const on = (k === "dmx") === following;
          return (
            <button
              key={k}
              type="button"
              role="radio"
              aria-checked={on}
              disabled={disabled || on}
              onClick={() => void send(k === "dmx" ? "follows the music" : "house lighting", k === "dmx" ? "dmx" : house())}
              className={cn(
                "min-h-14 rounded-xl border px-3 py-2 text-left transition-colors disabled:cursor-default",
                on ? "border-teal-300 bg-teal-50 text-teal-900" : "border-stone-200 bg-white hover:bg-stone-50",
                !on && disabled && "opacity-60",
              )}
            >
              <span className="flex items-center gap-1.5 text-base font-semibold">
                {k === "dmx" ? <Radio className="h-4 w-4" aria-hidden /> : <Lightbulb className="h-4 w-4" aria-hidden />}
                {title}
                {busy && ((k === "dmx") !== following) && <Spinner className="ml-auto" />}
              </span>
              <span className="block text-sm text-muted-foreground">{sub}</span>
            </button>
          );
        })}
      </div>

      {following ? (
        <p className="text-base text-muted-foreground">
          {f.status === "following"
            ? "Colouring with the music now."
            : f.status === "armed"
              ? "It follows as soon as the show sends colours."
              : f.status_text}
        </p>
      ) : (
        <div className="space-y-3">
          <div className="flex min-h-11 items-center justify-between gap-3">
            <span className="text-base font-medium">On</span>
            <Switch
              aria-label={`${f.label} on`}
              checked={st.on}
              disabled={disabled}
              onCheckedChange={(on) =>
                void send(on ? "on" : "off", on ? { ...(house() as object), on: true } as HueValue : "off")
              }
            />
          </div>
          <div className="flex min-h-11 items-center gap-3">
            <span className="shrink-0 text-base font-medium">Brightness</span>
            <Slider
              aria-label={`${f.label} brightness`}
              className="flex-1 py-3 [&_[role=slider]]:h-6 [&_[role=slider]]:w-6 [&_[role=slider]]:shadow-sm"
              min={1}
              max={100}
              step={1}
              value={[bri]}
              disabled={disabled}
              onValueChange={([v]) => {
                dragging.current = true;
                setBri(v);
              }}
              onValueCommit={([v]) => {
                dragging.current = false;
                const base = house();
                void send(`brightness ${v}%`, { ...(typeof base === "object" ? base : {}), on: true, bri: v } as HueValue);
              }}
            />
            <span className="w-11 shrink-0 text-right font-mono text-[15px] tabular-nums text-muted-foreground">{bri}%</span>
          </div>
          <div>
            <span className="text-base font-medium">Colour</span>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              {PRESETS.map((p) => (
                <button
                  key={p.name}
                  type="button"
                  title={p.name}
                  aria-label={p.name}
                  disabled={disabled}
                  onClick={() => void send(p.name.toLowerCase(), { on: true, bri: Math.max(bri, 1), hex: p.hex })}
                  className="h-10 w-10 rounded-full border border-stone-300 shadow-sm transition-transform hover:scale-105 disabled:opacity-50"
                  style={{ background: p.hex }}
                />
              ))}
              <label className="relative h-10 w-10 cursor-pointer overflow-hidden rounded-full border border-dashed border-stone-400" title="Any colour">
                <span className="sr-only">Any colour</span>
                <input
                  type="color"
                  disabled={disabled}
                  defaultValue={st.hex ?? "#ffc367"}
                  onChange={(e) => {
                    const hex = e.currentTarget.value;
                    window.clearTimeout((e.currentTarget as unknown as { _t?: number })._t);
                    (e.currentTarget as unknown as { _t?: number })._t = window.setTimeout(
                      () => void send(`colour ${hex}`, { on: true, bri: Math.max(bri, 1), hex }),
                      350,
                    );
                  }}
                  className="absolute inset-0 h-full w-full cursor-pointer opacity-0"
                />
                <Sparkles className="absolute inset-0 m-auto h-4 w-4 text-stone-500" aria-hidden />
              </label>
            </div>
          </div>
          <button
            type="button"
            disabled={disabled}
            onClick={() => void identify()}
            className="min-h-11 rounded-xl border border-stone-200 bg-white px-4 text-base hover:bg-stone-50 disabled:opacity-50"
          >
            {busy === "identify" ? <Spinner className="mr-2 inline" /> : null}Identify (the lamp breathes)
          </button>
        </div>
      )}

      {err && <p className="text-sm text-rose-700">{err}</p>}
      {!f.writable && <p className="text-sm text-muted-foreground">Can't change this lamp right now.</p>}
    </DeviceRow>
  );
}

// ── DMX lights: show_service's rig (contract A), through room_service's /show pass-through ──

/** A DMX light's room value: "dmx" follows the music, "off" is dark, an object is a steady colour (from a mode). */
type LightValue = "dmx" | "off" | { hex: string; bri: number };

/** One light as show_service's GET /api/v1/rig lists it (its per-fixture row). */
interface RigLight {
  id: string;
  label: string;
  kind?: string;
  type_label?: string | null;
  output?: string | null;
  driver?: string | null;
  node_ip?: string | null;
  universe?: number | null;
  address?: number | null;
  follow?: LightValue | null;
  online?: boolean | null;
  last_rgb?: number[] | string | null;
}

type RigBody = {
  enabled?: boolean;
  fixtures?: RigLight[] | Record<string, Omit<RigLight, "id">>;
  outputs?: Record<string, { driver?: string }>;
  registry_view?: { fixtures?: Record<string, Omit<RigLight, "id">>; outputs?: Record<string, { driver?: string }> };
};

const RIG_POLL_MS = 3000;
const SHOW_TIMEOUT_MS = 10_000;
/** Outputs whose lamps are the room's DMX lights (room_service adapters/lights.py DRIVERS); Hue lamps are listed above. */
const DMX_DRIVERS = ["artnet"];

/** show_service through room_service's /show pass-through (open on the LAN: no token). */
async function showFetch<T>(api: RoomApi, method: string, path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const ac = new AbortController();
  const onAbort = () => ac.abort();
  signal?.addEventListener("abort", onAbort);
  if (signal?.aborted) ac.abort();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    ac.abort();
  }, SHOW_TIMEOUT_MS);
  try {
    const res = await fetch(`${api.baseUrl}/show${path}`, {
      method,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: ac.signal,
      cache: "no-cache",
    });
    const text = await res.text();
    let j: unknown;
    try {
      j = text ? JSON.parse(text) : undefined;
    } catch {
      j = undefined;
    }
    if (!res.ok) {
      const o = (j ?? {}) as { error?: string; detail?: string; why?: string };
      throw new RoomApiError(res.status, o.error ?? `http_${res.status}`, o.detail ?? o.why ?? "");
    }
    return j as T;
  } catch (e) {
    if (e instanceof RoomApiError) throw e;
    if (timedOut) throw new RoomApiError(0, "network", `no reply in ${SHOW_TIMEOUT_MS / 1000} s`);
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new RoomApiError(0, "network", e instanceof Error ? e.message : String(e));
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", onAbort);
  }
}

/** The rig's DMX lights, in its order: lamps on an Art-Net output (as room_service's light adapter counts them). */
function rigLights(body: RigBody | undefined): RigLight[] {
  if (!body || body.enabled === false) return [];
  const raw = body.fixtures ?? body.registry_view?.fixtures;
  const rows: RigLight[] = Array.isArray(raw)
    ? raw
    : raw
      ? Object.entries(raw).map(([id, r]) => ({ ...r, id, label: r.label ?? id }))
      : [];
  const outputs = { ...(body.registry_view?.outputs ?? {}), ...(body.outputs ?? {}) };
  return rows.filter((r) => {
    const driver = r.driver ?? (r.output ? outputs[r.output]?.driver : undefined);
    return !!r.id && r.kind === "lamp" && !!driver && DMX_DRIVERS.includes(driver);
  });
}

/** The rig's DMX lights, polled every few seconds while the tab is visible; null until the first answer. */
function useRigLights(api: RoomApi, enabled: boolean) {
  const [lights, setLights] = useState<RigLight[] | null>(null);
  const [unreachable, setUnreachable] = useState(false);
  const [tick, setTick] = useState(0);
  const refresh = useCallback(() => setTick((t) => t + 1), []);
  useEffect(() => {
    if (!enabled) return;
    let stop = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const ac = new AbortController();
    const poll = async () => {
      if (document.visibilityState === "visible") {
        try {
          const body = await showFetch<RigBody>(api, "GET", "/api/v1/rig", undefined, ac.signal);
          if (stop) return;
          setLights(rigLights(body));
          setUnreachable(false);
        } catch (e) {
          if (stop || (e instanceof DOMException && e.name === "AbortError")) return;
          setUnreachable(true);                       // keep the last rows: a blip shouldn't empty the card
        }
      }
      if (!stop) timer = setTimeout(poll, RIG_POLL_MS);
    };
    void poll();
    return () => {
      stop = true;
      ac.abort();
      clearTimeout(timer);
    };
  }, [api, enabled, tick]);
  return { lights, unreachable, refresh };
}

function rgbHex(v: RigLight["last_rgb"]): string | null {
  if (typeof v === "string") return /^#[0-9a-f]{6}$/i.test(v) ? v : null;
  if (!Array.isArray(v) || v.length < 3) return null;
  const [r, g, b] = v.map((x) => Math.max(0, Math.min(255, Math.round(Number(x) || 0))));
  if (r + g + b === 0) return null;                    // dark: the plain disc
  return `#${[r, g, b].map((x) => x.toString(16).padStart(2, "0")).join("")}`;
}

function lightStatus(l: RigLight, unreachable: boolean): { tone: Tone; text: string; pulse?: boolean } {
  if (unreachable) return { tone: "warn", text: "Can't check it right now" };
  if (l.online === false) return { tone: "bad", text: "Not answering" };
  const v = l.follow;
  if (v === "dmx") return { tone: "live", text: "Follows the music", pulse: true };
  if (v === "off") return { tone: "muted", text: "Off" };
  if (v && typeof v === "object") return { tone: "muted", text: `On, ${v.bri}%` };
  return { tone: "muted", text: "" };
}

function DmxRow({
  l,
  api,
  connected,
  unreachable,
  addLog,
  reportError,
  onChanged,
}: {
  l: RigLight;
  api: RoomApi;
  connected: boolean;
  unreachable: boolean;
  addLog: (e: NewLogEntry) => void;
  reportError: (e: unknown) => void;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const disabled = !connected || !!busy;
  const now = l.follow;

  const send = async (value: "dmx" | "off") => {
    setBusy(value);
    setErr(null);
    try {
      await api.setTargets({ [`light.${l.id}`]: value });
      addLog({ type: "fixture", level: "info", summary: `${l.label}: ${value === "dmx" ? "follows the music" : "off"}` });
      window.setTimeout(onChanged, 800);              // the room applies it in the background
    } catch (e) {
      if (e instanceof RoomApiError && e.code === "unknown_target") {
        setErr("The room is still picking up this light. Try again in a few seconds.");
      } else {
        reportError(e);
        setErr(explainError(e));
      }
    } finally {
      setBusy(null);
    }
  };

  const identify = async () => {
    setBusy("identify");
    setErr(null);
    try {
      const r = await showFetch<{ ok?: boolean; why?: string }>(
        api,
        "POST",
        `/api/v1/rig/fixtures/${encodeURIComponent(l.id)}/identify`,
        {},
      );
      if (r && r.ok === false) setErr(r.why ? `Can't light it up: ${r.why}` : "Can't light it up right now.");
    } catch (e) {
      reportError(e);
      setErr(explainError(e));
    } finally {
      setBusy(null);
    }
  };

  const hex = rgbHex(l.last_rgb);
  return (
    <DeviceRow
      testId={`light-${l.id}`}
      visual={<Swatch hex={hex} dim={1} />}
      name={l.label}
      status={lightStatus(l, unreachable)}
      summary={l.type_label || "DMX light"}
    >
      <div role="radiogroup" aria-label={`${l.label}: what it does`} className="grid grid-cols-2 gap-2">
        {(
          [
            ["dmx", "Follows the music", "Colours from the show"],
            ["off", "Off", "Dark"],
          ] as const
        ).map(([k, title, subText]) => {
          const on = now === k;
          return (
            <button
              key={k}
              type="button"
              role="radio"
              aria-checked={on}
              disabled={disabled || on}
              onClick={() => void send(k)}
              className={cn(
                "min-h-14 rounded-xl border px-3 py-2 text-left transition-colors disabled:cursor-default",
                on ? "border-teal-300 bg-teal-50 text-teal-900" : "border-stone-200 bg-white hover:bg-stone-50",
                !on && disabled && "opacity-60",
              )}
            >
              <span className="flex items-center gap-1.5 text-base font-semibold">
                {k === "dmx" ? <Radio className="h-4 w-4" aria-hidden /> : <Moon className="h-4 w-4" aria-hidden />}
                {title}
                {busy === k && <Spinner className="ml-auto" />}
              </span>
              <span className="block text-sm text-muted-foreground">{subText}</span>
            </button>
          );
        })}
      </div>
      {now && typeof now === "object" && (
        <p className="text-base text-muted-foreground">The mode holds it at a steady colour.</p>
      )}
      {l.online === false && (
        <p className="text-base text-muted-foreground">
          Its DMX box{l.node_ip ? ` (${l.node_ip})` : ""} isn't answering. Check it is plugged in and powered.
        </p>
      )}
      <button
        type="button"
        disabled={disabled}
        onClick={() => void identify()}
        className="min-h-11 rounded-xl border border-stone-200 bg-white px-4 text-base hover:bg-stone-50 disabled:opacity-50"
      >
        {busy === "identify" ? <Spinner className="mr-2 inline" /> : null}Identify (full white for a few seconds)
      </button>
      {err && <p className="text-sm text-rose-700">{err}</p>}
    </DeviceRow>
  );
}

export function LightsSection({
  api,
  connected,
  addLog,
  reportError,
  fixtures,
  only,
  sub = false,
  title = "Lights",
}: {
  api: RoomApi;
  connected: boolean;
  addLog: (e: NewLogEntry) => void;
  reportError: (e: unknown) => void;
  /** A shared useFixtures() result (one poll for every room's lights); without it the section polls itself. */
  fixtures?: FixturesState;
  /** Only these lights (a room's lights from rooms.json: Hue lamps by fixture id such as "hue.ne", "light" for every
   *  DMX light, or "light.<id>" for one); nothing renders if none of them exist. */
  only?: string[];
  /** Inside a room's section: a smaller "Lights" heading under the room name. */
  sub?: boolean;
  /** The heading (inside the page's Lights fold: "Lamps", or the room's name when several rooms have lights). */
  title?: string;
}) {
  const own = useFixtures(api, !fixtures);
  const { view, missing, error, refresh } = fixtures ?? own;
  const wantsDmx = !only || only.some((id) => id === "light" || id.startsWith("light."));
  const rig = useRigLights(api, wantsDmx);
  const dmx = wantsDmx
    ? (rig.lights ?? []).filter((l) => !only || only.includes("light") || only.includes(`light.${l.id}`))
    : [];
  const hueOn = !(missing || (view && !view.enabled));
  const lamps = hueOn ? (view?.fixtures.filter((f) => f.kind === "hue") ?? []).filter((f) => !only || only.includes(f.id)) : [];
  if (!hueOn && dmx.length === 0) return null;
  if (hueOn && view && only && lamps.length === 0 && dmx.length === 0) return null;
  const a = view?.artnet;
  const feed = a?.online
    ? { tone: "live" as Tone, text: `${a.source === "fake" ? "Test show" : "Show"} sending` }
    : a?.packets
      ? { tone: "warn" as Tone, text: "Show stopped sending" }
      : { tone: "muted" as Tone, text: "Waiting for the show" };

  return (
    <DeviceSection
      title={title}
      sub={sub}
      badge={hueOn && view && lamps.length > 0 && <Pill tone={feed.tone} pulse={!!a?.online}>{feed.text}</Pill>}
    >
      {hueOn && !view && (
        <p className="flex items-center gap-2 py-3 text-base text-muted-foreground">
          {error ? error : (<><Spinner /> Finding the lights…</>)}
        </p>
      )}
      {hueOn && view?.hue && view.hue.state === "unavailable" && (
        <p className="py-2 text-base text-rose-700">Can't reach the lamps right now.</p>
      )}
      {(view || dmx.length > 0) && (
        <DeviceList>
          {lamps.map((f) => (
            <LampRow key={f.id} f={f} api={api} connected={connected} addLog={addLog} reportError={reportError} onChanged={refresh} />
          ))}
          {dmx.map((l) => (
            <DmxRow
              key={l.id}
              l={l}
              api={api}
              connected={connected}
              unreachable={rig.unreachable}
              addLog={addLog}
              reportError={reportError}
              onChanged={rig.refresh}
            />
          ))}
        </DeviceList>
      )}
    </DeviceSection>
  );
}
