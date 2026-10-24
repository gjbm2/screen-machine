// Settings › Music & piano (#music): what the virtual piano has and where it can play, read only (GET
// /show/api/v1/test: the installed sounds and the room's usual one, the places it can play; GET …/piano/devices: the
// phones and tablets that have played it), and the recordings (GET …/piano/recordings). The sound, speed and Play on
// are chosen on the player itself; the recordings are kept, named and played under Music › Mine.

import { useEffect, useState } from "react";
import { Piano } from "lucide-react";
import { musicPageUrl } from "@/components/nav/appNav";
import type { RoomApi } from "@/pages/Room/api";
import { Unroll } from "@/pages/Room/bits";
import { fetchDevices, myOutput, type DevicesView } from "@/pages/Room/listenStream";
import type { PianoRecordingsView, ShowTestView } from "@/pages/Room/types";
import { Group, Hint, LinkRow, ReadRow, Sub } from "./Group";

const ago = (s: number | undefined) =>
  typeof s !== "number" ? "" : s < 90 ? "just now" : s < 5400 ? `${Math.round(s / 60)} min ago` : s < 129600 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} days ago`;

export function MusicGroup({ api, showTest }: { api: RoomApi; showTest: ShowTestView | null }) {
  const usual = showTest?.instruments?.find((i) => i.id === showTest?.default_instrument)?.label ?? showTest?.default_instrument ?? null;
  return (
    <Group id="music" title="Music & piano" icon={<Piano />} summary={usual ? `Usual sound: ${usual}` : "The piano's sounds and recordings"}>
      <MusicBody api={api} showTest={showTest} />
    </Group>
  );
}

/** Mounted only while the group is open, so a closed group costs no requests. */
function MusicBody({ api, showTest }: { api: RoomApi; showTest: ShowTestView | null }) {
  const [devices, setDevices] = useState<DevicesView | null>(null);
  const [recs, setRecs] = useState<PianoRecordingsView | null>(null);
  useEffect(() => {
    const ac = new AbortController();
    void fetchDevices(ac.signal, api.baseUrl).then((v) => !ac.signal.aborted && setDevices(v));
    api
      .pianoRecordings(ac.signal)
      .then((v) => !ac.signal.aborted && setRecs(v))
      .catch(() => !ac.signal.aborted && setRecs(null));
    return () => ac.abort();
  }, [api]);

  const sounds = showTest?.instruments ?? [];
  const usual = sounds.find((i) => i.id === showTest?.default_instrument)?.label ?? showTest?.default_instrument ?? null;
  const places = (showTest?.outputs ?? []).filter((o) => !o.device);
  const taken = recs?.recordings ?? [];

  return (
    <>
      <Sub>The piano's sound</Sub>
      {showTest ? (
        <>
          {usual && <ReadRow label="The room's usual sound" value={usual} />}
          <Unroll title="Sounds installed" hint={`${sounds.length}`} testId="settings-music-sounds">
            <ul className="space-y-1 pb-2 text-[15px] leading-snug">
              {sounds.map((s) => (
                <li key={s.id}>{s.label}</li>
              ))}
            </ul>
          </Unroll>
          <Hint>Pick the sound for what plays next on the player (Sound), on Music or the Room page.</Hint>
        </>
      ) : (
        <Hint>Not read yet: the music side may be starting.</Hint>
      )}

      <Sub>Where it plays</Sub>
      {places.length > 0 && <ReadRow label="Places" value={places.map((o) => o.label).join(", ")} />}
      {devices && devices.devices.length > 0 && (
        <Unroll title="Phones and tablets that have played it" hint={`${devices.devices.length}`} testId="settings-music-devices">
          <ul className="space-y-1 pb-2 text-[15px] leading-snug">
            {devices.devices.map((d) => (
              <li key={d.id}>
                {d.label}
                {d.id === myOutput() ? " (this device)" : ""}
                <span className="text-muted-foreground">{d.seen_s !== undefined ? ` · seen ${ago(d.seen_s)}` : ""}</span>
              </li>
            ))}
          </ul>
        </Unroll>
      )}
      <Hint>Choose where it plays with Play on, on the player. Name this device under This device.</Hint>

      <Sub>Recordings</Sub>
      {recs ? (
        <ReadRow label="Kept" value={taken.length ? `${taken.length} · newest “${taken[0].title}”` : "None yet"} />
      ) : (
        <Hint>Not read yet.</Hint>
      )}
      <LinkRow href={musicPageUrl("mine")}>Recordings in Music</LinkRow>
    </>
  );
}
