// A row of small buttons, one per event a schedule listens for: a room's actions (its non-screen schedules, e.g. the
// cloud's "Develop theme"), or one screen's events. Which events, their labels and the scope they go to all come from
// the schedules (routes/scheduler_room.py): names starting with "_" and triggers with "room": false never show.
// A tap throws the event (POST /api/schedulers/events/throw) and the button says "Sent" for a moment.
// The one chip component for schedule events (the Room page's Today's art and screen cards, the Schedules page): give it
// a room (read through the shared useRoomSchedule) or the events themselves. Labels are friendly (the schedule's label,
// else the event name in words), "_" names and room:false triggers are left out here too, an optional lead-in line sits
// above the chips, and with `describe` each event's description (when the schedule gives one) shows under its button.
// No frame or page styling: the caller places it (a card, a disclosure).

import { useEffect, useRef, useState } from "react";
import { Check, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { throwRoomEvent, type RoomEvent } from "@/shared/api/roomSchedule";
import { refreshRoomSchedule, useRoomSchedule } from "./useRoomSchedule";

export interface EventsListProps {
  /** The room (rooms.json id) whose schedules to read (re-read after a send). */
  room?: string;
  /** The events to show, instead of reading the room's (e.g. the Schedules page's own list). */
  events?: (RoomEvent & { room?: boolean })[] | null;
  /** A destination (a screen) for its own events; left out: the room's actions. */
  destination?: string;
  /** Shown when there are no events (default: nothing at all). */
  empty?: string;
  className?: string;
  /** Told about each event sent (e.g. the page's log). */
  onSent?: (ev: RoomEvent) => void;
  /** A line above the buttons (e.g. "Ask the schedule for new ideas:"). */
  lead?: string;
  /** Show each event's description under its button, when it has one. */
  describe?: boolean;
}

/** "Develop_theme" -> "Develop theme"; "toggleAnimation" -> "Toggle animation". */
function words(name: string): string {
  const w = name
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/[_-]+/g, " ")
    .trim()
    .toLowerCase();
  return w ? w[0].toUpperCase() + w.slice(1) : name;
}

/** The button's words: the schedule's label, unless it is just the raw event name. */
export function eventLabel(ev: Pick<RoomEvent, "event" | "label">): string {
  const l = (ev.label ?? "").trim();
  return l && l !== ev.event && !/[_]/.test(l) ? l : words(l || ev.event);
}

/** The events a person should see: no "_" names, no triggers marked room: false. */
export function visibleEvents<T extends Pick<RoomEvent, "event"> & { room?: boolean }>(events: T[]): T[] {
  return events.filter((ev) => !ev.event.startsWith("_") && ev.room !== false);
}

/** The events for a room (its actions) or one of its screens, from the shared read. */
export function useRoomEvents(room: string | null | undefined, destination?: string): RoomEvent[] | null {
  const { data } = useRoomSchedule(room);
  if (!data) return null;
  return visibleEvents(destination ? data.screens?.[destination] ?? [] : data.actions ?? []);
}

export function EventsList({ room, events: given, destination, empty, className, onSent, lead, describe = false }: EventsListProps) {
  const read = useRoomEvents(given ? null : room, destination);
  const events = given ? visibleEvents(given) : read;
  const [state, setState] = useState<Record<string, "sending" | "sent">>({});
  const [error, setError] = useState<string | null>(null);
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);
  useEffect(() => () => timers.current.forEach(clearTimeout), []);

  if (!events) return null;
  if (!events.length) return empty ? <p className={cn("text-sm text-muted-foreground", className)}>{empty}</p> : null;

  const send = async (ev: RoomEvent) => {
    const key = `${ev.scope}|${ev.event}`;
    setError(null);
    setState((s) => ({ ...s, [key]: "sending" }));
    try {
      await throwRoomEvent(ev);
      setState((s) => ({ ...s, [key]: "sent" }));
      onSent?.(ev);
      timers.current.push(
        setTimeout(() => {
          setState((s) => {
            const { [key]: _gone, ...rest } = s;
            return rest;
          });
          if (room) void refreshRoomSchedule(room);
        }, 2500),
      );
    } catch (e) {
      setState((s) => {
        const { [key]: _gone, ...rest } = s;
        return rest;
      });
      setError(`${eventLabel(ev)}: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const button = (ev: RoomEvent) => {
    const st = state[`${ev.scope}|${ev.event}`];
    const label = eventLabel(ev);
    return (
      <Button
        key={`${ev.scope}|${ev.event}`}
        type="button"
        variant="outline"
        size="sm"
        title={ev.description ?? undefined}
        aria-label={st === "sent" ? `${label}: sent` : label}
        disabled={st === "sending"}
        onClick={() => void send(ev)}
        className={cn("shrink-0 rounded-full bg-card", st === "sent" && "border-primary/50 text-primary")}
      >
        {st === "sending" && <Loader2 aria-hidden className="motion-safe:animate-spin" />}
        {st === "sent" && <Check aria-hidden />}
        {st === "sent" ? "Sent" : label}
      </Button>
    );
  };
  const described = describe && events.some((ev) => ev.description?.trim());

  return (
    <div className={className}>
      {lead && <p className="mb-2 text-sm text-muted-foreground">{lead}</p>}
      {described ? (
        // A row per event: the button, and what it does under it.
        <ul className="space-y-2">
          {events.map((ev) => (
            <li key={`${ev.scope}|${ev.event}`}>
              {button(ev)}
              {ev.description?.trim() && <p className="mt-1 px-1 text-sm leading-snug text-muted-foreground">{ev.description}</p>}
            </li>
          ))}
        </ul>
      ) : (
        <div className="flex flex-wrap gap-2">{events.map(button)}</div>
      )}
      {error && (
        <p role="alert" className="mt-2 text-sm text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}
