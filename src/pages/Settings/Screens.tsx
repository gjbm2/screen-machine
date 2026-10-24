// Settings › Screens (#screens, and #screen-<id> for one screen). First the art's brightness by the room's light: each
// room light sensor's curve (lux → how bright the art is), as plain sliders (the existing PUT
// /api/lightsensor/intensity-settings/<sensor>, the same file /lightsense edits). Then one fold per screen:
//   - brightness now (the mask room_service pushes to the TV) and "Brighter or dimmer now" (the existing
//     POST/DELETE /api/<screen>/brightness-override: a fixed level until "Back to automatic");
//   - what the site config says about it, read only for now (its name, whether it goes live, whether it follows the
//     room's light, picture profile and GPU, from GET /api/publish-destinations);
//   - while it's live, how it picks scenes and effects and its feel (the Room page's LookSettings, by import);
//   - links to it on the Room page, its schedule and its TV page.

import { useCallback, useEffect, useMemo, useState } from "react";
import { MonitorPlay } from "lucide-react";
import { schedulerUrl, displayUrl } from "@/components/nav/appNav";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { clearBrightnessOverride, getBrightnessOverrideResponse, setBrightnessOverride } from "@/shared/api/admin";
import { getIntensitySettings, getLightsense, updateIntensitySettings } from "@/shared/api/light";
import { Notice, Spinner, Unroll } from "@/pages/Room/bits";
import { LookSettings } from "@/pages/Room/LivePanel";
import type { RoomApi } from "@/pages/Room/api";
import type { Library, RoomStatus, ScreenStatus } from "@/pages/Room/types";
import { useLiveLook } from "@/pages/Room/useLiveLook";
import type { NewLogEntry, StreamEvent } from "@/pages/Room/useRoomConnection";
import { friendlyName } from "@/pages/Room/words";
import { Group, Hint, LinkRow, ReadRow, Row, Sub } from "./Group";

/** The parts of a publish-destinations.json entry shown here. */
export interface Destination {
  id: string;
  name?: string;
  hidden?: boolean;
  intensity_cfg?: { adjust?: boolean } | null;
  live?: { enabled?: boolean; gpu?: number; profile?: string } | null;
}

interface Point {
  lux: number;
  intensity: number;
}
interface Mapping {
  lux_to_intensity: Point[];
  target_group: string;
}

const pct = (v: number) => `${Math.round(v * 100)}%`;
const luxWords = (lux: number) => (lux <= 0 ? "Dark (0 lux)" : `${lux} lux`);

const MASK_SOURCE: Record<string, string> = {
  sensor: "from the room's light",
  astral: "from the time of day",
  blend: "from the room's light and the time of day",
  fixed: "fixed",
  manual_override: "set by hand",
  maskoff: "not dimmed",
  flask: "from the app",
};

// ── The curve, per light sensor ──────────────────────────────────────────────

function SensorCurve({ sensor, mapping, roomName, onSaved }: { sensor: string; mapping: Mapping; roomName: string; onSaved: () => void }) {
  const [points, setPoints] = useState<Point[]>(() => [...mapping.lux_to_intensity].sort((a, b) => a.lux - b.lux));
  const [now, setNow] = useState<{ lux: number | null; target: number | null } | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => setPoints([...mapping.lux_to_intensity].sort((a, b) => a.lux - b.lux)), [mapping]);

  useEffect(() => {
    let alive = true;
    const read = () =>
      getLightsense()
        .then((d) => {
          const c = d?.sensors?.[sensor]?.current;
          if (alive) setNow(c ? { lux: c.lux ?? null, target: c.target_intensity ?? null } : null);
        })
        .catch(() => alive && setNow(null));
    void read();
    const id = window.setInterval(read, 10_000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, [sensor]);

  const original = useMemo(() => [...mapping.lux_to_intensity].sort((a, b) => a.lux - b.lux), [mapping]);
  const changed = points.some((p, i) => p.intensity !== original[i]?.intensity);

  const save = async () => {
    setBusy(true);
    setMsg(null);
    try {
      await updateIntensitySettings(sensor, { points, target_group: mapping.target_group });
      setMsg({ ok: true, text: "Saved. The screens follow it within a few seconds." });
      onSaved();
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : "Couldn't save that." });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-2 rounded-2xl border border-stone-200/80 bg-card px-3 py-2" data-testid={`settings-curve-${sensor}`}>
      <p className="text-base font-semibold">Art brightness by the room's light · {roomName}</p>
      <ReadRow
        label="Room light now"
        value={now?.lux === null || now?.lux === undefined ? "Not heard yet" : `${Math.round(now.lux)} lux${now.target !== null ? ` · art at ${pct(now.target)}` : ""}`}
      />
      <Unroll title="Brightness at each light level" hint={`${points.length} levels`} testId={`settings-curve-${sensor}-points`}>
        <div className="space-y-3 pb-2">
          {points.map((p, i) => (
            <div key={`${p.lux}-${i}`} className="space-y-1">
              <div className="flex items-baseline justify-between text-[15px]">
                <span>{luxWords(p.lux)}</span>
                <span className="font-medium tabular-nums">{pct(p.intensity)}</span>
              </div>
              <Slider
                value={[Math.round(p.intensity * 100)]}
                min={0}
                max={100}
                step={1}
                aria-label={`Art brightness at ${luxWords(p.lux)}`}
                onValueChange={([v]) => setPoints((ps) => ps.map((q, j) => (j === i ? { ...q, intensity: v / 100 } : q)))}
                className="py-2"
              />
            </div>
          ))}
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => void save()} disabled={!changed || busy} className="h-11 rounded-xl px-5 text-base">
              {busy && <Spinner />} Save
            </Button>
            {changed && (
              <Button variant="ghost" onClick={() => setPoints(original)} className="h-11 rounded-xl text-base">
                Put back
              </Button>
            )}
          </div>
        </div>
      </Unroll>
      {msg && <p className={msg.ok ? "text-sm text-muted-foreground" : "text-sm text-rose-700"}>{msg.text}</p>}
    </div>
  );
}

