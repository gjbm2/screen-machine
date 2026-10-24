// The room's lights. Hue lamps (room_service dmx/api.py): each one device that either follows the music
// (Art-Net, streamed to the bridge) or is under house control. DMX lights (show_service's rig, registered on
// the Lights setup page, read through room_service's /show pass-through): each follows the music, holds a steady
// colour, or is off. Both are set through the room state model (PUT /api/v1/room/targets: hue.<id>, light.<id>), so a
// mode change and a hand change go through the same coordinator. Each row is the Lights setup page's own (LightShell:
// the same name, kind, where it is, Identify always at the right, the same colour block) and ends with
// "Set up this light ›" (/lights?light=<id>). Names drop their room's name (rooms.json); where a light is comes from
// the Room map once it's placed there.

import { useCallback, useEffect, useMemo, useState } from "react";
import { Lightbulb, Moon, Palette, Radio } from "lucide-react";
import { AppLink } from "@/components/nav/AppLink";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import { LightsApi } from "@/pages/Lights/api";
import { useIdentify } from "@/pages/Lights/Identify";
import { ColourBlock, LightShell, summaryLine } from "@/pages/Lights/LightItem";
import {
  COLOUR_PRESETS,
  fixtureStatus,
  fixtureSwatch,
  roomOfLight,
  shortName,
  whereWords,
  type RoomSpace,
  type ScreenSpot,
} from "@/pages/Lights/lib";
import { useRoomFacts, useSpace } from "@/pages/Lights/useRig";
import { explainError, RoomApiError, type RoomApi } from "./api";
import { Pill, Spinner } from "./bits";
import { DeviceList, DeviceSection } from "./Devices";
import type { Tone } from "./tones";
import type { Fixture, HueValue } from "./types";
import { type FixturesState, useFixtures } from "./useFixtures";
import type { NewLogEntry } from "./useRoomConnection";

/** The words for a quick colour in the log ("warm"), else its hex. */
function colourName(hex: string): string {
  return COLOUR_PRESETS.find((p) => p.hex === hex.toLowerCase())?.name.toLowerCase() ?? `colour ${hex}`;
}

/** Where a lamp is when it isn't on the Room map yet: patch.yaml's screen and placement. */
function placementText(f: Fixture, screenNames: Record<string, string>): string | null {
  if (!f.screen) return null;
  const name = screenNames[f.screen];
  const screen = name ? name.replace(/^([A-Z])(?=[a-z])/, (c) => c.toLowerCase()) : `${f.screen.replace(/-screen$/, "")} screen`;
  if (f.placement === "above") return `Above the ${screen}`;
  if (f.placement === "opposite") return `Opposite the ${screen}`;
  return `By the ${screen}`;
}

function stateOf(f: Fixture): { on: boolean; bri: number; hex: string | null } {
  const v = (f.light?.value ?? f.house_value) as HueValue | undefined;
  if (v && typeof v === "object") return { on: v.on, bri: v.bri, hex: f.light?.hex ?? null };
  return { on: false, bri: f.light?.bri ?? 50, hex: f.light?.hex ?? null };
}

/** "Set up this light ›": its row on the Lights setup page. */
function SetupLink({ id }: { id: string }) {
  return (
    <AppLink
      href={`/lights?light=${encodeURIComponent(id)}`}
      state={{ from: "Room" }}
      className="inline-flex min-h-11 items-center text-[15px] font-medium text-teal-800 underline-offset-4 hover:underline"
      data-testid={`setup-${id}`}
    >
      Set up this light ›
    </AppLink>
  );
}

