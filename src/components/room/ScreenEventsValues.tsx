// A screen's schedule events and variables, under its strip's schedule chip (ScreenStrip.tsx), so they show on both
// the Generator's screen tabs and the Room page's screen cards (owner, 28 Oct: "I'd expect to see events & variables
// exposed in a suitable manner in both places too").
//
// Events: the events the screen's schedule listens for, by the Room page's rule (scheduleEvents() on the top schedule
//   of its stack: no "_" names, no "room": false), as EventsList's chips; a tap sends one to this screen.
// Variables: the room's shared values this screen uses (its room's exported variables, TodayCard's rows, with Edit),
//   then, folded, the screen's own values (its schedule's context; "_" names are internal and left out): one line
//   each, a tap shows all of it and Edit (POST /api/schedulers/<id>/context). The schedule uses a change on its next run.
// The stack and the context are read when this opens, and again after a change. The room is the caller's, else the
// room link's (on the LAN); without one, the shared values aren't shown (the screen's own values still are).

import { useEffect, useState } from "react";
import { ChevronDown, Pencil } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import { schedulerApi } from "@/shared/api";
import type { RoomEvent } from "@/shared/api/roomSchedule";
import { labelFor, scheduleEvents } from "@/components/scheduler/scheduleWords";
import { Unroll } from "@/pages/Room/bits";
import { EventsList } from "./EventsList";
import { useRoomLinkSelect } from "./roomLink";
import { VariableRow } from "./TodayCard";
import { useRoomSchedule } from "./useRoomSchedule";

/** The screen's events (from its top schedule) and its own values (its context), re-read on reload(). */
function useScreenScheduleRead(screenId: string) {
  const [events, setEvents] = useState<RoomEvent[] | null>(null);
  const [values, setValues] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rev, setRev] = useState(0);
  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const r = await schedulerApi.getScheduleStack(screenId);
        const stack: unknown[] = Array.isArray(r?.stack) ? r.stack : [];
        if (!alive) return;
        setEvents(scheduleEvents(stack[stack.length - 1] ?? null).map((ev) => ({ ...ev, scope: screenId })));
        setError(null);
      } catch (e) {
        if (!alive) return;
        setEvents((old) => old ?? []);
        setError(e instanceof Error ? e.message : String(e));
      }
      try {
        const c = await schedulerApi.getSchedulerContext(screenId);
        if (alive) setValues(c?.vars && typeof c.vars === "object" ? c.vars : {});
      } catch {
        // no schedule has run here yet: nothing to show
        if (alive) setValues((old) => old ?? {});
      }
    })();
    return () => {
      alive = false;
    };
  }, [screenId, rev]);
  return { events, values, error, reload: () => setRev((n) => n + 1) };
}

function valueText(v: unknown): string {
  if (v === null || v === undefined) return "";
  return typeof v === "string" ? v : JSON.stringify(v);
}

/** The draft back in the value's own type: a string stays a string; a number, true/false or JSON is read as one. */
function parseLike(old: unknown, draft: string): unknown {
  if (typeof old === "string") return draft;
  if (typeof old === "number" && draft.trim() !== "" && !Number.isNaN(Number(draft))) return Number(draft);
  if (typeof old === "boolean" && /^(true|false)$/i.test(draft.trim())) return draft.trim().toLowerCase() === "true";
  try {
    return JSON.parse(draft);
  } catch {
    return draft;
  }
}

