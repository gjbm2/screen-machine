// Settings › Lights (#lights): where the room's lights are set up (Lights setup: add a light, name it, place it on the
// map), the Hue lamps the bridge has (read only: they're named in the Hue app), and the remote-controlled things the
// room drives (the glitterball, the blinds), read only. Each lamp's on, off or "follows the music" stays on the Room
// page's Lights fold.

import { useEffect, useState } from "react";
import { Lightbulb } from "lucide-react";
import { lightSetupUrl, lightsPageUrl } from "@/components/nav/appNav";
import { useDeployment } from "@/hooks/useDeployment";
import { rigLightIds } from "@/pages/Room/lightStates";
import { capabilityOn } from "@/shared/api/deployment";
import type { RoomApi } from "@/pages/Room/api";
import type { FixturesView, RoomStatus } from "@/pages/Room/types";
import { friendlyName } from "@/pages/Room/words";
import { Group, Hint, LinkRow, ReadRow, Sub } from "./Group";

/** The group's closed line from what this site has: its Hue lamps, its DMX lights, things worked by remote. */
function lightsSummary(hue: boolean | undefined, dmx: number, rf: boolean | undefined, remoteThings: number): string {
  const parts = ["Set up lights"];
  if (hue !== false) parts.push("the Hue lamps");
  else if (dmx > 0) parts.push(`${dmx} DMX light${dmx === 1 ? "" : "s"}`);
  if (rf !== false || remoteThings > 0) parts.push("worked by remote");
  return parts.join(" · ");
}

export function LightsGroup({ api, status }: { api: RoomApi; status: RoomStatus | null }) {
  const { deployment } = useDeployment();
  const rooms = status?.rooms ?? [];
  const remoteThings = rooms.reduce((n, r) => n + (r.blinds?.length ?? 0) + (r.effects?.length ?? 0), 0);
  const summary = lightsSummary(capabilityOn(deployment, "hue"), (rigLightIds(status) ?? []).length, capabilityOn(deployment, "rf"), remoteThings);
  return (
    <Group id="lights" title="Lights" icon={<Lightbulb />} summary={summary}>
      <LightsBody api={api} status={status} hueCap={capabilityOn(deployment, "hue")} />
    </Group>
  );
}

/** Mounted only while the group is open, so a closed group costs no requests. */
function LightsBody({ api, status, hueCap }: { api: RoomApi; status: RoomStatus | null; hueCap: boolean | undefined }) {
  // undefined: reading; "error": the room didn't answer; null: room_service has no fixtures view (no Hue)
  const [fx, setFx] = useState<FixturesView | null | "error" | undefined>(undefined);
  useEffect(() => {
    let alive = true;
    api
      .fixtures()
      .then((v) => alive && setFx(v))
      .catch(() => alive && setFx("error"));
    return () => {
      alive = false;
    };
  }, [api]);

  const view = fx && fx !== "error" ? fx : null;
  const hue = (view?.fixtures ?? []).filter((f) => f.kind === "hue");
  // the Hue part only where the site has Hue: lamps, or Hue switched on (a bridge not yet paired says so)
  const showHue = hue.length > 0 || fx === "error" || (hueCap !== false && (fx === undefined || !!view?.enabled));
  const dmx = rigLightIds(status) ?? [];
  const subs = status?.subsystems ?? {};
  const rooms = status?.rooms ?? [];
  const blinds = rooms.reduce((n, r) => n + (r.blinds?.length ?? 0), 0);
  const effects = rooms.flatMap((r) => r.effects ?? []).filter((e) => !!subs[e.power.split(".")[0]]);
  const bridge = view?.hue;

  return (
    <>
      <LinkRow href={lightSetupUrl()} state={{ from: "Settings" }}>
        Light setup: add a light or an appliance, name it, place it, learn its remote
      </LinkRow>

      {/* a site without Hue: its DMX lights instead (where there's Hue, this part reads as it always has) */}
      {dmx.length > 0 && !showHue && (
        <>
          <Sub>DMX lights</Sub>
          <ReadRow label="Lights" value={`${dmx.length} · ${subs.light?.available === false ? "not answering" : "connected"}`} />
          <Hint>Added, named and placed on the light setup page.</Hint>
        </>
      )}

      {showHue && <Sub>Hue lamps</Sub>}
      {!showHue ? null : fx === undefined ? (
        <Hint>Reading…</Hint>
      ) : hue.length ? (
        <>
          {bridge?.bridge && (
            <ReadRow label="Bridge" value={`${bridge.bridge}${bridge.reachable === false ? " · not answering" : ""}`} />
          )}
          {hue.map((f) => (
            <ReadRow key={f.id} label={f.label || f.id} value={f.placement || f.status_text || "Hue lamp"} />
          ))}
          <Hint>Hue lamps are named in the Hue app.</Hint>
        </>
      ) : (
        <Hint>{fx === "error" ? "The room isn't answering." : "No Hue lamps."}</Hint>
      )}

      {(effects.length > 0 || blinds > 0) && (
        <>
          <Sub>Worked by remote</Sub>
          {effects.map((e) => (
            <ReadRow
              key={e.power}
              label={friendlyName(e.power.split(".")[0])}
              value={subs[e.power.split(".")[0]]?.available === false ? "Not connected" : "Connected"}
            />
          ))}
          {blinds > 0 && (
            <ReadRow label="Blinds" value={`${blinds} · ${subs.blinds?.dry_run ? "practice run only" : subs.blinds?.available === false ? "not connected" : "connected"}`} />
          )}
          <Hint>Added, and their remote buttons learned, on the light setup page.</Hint>
        </>
      )}

      <LinkRow href={lightsPageUrl()}>Work the lights</LinkRow>
    </>
  );
}