// ── One screen ───────────────────────────────────────────────────────────────

function BrightNow({ screenId, mask }: { screenId: string; mask: ScreenStatus["mask"] }) {
  const [override, setOverride] = useState<number | null | undefined>(undefined);
  const [draft, setDraft] = useState(50);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const read = useCallback(async () => {
    try {
      const r = await getBrightnessOverrideResponse(screenId);
      const j = r.ok ? await r.json() : null;
      const v = typeof j?.override === "number" ? (j.override as number) : null;
      setOverride(v);
      if (v !== null) setDraft(Math.round(v * 100));
    } catch {
      setOverride(undefined);
    }
  }, [screenId]);
  useEffect(() => {
    void read();
  }, [read]);
  useEffect(() => {
    if (override === null && typeof mask?.brightness === "number") setDraft(Math.round(mask.brightness * 100));
  }, [override, mask?.brightness]);

  const act = async (fn: () => Promise<Response>) => {
    setBusy(true);
    setErr(null);
    try {
      const r = await fn();
      if (!r.ok) setErr("The app didn't take that.");
      await read();
    } catch {
      setErr("The app isn't answering.");
    } finally {
      setBusy(false);
    }
  };

  const b = typeof mask?.brightness === "number" ? mask.brightness : null;
  return (
    <>
      <ReadRow
        label="Art brightness now"
        value={b === null ? "Not known" : `${pct(b)}${mask?.source && MASK_SOURCE[mask.source] ? ` · ${MASK_SOURCE[mask.source]}` : ""}`}
      />
      <Row
        label="Brighter or dimmer now"
        hint={override !== null && override !== undefined ? `Held at ${pct(override)} until you put it back.` : "Follows the room by itself."}
        stack
        testId={`settings-bright-${screenId}`}
      >
        <div className="space-y-2">
          <div className="flex items-center gap-3">
            <Slider
              value={[draft]}
              min={0}
              max={100}
              step={1}
              onValueChange={([v]) => setDraft(v)}
              aria-label="Art brightness by hand"
              className="flex-1 py-2"
            />
            <span className="w-12 text-right text-[15px] font-medium tabular-nums">{draft}%</span>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => void act(() => setBrightnessOverride(screenId, draft / 100))} disabled={busy} className="h-11 rounded-xl px-5 text-base">
              {busy && <Spinner />} Hold at {draft}%
            </Button>
            {override !== null && override !== undefined && (
              <Button variant="outline" onClick={() => void act(() => clearBrightnessOverride(screenId))} disabled={busy} className="h-11 rounded-xl bg-white px-5 text-base">
                Back to automatic
              </Button>
            )}
          </div>
          {err && <p className="text-sm text-rose-700">{err}</p>}
        </div>
      </Row>
    </>
  );
}

function ScreenLook({
  screenId,
  name,
  session,
  api,
  lib,
  subscribe,
  addLog,
  reportError,
}: {
  screenId: string;
  name: string;
  session: string;
  api: RoomApi;
  lib: Library | null;
  subscribe: (fn: (e: StreamEvent) => void) => () => void;
  addLog: (e: NewLogEntry) => void;
  reportError: (e: unknown) => void;
}) {
  const lv = useLiveLook({ api, screenId, sessionId: session, enabled: true, subscribe, addLog, reportError });
  if (lv.available === false) return <Hint>This room service can't change how {name} picks while live.</Hint>;
  if (!lv.look) {
    return (
      <p className="flex items-center gap-2 text-[15px] text-muted-foreground">
        <Spinner /> Reading {name}'s look…
      </p>
    );
  }
  return <LookSettings screenId={screenId} lv={lv} lib={lib} api={api} words={`These change ${name} while it's live.`} />;
}

