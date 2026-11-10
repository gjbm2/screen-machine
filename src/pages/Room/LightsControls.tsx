// The room's lights, worked from the Room page's Lights fold and the Lights page (/lights): House lights and Lights low
// (RoomLightsRow.tsx), then each room's lamps (Fixtures.tsx) and its appliances worked by remote (Effects.tsx: the
// glitterball, pinspots). Operating only; setting them up is in Settings (the light setup page).

import { ChevronRight } from "lucide-react";
import { AppLink } from "@/components/nav/AppLink";
import { lightSetupUrl } from "@/components/nav/appNav";
import { EffectsSection } from "./Effects";
import { LightsSection } from "./Fixtures";
import { rigLightIds } from "./lightStates";
import type { ShowFeed } from "./MusicCard";
import { RoomLightsRow } from "./RoomLightsRow";
import type { Section } from "./sections";
import type { RoomApi } from "./api";
import type { RoomDescribe, RoomStatus } from "./types";
import type { FixturesState } from "./useFixtures";
import type { NewLogEntry } from "./useRoomConnection";

export function LightsControls({
  api,
  connected,
  addLog,
  reportError,
  fixtures,
  show,
  status,
  describe,
  groups,
  lampIds,
  flat,
}: {
  api: RoomApi;
  connected: boolean;
  addLog: (e: NewLogEntry) => void;
  reportError: (e: unknown) => void;
  fixtures: FixturesState;
  show: ShowFeed;
  status: RoomStatus | null;
  describe: RoomDescribe | null;
  /** The rooms with lights or appliances (sections.ts). */
  groups: Section[];
  /** The lamps the room has (null while loading). */
  lampIds: string[] | null;
  /** No rooms (an older room_service): one flat list. */
  flat: boolean;
}) {
  // House lights and Lights low act on the Hue lamps and the DMX lights alike (show_service's house/low)
  const anyLights = lampIds === null || lampIds.length > 0 || (rigLightIds(status) ?? []).length > 0;
  return (
    <>
      {anyLights && <RoomLightsRow api={api} feed={show} addLog={addLog} />}
      {!flat && groups.length === 0 && (
        <p className="px-1 text-base text-muted-foreground" data-testid="no-lights">
          No lights yet ·{" "}
          <AppLink href={lightSetupUrl()} className="font-medium text-teal-800 underline-offset-4 hover:underline">
            Set up lights <ChevronRight className="inline h-4 w-4 align-[-2px]" aria-hidden />
          </AppLink>
        </p>
      )}
      {!flat ? (
        groups.map((sec) => (
          <div key={sec.key} className="space-y-3">
            {sec.lights && (
              <LightsSection
                api={api}
                connected={connected}
                addLog={addLog}
                reportError={reportError}
                fixtures={fixtures}
                only={sec.lights}
                title={sec.title}
                showTitle={groups.filter((g) => !!g.lights).length > 1 ? true : undefined}
                sub
              />
            )}
            {status && sec.effects.length > 0 && (
              <EffectsSection
                effects={sec.effects}
                status={status}
                describe={describe}
                api={api}
                connected={connected}
                addLog={addLog}
                reportError={reportError}
              />
            )}
          </div>
        ))
      ) : (
        <LightsSection
          api={api}
          connected={connected}
          addLog={addLog}
          reportError={reportError}
          fixtures={fixtures}
          title="Lights"
          sub
        />
      )}
    </>
  );
}