/** One choice of what a light does (a radio tile). */
function Choice({
  on,
  disabled,
  busy,
  icon,
  title,
  sub,
  onPick,
}: {
  on: boolean;
  disabled: boolean;
  busy: boolean;
  icon: JSX.Element;
  title: string;
  sub: string;
  onPick: () => void;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={on}
      disabled={disabled || on}
      onClick={onPick}
      className={cn(
        "min-h-14 rounded-xl border px-3 py-2 text-left transition-colors disabled:cursor-default",
        on ? "border-teal-300 bg-teal-50 text-teal-900" : "border-stone-200 bg-white hover:bg-stone-50",
        !on && disabled && "opacity-60",
      )}
    >
      <span className="flex items-center gap-1.5 text-base font-semibold">
        {icon}
        {title}
        {busy && <Spinner className="ml-auto" />}
      </span>
      <span className="block text-sm text-muted-foreground">{sub}</span>
    </button>
  );
}

function LampRow({
  f,
  api,
  lights,
  name,
  where,
  connected,
  addLog,
  reportError,
  onChanged,
}: {
  f: Fixture;
  api: RoomApi;
  lights: LightsApi;
  name: string;
  where: string | null;
  connected: boolean;
  addLog: (e: NewLogEntry) => void;
  reportError: (e: unknown) => void;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const ident = useIdentify(lights, f.id, true);
  const st = stateOf(f);

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

  return (
    <LightShell
      testId={`fixture-${f.id}`}
      swatch={fixtureSwatch(f)}
      name={name}
      status={fixtureStatus(f)}
      summary={summaryLine("Hue lamp", where)}
      identify={ident}
    >
      {/* Who drives it */}
      <div role="radiogroup" aria-label={`${name}: who controls it`} className="grid grid-cols-2 gap-2">
        <Choice
          on={!following}
          disabled={disabled}
          busy={!!busy && following}
          icon={<Lightbulb className="h-4 w-4" aria-hidden />}
          title="House"
          sub="Normal lighting"
          onPick={() => void send("house lighting", house())}
        />
        <Choice
          on={following}
          disabled={disabled}
          busy={!!busy && !following}
          icon={<Radio className="h-4 w-4" aria-hidden />}
          title="Follows the music"
          sub="Colours from the show"
          onPick={() => void send("follows the music", "dmx")}
        />
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
              aria-label={`${name} on`}
              checked={st.on}
              disabled={disabled}
              onCheckedChange={(on) =>
                void send(on ? "on" : "off", on ? ({ ...(house() as object), on: true } as HueValue) : "off")
              }
            />
          </div>
          <ColourBlock
            label={name}
            hex={st.hex}
            bri={st.bri}
            disabled={disabled}
            onBrightness={(v) => {
              const base = house();
              void send(`brightness ${v}%`, { ...(typeof base === "object" ? base : {}), on: true, bri: v } as HueValue);
            }}
            onColour={(hex, bri) => void send(colourName(hex), { on: true, bri, hex })}
          />
        </div>
      )}

      {err && <p className="text-sm text-rose-700">{err}</p>}
      {!f.writable && <p className="text-sm text-muted-foreground">Can't change this lamp right now.</p>}
      <SetupLink id={f.id} />
    </LightShell>
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
  placement?: string | null;
  zone?: string | null;
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
export function useRigLights(api: RoomApi, enabled: boolean) {
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
  lights,
  name,
  where,
  connected,
  unreachable,
  addLog,
  reportError,
  onChanged,
}: {
  l: RigLight;
  api: RoomApi;
  lights: LightsApi;
  name: string;
  where: string | null;
  connected: boolean;
  unreachable: boolean;
  addLog: (e: NewLogEntry) => void;
  reportError: (e: unknown) => void;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const ident = useIdentify(lights, l.id, false);
  const disabled = !connected || !!busy;
  const now = l.follow;
  const steady = now && typeof now === "object" ? now : null;

  const send = async (key: string, value: LightValue, words: string) => {
    setBusy(key);
    setErr(null);
    try {
      await api.setTargets({ [`light.${l.id}`]: value });
      addLog({ type: "fixture", level: "info", summary: `${l.label}: ${words}` });
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

  // A steady colour starts where the room's own form does (room_service adapters/lights.py): full white.
  const startSteady = steady ?? { hex: "#ffffff", bri: 100 };
  const hex = steady ? steady.hex : rgbHex(l.last_rgb);
  return (
    <LightShell
      testId={`light-${l.id}`}
      swatch={{ hex, dim: steady ? Math.max(0.25, steady.bri / 100) : 1 }}
      name={name}
      status={lightStatus(l, unreachable)}
      summary={summaryLine("DMX light", where)}
      identify={ident}
    >
      <div role="radiogroup" aria-label={`${name}: what it does`} className="grid grid-cols-1 gap-2 sm:grid-cols-3">
        <Choice
          on={!!steady}
          disabled={disabled}
          busy={busy === "steady"}
          icon={<Palette className="h-4 w-4" aria-hidden />}
          title="Steady colour"
          sub="One colour, as set below"
          onPick={() => void send("steady", startSteady, `steady colour ${startSteady.hex}`)}
        />
        <Choice
          on={now === "dmx"}
          disabled={disabled}
          busy={busy === "dmx"}
          icon={<Radio className="h-4 w-4" aria-hidden />}
          title="Follows the music"
          sub="Colours from the show"
          onPick={() => void send("dmx", "dmx", "follows the music")}
        />
        <Choice
          on={now === "off"}
          disabled={disabled}
          busy={busy === "off"}
          icon={<Moon className="h-4 w-4" aria-hidden />}
          title="Off"
          sub="Dark"
          onPick={() => void send("off", "off", "off")}
        />
      </div>
      {steady && (
        <ColourBlock
          label={name}
          hex={steady.hex}
          bri={steady.bri}
          disabled={disabled}
          onBrightness={(v) => void send("bri", { hex: steady.hex, bri: v }, `brightness ${v}%`)}
          onColour={(c, bri) => void send("colour", { hex: c, bri }, colourName(c))}
        />
      )}
      {l.online === false && (
        <p className="text-base text-muted-foreground">
          It isn't answering. Check it is plugged in and powered.
        </p>
      )}
      {err && <p className="text-sm text-rose-700">{err}</p>}
      <SetupLink id={l.id} />
    </LightShell>
  );
}

/** Where things are on the Room map, and the screens by name, for the rows' "where" words: read once (and when the
 *  page comes back into view), not polled. */
function useWhere(api: RoomApi, view: FixturesState["view"]) {
  const lights = useMemo(() => new LightsApi(api.baseUrl), [api.baseUrl]);
  const facts = useRoomFacts(api.baseUrl);
  const { space } = useSpace(lights);
  const spots = useMemo((): ScreenSpot[] => {
    if (!space) return [];
    return (view?.fixtures ?? []).flatMap((f) => {
      const at = f.kind === "screen" ? space.appliances[f.id]?.at : null;
      return at ? [{ name: (f.screen && facts.screenNames[f.screen]) || f.label, at }] : [];
    });
  }, [space, view, facts.screenNames]);
  return { lights, facts, space: space as RoomSpace | null, spots };
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
  const where = useWhere(api, view);
  const rooms = where.facts.rooms;
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
            <LampRow
              key={f.id}
              f={f}
              api={api}
              lights={where.lights}
              name={shortName(f.label, roomOfLight(rooms, f.id, false)?.name)}
              where={
                whereWords(f.id, where.space, where.spots, null, null) ?? placementText(f, where.facts.screenNames)
              }
              connected={connected}
              addLog={addLog}
              reportError={reportError}
              onChanged={refresh}
            />
          ))}
          {dmx.map((l) => (
            <DmxRow
              key={l.id}
              l={l}
              api={api}
              lights={where.lights}
              name={shortName(l.label, roomOfLight(rooms, l.id, true)?.name)}
              where={whereWords(l.id, where.space, where.spots, l.placement ?? null, l.zone ?? null)}
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