/** Every light sensor's curve (read when the Screens group opens). */
function Curves({ status }: { status: RoomStatus | null }) {
  const [maps, setMaps] = useState<Record<string, Mapping> | null>(null);
  const [mapsErr, setMapsErr] = useState(false);
  const [nonce, setNonce] = useState(0);
  useEffect(() => {
    let alive = true;
    getIntensitySettings()
      .then((d) => {
        if (!alive) return;
        setMaps((d?.sensor_mappings ?? {}) as Record<string, Mapping>);
        setMapsErr(false);
      })
      .catch(() => alive && setMapsErr(true));
    return () => {
      alive = false;
    };
  }, [nonce]);
  const rooms = status?.rooms ?? [];
  const roomOfSensor = (s: string) => rooms.find((r) => r.sensors?.includes(s))?.name ?? null;
  if (mapsErr) return <Notice tone="warn">The app isn't answering, so the brightness curve can't be read.</Notice>;
  if (maps === null)
    return (
      <p className="flex items-center gap-2 text-[15px] text-muted-foreground">
        <Spinner /> Reading…
      </p>
    );
  const list = Object.entries(maps);
  if (!list.length) return <Hint>No light sensor is set up.</Hint>;
  return (
    <>
      {list.map(([sensor, m]) => (
        <SensorCurve key={sensor} sensor={sensor} mapping={m} roomName={roomOfSensor(sensor) ?? m.target_group ?? sensor} onSaved={() => setNonce((n) => n + 1)} />
      ))}
    </>
  );
}

export function ScreensGroup({
  api,
  status,
  dests,
  lib,
  subscribe,
  addLog,
  reportError,
}: {
  api: RoomApi;
  status: RoomStatus | null;
  dests: Destination[] | null;
  lib: Library | null;
  subscribe: (fn: (e: StreamEvent) => void) => () => void;
  addLog: (e: NewLogEntry) => void;
  reportError: (e: unknown) => void;
}) {
  const screens = Object.entries(status?.screens ?? {});
  const destOf = (id: string) => dests?.find((d) => d.id === id) ?? null;
  const yesNo = (v: boolean | undefined | null, yes: string, no: string) => (v === undefined || v === null ? "Not set" : v ? yes : no);

  return (
    <Group
      id="screens"
      title="Screens"
      icon={<MonitorPlay />}
      summary={screens.length ? screens.map(([id, sc]) => sc.name || friendlyName(id)).join(", ") : "Screens"}
      owns={(t) => t.startsWith("screen-")}
    >
      <Sub>Art brightness</Sub>
      <Hint>The art on the screens dims with the room's light, so it never glares at night.</Hint>
      <Curves status={status} />

      <Sub>Each screen</Sub>
      {screens.length === 0 && <Hint>{status ? "The room has no screens set up." : "Reading the room…"}</Hint>}
      <div className="space-y-2">
        {screens.map(([id, sc]) => {
          const name = sc.name || friendlyName(id);
          const d = destOf(id);
          const obs = sc.observed;
          const live = (obs?.phase === "live" || obs?.phase === "entering") && !!obs?.session;
          return (
            <Group key={id} id={`screen-${id}`} title={name} summary={live ? "Live now" : "Showing art"} sub>
              <BrightNow screenId={id} mask={sc.mask} />

              <Sub>Set up</Sub>
              <ReadRow label="Name" value={d?.name || name} />
              <ReadRow label="Goes live" value={yesNo(d?.live?.enabled, "Yes", "No: art only")} />
              <ReadRow label="Dims with the room's light" value={yesNo(d?.intensity_cfg?.adjust, "Yes", "No")} />
              {d?.live?.profile && <ReadRow label="Live picture" value={d.live.profile} />}
              {typeof d?.live?.gpu === "number" && <ReadRow label="Graphics card" value={`GPU ${d.live.gpu}`} />}
              <Hint>These are read only here for now.</Hint>

              <Sub>Scenes and effects</Sub>
              {live && obs?.session ? (
                <ScreenLook
                  screenId={id}
                  name={name}
                  session={obs.session}
                  api={api}
                  lib={lib}
                  subscribe={subscribe}
                  addLog={addLog}
                  reportError={reportError}
                />
              ) : (
                <Hint>How {name} picks its scenes and effects shows here while it's live.</Hint>
              )}

              <div className="flex flex-wrap gap-x-4">
                <LinkRow href={`/room?screen=${encodeURIComponent(id)}`}>On the Room page</LinkRow>
                <LinkRow href={schedulerUrl(id)}>Schedule</LinkRow>
                <LinkRow href={displayUrl(id)} newTab>
                  TV page
                </LinkRow>
              </div>
            </Group>
          );
        })}
      </div>
    </Group>
  );
}
