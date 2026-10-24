// A row of small buttons, one per event a schedule listens for: a room's actions (its non-screen schedules, e.g. the
// cloud's "Develop theme"), or one screen's events. Which events, their labels and the scope they go to all come from
// the schedules (routes/scheduler_room.py): names starting with "_" and triggers with "room": false never show.
// A tap throws the event (POST /api/schedulers/events/throw) and the button says "Sent" for a moment.
// No frame or page styling: the caller places it (a card, a disclosure).

import { useEffect, useRef, useState } from "react";
import { Check, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { throwRoomEvent, type RoomEvent } from "@/shared/api/roomSchedule";
import { refreshRoomSchedule, useRoomSchedule } from "./useRoomSchedule";

export interface EventsListProps {
  /** The room (rooms.json id) whose schedules to read. */
  room: string;
  /** A destination (a screen) for its own events; left out: the room's actions. */
  destination?: string;
  /** Shown when there are no events (default: nothing at all). */
  empty?: string;
  className?: string;
  /** Told about each event sent (e.g. the page's log). */
  onSent?: (ev: RoomEvent) => void;
}

/** The events for a room (its actions) or one of its screens, from the shared read. */
export function useRoomEvents(room: string, destination?: string): RoomEvent[] | null {
  const { data } = useRoomSchedule(room);
  if (!data) return null;
  return destination ? data.screens[destination] ?? [] : data.actions;
}

export function EventsList({ room, destination, empty, className, onSent }: EventsListProps) {
  const events = useRoomEvents(room, destination);
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
          void refreshRoomSchedule(room);
        }, 2500),
      );
    } catch (e) {
      setState((s) => {
        const { [key]: _gone, ...rest } = s;
        return rest;
      });
      setError(`${ev.label}: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  return (
    <div className={className}>
      <div className="flex flex-wrap gap-2">
        {events.map((ev) => {
          const st = state[`${ev.scope}|${ev.event}`];
          return (
            <Button
              key={`${ev.scope}|${ev.event}`}
              type="button"
              variant="outline"
              size="sm"
              title={ev.description ?? undefined}
              disabled={st === "sending"}
              onClick={() => void send(ev)}
              className={cn("rounded-full bg-card", st === "sent" && "border-primary/50 text-primary")}
            >
              {st === "sending" && <Loader2 aria-hidden className="motion-safe:animate-spin" />}
              {st === "sent" && <Check aria-hidden />}
              {st === "sent" ? "Sent" : ev.label}
            </Button>
          );
        })}
      </div>
      {error && (
        <p role="alert" className="mt-2 text-sm text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}
