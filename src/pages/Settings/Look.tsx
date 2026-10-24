// Settings › Look (#look): how the room picks the look, and the feels it knows. While a room's screens are live, what
// it picks scenes from and how often (the Room page's LookSettings, "picking" part, moved here from the Look fold's
// "What it picks from, and how often"); otherwise a line saying when it shows. The feels (vibes.yaml, GET
// /show/api/v1/vibes) are read only for now: each one's name, what it's for and its palettes. Calmer, Wilder, the
// palette and Next look stay on the Room page, where they change the room at once.

import { useEffect, useState } from "react";
import { Palette } from "lucide-react";
import { rendererConsoleUrl } from "@/components/nav/appNav";
import { useRoomLink } from "@/components/room/roomLink";
import type { RoomApi } from "@/pages/Room/api";
import { Spinner, Unroll } from "@/pages/Room/bits";
import { LookSettings } from "@/pages/Room/LivePanel";
import type { LiveMember } from "@/pages/Room/roomFx";
import type { Library, RoomStatus } from "@/pages/Room/types";
import { useRoomLive } from "@/pages/Room/useRoomLive";
import type { NewLogEntry, StreamEvent } from "@/pages/Room/useRoomConnection";
import { liveOf } from "@/pages/Room/useScreenControls";
import { friendlyName } from "@/pages/Room/words";
import { Group, Hint, LinkRow, Sub } from "./Group";

interface Vibe {
  label?: string;
  description?: string;
  fields?: { palettes?: string[]; energy?: number[] };
}
interface VibesView {
  order?: string[];
  vibes?: Record<string, Vibe>;
}

type Shared = {
  api: RoomApi;
  lib: Library | null;
  subscribe: (fn: (e: StreamEvent) => void) => () => void;
  addLog: (e: NewLogEntry) => void;
  reportError: (e: unknown) => void;
};

function RoomPicking({ roomId, roomName, members, ...s }: Shared & { roomId: string; roomName: string; members: LiveMember[] }) {
  const rl = useRoomLive({ ...s, roomId, roomName, members });
  if (!rl.lead || !rl.look) {
    return (
      <p className="flex items-center gap-2 text-[15px] text-muted-foreground">
        <Spinner /> Reading {roomName}'s look…
      </p>
    );
  }
  const both = members.map((m) => m.name).join(" and ");
  return <LookSettings screenId={rl.lead.id} lv={rl} lib={s.lib} api={s.api} parts="picking" words={`Changes here go to ${both}.`} />;
}

export function LookGroup(props: Shared & { status: RoomStatus | null }) {
  return (
    <Group id="look" title="Look" icon={<Palette />} summary="How the room picks its look · the feels">
      <LookBody {...props} />
    </Group>
  );
}

/** Mounted only while the group is open, so a closed group costs no requests. */
function LookBody({ status, ...s }: Shared & { status: RoomStatus | null }) {
  const link = useRoomLink();
  const [vibes, setVibes] = useState<VibesView | null>(null);
  useEffect(() => {
    let alive = true;
    fetch(`${s.api.baseUrl}/show/api/v1/vibes`, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((v) => alive && setVibes(v))
      .catch(() => alive && setVibes(null));
    return () => {
      alive = false;
    };
  }, [s.api.baseUrl]);

  // each room of several screens, with its live screens (as the Room page's Look fold)
  const rooms = (status?.rooms ?? [])
    .filter((r) => (r.screens ?? []).length > 1)
    .map((r) => ({
      id: r.id,
      name: r.name || r.id,
      members: (r.screens ?? []).flatMap((id): LiveMember[] => {
        const sc = status?.screens?.[id];
        const ph = sc?.observed?.phase;
        return sc && liveOf(sc) && (ph === "live" || ph === "entering") && sc.observed?.session
          ? [{ id, name: sc.name || friendlyName(id), session: sc.observed.session }]
          : [];
      }),
    }));
  const order = vibes?.order?.length ? vibes.order : Object.keys(vibes?.vibes ?? {});

  return (
    <>
      <Sub>How the room picks</Sub>
      {rooms.length === 0 && <Hint>Shows here for a room with more than one screen.</Hint>}
      {rooms.map((r) =>
        r.members.length > 0 && s.lib ? (
          <div key={r.id} className="space-y-2">
            {rooms.length > 1 && <p className="text-base font-semibold">{r.name}</p>}
            <RoomPicking roomId={r.id} roomName={r.name} members={r.members} {...s} />
          </div>
        ) : (
          <Hint key={r.id}>What {r.name} picks from, and how often, shows here while its screens are live.</Hint>
        ),
      )}

      <Sub>Feels</Sub>
      <Hint>Each mode follows one of these. Read only here for now.</Hint>
      {vibes === null ? (
        <Hint>Not read yet: the music side may be starting.</Hint>
      ) : (
        <div className="space-y-1">
          {order.map((k) => {
            const v = vibes.vibes?.[k];
            if (!v) return null;
            return (
              <Unroll key={k} title={v.label || k} testId={`settings-vibe-${k}`}>
                <div className="space-y-1 pb-2 text-[15px] leading-snug">
                  {v.description && <p>{v.description}</p>}
                  {!!v.fields?.palettes?.length && <p className="text-muted-foreground">Palettes: {v.fields.palettes.join(", ")}</p>}
                  {Array.isArray(v.fields?.energy) && v.fields.energy.length === 2 && (
                    <p className="text-muted-foreground">
                      Energy: {Math.round(v.fields.energy[0] * 100)}–{Math.round(v.fields.energy[1] * 100)}%
                    </p>
                  )}
                </div>
              </Unroll>
            );
          })}
        </div>
      )}

      <div className="flex flex-wrap gap-x-4">
        <LinkRow href="/room#look">Steer the look on the Room page</LinkRow>
        {link.onLan && (
          <LinkRow href={rendererConsoleUrl()} newTab>
            Scene library
          </LinkRow>
        )}
      </div>
    </>
  );
}
