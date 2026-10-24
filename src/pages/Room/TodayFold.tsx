// The Room page's "Today's art" fold (layout A, owner 24 Oct): each room's TodayCard (src/components/room) in one
// Section, with the day's theme itself as the closed summary (its first sentence, or its name). The rooms' schedules
// come from the shared read the cards use themselves (useRoomSchedule: one request per room), so the summary fetches
// nothing new. Hidden while no room has anything to show. With one room the card has no heading of its own (the
// fold's title says it); each card ends with "Schedules ›" (/scheduler?room=<id>), where the day is set and changed.

import { useCallback, useEffect, useState } from "react";
import { ChevronRight } from "lucide-react";
import { AppLink } from "@/components/nav/AppLink";
import { TodayCard } from "@/components/room/TodayCard";
import { useRoomSchedule } from "@/components/room/useRoomSchedule";
import { Section } from "./Section";
import { CARD_CLASS } from "./theme";

interface Probe {
  /** The room has variables or actions to show. */
  has: boolean;
  /** The day's theme in a line: its first set variable's first sentence ("Today's art theme is 'Diplomacy of
   *  Colors', inspired by United Nations Day."), or that variable's name; null when nothing is set. */
  headline: string | null;
}

/** The first sentence of a text, or the whole text when it has no sentence end. */
function firstSentence(text: string): string {
  const t = text.trim().replace(/\s+/g, " ");
  const m = /^(.+?[.!?])(?=\s|$)/.exec(t);
  return m ? m[1] : t;
}

function headlineOf(data: ReturnType<typeof useRoomSchedule>["data"]): Probe | null {
  if (!data) return null;
  const has = (data.variables?.length ?? 0) > 0 || (data.actions?.length ?? 0) > 0;
  const v = (data.variables ?? []).find((x) => typeof x.value === "string" && x.value.trim());
  return { has, headline: v ? firstSentence(String(v.value)) || v.label : null };
}

const LINK = "inline-flex min-h-11 items-center gap-1 rounded-xl px-1 text-[15px] font-medium text-teal-800 hover:underline";

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
          <TodayCard
            key={r.id}
            room={r.id}
            title={withArt.length > 1 ? r.name : null}
            className={CARD_CLASS}
            onChange={onChange}
            footer={
              <AppLink href={`/scheduler?room=${encodeURIComponent(r.id)}`} state={{ from: "Room" }} className={LINK} data-testid={`today-schedules-${r.id}`}>
                {withArt.length > 1 ? `${r.name} schedules` : "Schedules"} <ChevronRight className="h-4 w-4" aria-hidden />
              </AppLink>
            }
          />
        ))}
      </Section>
    </>
  );
}