/** One of the screen's own values: "Room mode · gallery"; a tap shows all of it and Edit. */
function OwnValueRow({ screenId, name, value, onSaved }: { screenId: string; name: string; value: unknown; onSaved: () => void }) {
  const text = valueText(value);
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const expanded = open || draft !== null;
  const label = labelFor(name);

  const save = async () => {
    if (draft === null) return;
    setBusy(true);
    setError(null);
    try {
      await schedulerApi.setSchedulerContextVar(screenId, name, parseLike(value, draft));
      setDraft(null);
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <li className="min-w-0">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={expanded}
        disabled={draft !== null}
        title={name}
        className="flex min-h-10 w-full min-w-0 items-center gap-2 rounded-lg px-2 text-left hover:bg-stone-100/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-default disabled:hover:bg-transparent"
      >
        <span className="min-w-0 flex-1 truncate text-[14px] leading-snug">
          <span className="font-semibold">{label}</span>
          {!expanded && <span className={text ? "text-stone-700" : "text-muted-foreground"}> · {text || "empty"}</span>}
        </span>
        <ChevronDown
          aria-hidden
          className={cn("h-4 w-4 shrink-0 text-muted-foreground transition-transform motion-reduce:transition-none", expanded && "rotate-180")}
        />
      </button>
      {expanded && (
        <div className="space-y-2 px-2 pb-2">
          {draft !== null ? (
            <div className="space-y-2">
              <Textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                rows={Math.min(8, Math.max(2, Math.ceil(draft.length / 60)))}
                className="bg-card text-base"
                aria-label={label}
                autoFocus
              />
              <div className="flex flex-wrap items-center gap-2">
                <Button type="button" size="sm" disabled={busy} onClick={() => void save()}>
                  {busy ? "Saving…" : "Save"}
                </Button>
                <Button type="button" size="sm" variant="outline" className="bg-card" disabled={busy} onClick={() => setDraft(null)}>
                  Cancel
                </Button>
                <span className="text-sm text-muted-foreground">The schedule uses it on its next run.</span>
              </div>
              {error && (
                <p role="alert" className="text-sm text-destructive">
                  {error}
                </p>
              )}
            </div>
          ) : (
            <p
              className={cn(
                "max-h-48 overflow-y-auto whitespace-pre-wrap break-words text-[15px] leading-relaxed",
                !text && "text-muted-foreground",
              )}
            >
              {text || "empty"}
            </p>
          )}
          {draft === null && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-9 bg-card px-3"
              aria-label={`Change ${label}`}
              onClick={() => setDraft(text)}
            >
              <Pencil aria-hidden /> Edit
            </Button>
          )}
        </div>
      )}
    </li>
  );
}

const HEAD = "mb-1 text-[14px] font-semibold text-stone-800";

export function ScreenEventsValues({ screenId, room: givenRoom }: { screenId: string; room?: string | null }) {
  const linkRoom = useRoomLinkSelect((s) => s.status?.rooms?.find((r) => r.screens?.includes(screenId))?.id ?? null);
  const room = givenRoom ?? linkRoom;
  const { data: roomData } = useRoomSchedule(room);
  const { events, values, error, reload } = useScreenScheduleRead(screenId);

  const shared = (roomData?.variables ?? []).filter((v) => v.owner === screenId || v.used_by.some((u) => u.id === screenId));
  const own = Object.entries(values ?? {}).filter(([k]) => !k.startsWith("_"));

  return (
    <div className="space-y-3 text-[14px]" data-testid={`events-values-${screenId}`}>
      <section aria-label="Events">
        <h4 className={HEAD}>Events</h4>
        {events === null ? (
          <p className="text-muted-foreground">Reading the schedule…</p>
        ) : (
          <EventsList events={events} empty="Its schedule listens for no events." />
        )}
        {error && <p className="mt-1 text-sm text-muted-foreground">Couldn't read the schedule: {error}</p>}
      </section>

      <section aria-label="Variables">
        <h4 className={HEAD}>Variables</h4>
        {shared.length > 0 && room && (
          <ul className="-mx-2 grid gap-x-4">
            {shared.map((v) => (
              <VariableRow key={`${v.owner}|${v.name}`} v={v} room={room} />
            ))}
          </ul>
        )}
        {values === null ? (
          <p className="text-muted-foreground">Reading its values…</p>
        ) : own.length ? (
          <Unroll
            title={shared.length ? "This screen's own values" : "Its values"}
            hint={`${own.length}`}
            className="-mx-1"
            contentClassName="pt-1"
            testId={`own-values-${screenId}`}
          >
            <ul className="-mx-1">
              {own.map(([k, v]) => (
                <OwnValueRow key={k} screenId={screenId} name={k} value={v} onSaved={reload} />
              ))}
            </ul>
          </Unroll>
        ) : (
          !shared.length && <p className="text-muted-foreground">No values yet: its schedule hasn't set any.</p>
        )}
      </section>
    </div>
  );
}
