// Settings › Lights (#lights): where the room's lights are set up (Lights setup: add a light, name it, place it on the
// map), the Hue lamps the bridge has (read only: they're named in the Hue app), and the remote-controlled things the
// room drives (the glitterball, the blinds), read only. Each lamp's on, off or "follows the music" stays on the Room
// page's Lights fold.

import { useEffect, useState } from "react";
import { Lightbulb } from "lucide-react";
import { lightsPageUrl } from "@/components/nav/appNav";
import type { RoomApi } from "@/pages/Room/api";
import type { FixturesView, RoomStatus } from "@/pages/Room/types";
import { friendlyName } from "@/pages/Room/words";
import { Group, Hint, LinkRow, ReadRow, Sub } from "./Group";

export function LightsGroup({ api, status }: { api: RoomApi; status: RoomStatus | null }) {
  return (
    <Group id="lights" title="Lights" icon={<Lightbulb />} summary="Set up lights · the Hue lamps · worked by remote">
      <LightsBody api={api} status={status} />
    </Group>
  );
}

/** Mounted only while the group is open, so a closed group costs no requests. */
function LightsBody({ api, status }: { api: RoomApi; status: RoomStatus | null }) {
  const [fx, setFx] = useState<FixturesView | null | undefined>(undefined);
  useEffect(() => {
    let alive = true;
    api
      .fixtures()
      .then((v) => alive && setFx(v))
      .catch(() => alive && setFx(null));
    return () => {
      alive = false;
    };
  }, [api]);

  const hue = (fx?.fixtures ?? []).filter((f) => f.kind === "hue");
  const subs = status?.subsystems ?? {};
  const rooms = status?.rooms ?? [];
  const blinds = rooms.reduce((n, r) => n + (r.blinds?.length ?? 0), 0);
  const effects = rooms.flatMap((r) => r.effects ?? []).filter((e) => !!subs[e.power.split(".")[0]]);
  const bridge = fx?.hue;

  return (
    <>
      <LinkRow href={lightsPageUrl()} state={{ from: "Settings" }}>
        Set up lights: add a light, name it, place it on the map
      </LinkRow>

      <Sub>Hue lamps</Sub>
      {fx === undefined ? (
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
        <Hint>{fx === null ? "The room isn't answering." : "No Hue lamps."}</Hint>
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
          <Hint>Their remote codes were learned once and are read only here.</Hint>
        </>
      )}

      <LinkRow href="/room#lights">Switch the lights on the Room page</LinkRow>
    </>
  );
}
