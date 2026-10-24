// The Room page's "Today's art" fold (layout A, owner 24 Oct): each room's TodayCard (src/components/room, unchanged)
// in one Section, with its headline as the closed summary. The rooms' schedules come from the shared read the cards
// use themselves (useRoomSchedule: one request per room), so the summary fetches nothing new. Hidden while no room
// has anything to show.

import { useCallback, useEffect, useState } from "react";
import { TodayCard } from "@/components/room/TodayCard";
import { useRoomSchedule } from "@/components/room/useRoomSchedule";
import { Section } from "./Section";
import { CARD_CLASS } from "./theme";

interface Probe {
  /** The room has variables or actions to show. */
  has: boolean;
  /** Its first set variable, in a line ("Theme: autumn harbour"), or null. */
  headline: string | null;
}

function headlineOf(data: ReturnType<typeof useRoomSchedule>["data"]): Probe | null {
  if (!data) return null;
  const has = data.variables.length > 0 || data.actions.length > 0;
  const v = data.variables.find((x) => typeof x.value === "string" && x.value.trim());
  return { has, headline: v ? `${v.label}: ${String(v.value).trim()}` : null };
}

/** Reads one room's schedule from the shared store and reports what the fold needs. Draws nothing. */
function TodayProbe({ room, onProbe }: { room: string; onProbe: (room: string, p: Probe | null) => void }) {
  const { data } = useRoomSchedule(room);
  const p = headlineOf(data);
  const has = p?.has ?? null;
  const headline = p?.headline ?? null;
  useEffect(() => {
    onProbe(room, has === null ? null : { has, headline });
  }, [room, has, headline, onProbe]);
  return null;
}

export function TodayFold({ rooms, onChange }: { rooms: { id: string; name: string }[]; onChange: (what: string) => void }) {
  const [probes, setProbes] = useState<Record<string, Probe | null>>({});
  const onProbe = useCallback((room: string, p: Probe | null) => {
    setProbes((prev) => (prev[room]?.has === p?.has && prev[room]?.headline === p?.headline ? prev : { ...prev, [room]: p }));
  }, []);
  const withArt = rooms.filter((r) => probes[r.id]?.has);
  const summary = withArt.map((r) => probes[r.id]?.headline).find(Boolean) ?? null;
  return (
    <>
      {rooms.map((r) => (
        <TodayProbe key={r.id} room={r.id} onProbe={onProbe} />
      ))}
      <Section id="today" title="Today's art" summary={summary ?? "What the screens are making today"} hidden={withArt.length === 0}>
        {rooms.map((r) => (
          <TodayCard key={r.id} room={r.id} title={withArt.length > 1 ? r.name : "Today"} className={CARD_CLASS} onChange={onChange} />
        ))}
      </Section>
    </>
  );
}
