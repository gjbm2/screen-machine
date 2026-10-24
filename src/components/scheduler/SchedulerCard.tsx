// One screen's schedule on the Schedules page (/scheduler). Closed, it is one row: the name, a status dot and when it
// next runs, in words ("Next: Generate at 20:04 (in 13 min) · every 2 h until midnight"). Open, in this order:
//   1. what to do with it: Pause / Resume (or Start), Edit schedule (or New schedule), Stop;
//   2. "Run now": its events as buttons, with the Room page's labels and rule (EventsList for a room's screen; the
//      same rule read from the schedule itself for a screen in no room); a schedule behind a room's actions (the
//      cloud's) shows that room's actions;
//   3. "How often": the top schedule's timed blocks in plain controls (HowOften.tsx), "Edit everything…" for the rest;
//   4. "Waiting to run": the instruction queue, when anything waits;
//   5. Advanced: New schedule on top, Remove the top schedule, each schedule of the stack (Edit, Change a value, its
//      values and script), the log, and every event by its raw name.
// The schedule stack and its values are read only while the card is open (and again after a change); the queue is
// polled every 5 s and the log every 5 s only while they are shown and the page is visible.

import { useCallback, useEffect, useRef, useState } from "react";
import { Check, ChevronDown, Loader2, Pause, Pencil, Play, Plus, Square } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { eventsApi, schedulerApi } from "@/shared/api";
import { throwRoomEvent } from "@/shared/api/roomSchedule";
import { EventsList } from "@/components/room/EventsList";
import { Dot, Unroll } from "@/pages/Room/bits";
import { CARD_CLASS } from "@/pages/Room/theme";
import { HowOften } from "./HowOften";
import { SetVarsModal } from "./SetVarsModal";
import {
  allEventNames,
  nextLine,
  runState,
  scheduleEvents,
  STATE_WORDS,
  timedTriggers,
  type SchedStatus,
  type ScheduleEvent,
} from "./scheduleWords";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Json = any;

export interface ScheduleDest {
  id: string;
  name: string;
}

export interface RoomRef {
  id: string;
  name: string;
}

export interface SchedulerCardProps {
  dest: ScheduleDest;
  status?: SchedStatus;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Arrived here for this screen (?destination=): the teal ring. */
  highlight?: boolean;
  /** The room this screen is in: its events come from the room's schedule read, labelled as on the Room page. */
  room?: RoomRef | null;
  /** The rooms whose actions this schedule serves (a behind-the-scenes one, e.g. the cloud's). */
  servesRooms?: RoomRef[];
  /** Bumped by the page after a change or a Refresh: the open card reads its stack again. */
  refreshKey: number;
  /** After an action here: the page reads the statuses again. */
  onChanged: () => void;
  /** The full schema editor on schedule `layer` of the stack, or a new schedule on top (null). */
  onEdit: (destId: string, layer: number | null) => void;
}

const BTN = "h-11 rounded-xl px-4 text-base";

function useVisible(): boolean {
  const [v, setV] = useState(() => typeof document === "undefined" || !document.hidden);
  useEffect(() => {
    const on = () => setV(!document.hidden);
    document.addEventListener("visibilitychange", on);
    return () => document.removeEventListener("visibilitychange", on);
  }, []);
  return v;
}

/** Polls `fn` every `ms` while `on` and the page is visible (and once at once). */
function usePoll(fn: () => void, ms: number, on: boolean) {
  const visible = useVisible();
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    if (!on || !visible) return;
    ref.current();
    const id = setInterval(() => ref.current(), ms);
    return () => clearInterval(id);
  }, [on, visible, ms]);
}

export function SchedulerCard({ dest, status, open, onOpenChange, highlight, room, servesRooms = [], refreshKey, onChanged, onEdit }: SchedulerCardProps) {
  const st = runState(status);
  const words = STATE_WORDS[st];
  const line = nextLine(status);
  const bodyId = `schedule-body-${dest.id}`;

  return (
    <article
      id={`schedule-${dest.id}`}
      data-testid={`schedule-${dest.id}`}
      className={cn(
        CARD_CLASS,
        "min-w-0 scroll-mt-[calc(var(--app-top,0px)+1rem)] transition-shadow",
        highlight && "ring-2 ring-teal-600 ring-offset-2 ring-offset-background",
      )}
    >
      <button
        type="button"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={() => onOpenChange(!open)}
        className="flex min-h-16 w-full min-w-0 items-center gap-3 rounded-2xl px-4 py-3 text-left hover:bg-stone-50/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <Dot tone={words.tone} pulse={st === "running"} className="h-2.5 w-2.5" />
        <span className="min-w-0 flex-1">
          <span className="block break-words text-base font-semibold leading-snug">{dest.name}</span>
          <span className="block break-words text-[15px] leading-snug text-muted-foreground" data-testid={`schedule-${dest.id}-line`}>
            {line}
          </span>
        </span>
        <ChevronDown aria-hidden className={cn("h-5 w-5 shrink-0 text-muted-foreground transition-transform motion-reduce:transition-none", open && "rotate-180")} />
      </button>
      {open && (
        <div id={bodyId} className="min-w-0 space-y-4 border-t border-stone-200/80 px-4 pb-4 pt-3">
          <CardBody dest={dest} st={st} room={room} servesRooms={servesRooms} refreshKey={refreshKey} onChanged={onChanged} onEdit={onEdit} />
        </div>
      )}
    </article>
  );
}

function CardBody({
  dest,
  st,
  room,
  servesRooms,
  refreshKey,
  onChanged,
  onEdit,
}: {
  dest: ScheduleDest;
  st: ReturnType<typeof runState>;
  room?: RoomRef | null;
  servesRooms: RoomRef[];
  refreshKey: number;
  onChanged: () => void;
  onEdit: (destId: string, layer: number | null) => void;
}) {
  const [stack, setStack] = useState<Json[] | null>(null);
  const [contexts, setContexts] = useState<Json[]>([]);
  const [readError, setReadError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [actError, setActError] = useState<string | null>(null);
  const [queue, setQueue] = useState<Json[]>([]);
  const [reload, setReload] = useState(0);

  // the stack and its values: when opened, after a change, and when the state changes
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const r = await schedulerApi.getScheduleStack(dest.id);
        if (!alive) return;
        setStack(Array.isArray(r?.stack) ? r.stack : []);
        setReadError(null);
      } catch (e) {
        if (!alive) return;
        setStack((s) => s ?? []);
        setReadError(e instanceof Error ? e.message : String(e));
      }
      try {
        const c = await schedulerApi.getSchedulerContext(dest.id);
        if (!alive) return;
        setContexts(Array.isArray(c?.context_stack) ? c.context_stack : c?.vars ? [c] : []);
      } catch {
        if (alive) setContexts([]);
      }
    })();
    return () => {
      alive = false;
    };
  }, [dest.id, refreshKey, st, reload]);

  // what waits to run: only while this card is open and the page visible
  const readQueue = useCallback(() => {
    schedulerApi
      .getInstructionQueue(dest.id)
      .then((r: Json) => setQueue(Array.isArray(r?.instructions) ? r.instructions : []))
      .catch(() => setQueue([]));
  }, [dest.id]);
  usePoll(readQueue, 5000, st === "running" || st === "paused");

  const act = async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key);
    setActError(null);
    try {
      await fn();
    } catch (e) {
      setActError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
      setReload((n) => n + 1);
      onChanged();
    }
  };

  const top = stack && stack.length ? stack[stack.length - 1] : null;
  const topIndex = stack ? stack.length - 1 : -1;
  const ownEvents = top ? scheduleEvents(top) : [];
  const hasTimed = top ? timedTriggers(top).length > 0 : false;
  const spin = (key: string) => (busy === key ? <Loader2 aria-hidden className="motion-safe:animate-spin" /> : null);

  return (
    <>
      <div className="flex flex-wrap gap-2">
        {st === "running" && (
          <Button type="button" className={BTN} disabled={!!busy} onClick={() => void act("pause", () => schedulerApi.pauseScheduler(dest.id))}>
            {spin("pause") ?? <Pause aria-hidden />} Pause
          </Button>
        )}
        {st === "paused" && (
          <Button type="button" className={BTN} disabled={!!busy} onClick={() => void act("resume", () => schedulerApi.unpauseScheduler(dest.id))}>
            {spin("resume") ?? <Play aria-hidden />} Resume
          </Button>
        )}
        {(st === "stopped" || st === "error") && (
          <Button type="button" className={BTN} disabled={!!busy} onClick={() => void act("start", () => schedulerApi.startScheduler(dest.id, {}))}>
            {spin("start") ?? <Play aria-hidden />} Start
          </Button>
        )}
        {top ? (
          <Button type="button" variant="outline" className={BTN} onClick={() => onEdit(dest.id, topIndex)}>
            <Pencil aria-hidden /> Edit schedule
          </Button>
        ) : (
          stack && (
            <Button type="button" variant="outline" className={BTN} onClick={() => onEdit(dest.id, null)}>
              <Plus aria-hidden /> New schedule
            </Button>
          )
        )}
        {(st === "running" || st === "paused") && (
          <Button
            type="button"
            variant="outline"
            className={cn(BTN, "text-rose-800 hover:text-rose-900")}
            disabled={!!busy}
            onClick={() => void act("stop", () => schedulerApi.stopScheduler(dest.id))}
          >
            {spin("stop") ?? <Square aria-hidden />} Stop
          </Button>
        )}
      </div>
      {actError && (
        <p role="alert" className="break-words text-sm text-destructive">
          {actError}
        </p>
      )}
      {readError && <p className="break-words text-sm text-muted-foreground">Couldn't read the schedule: {readError}</p>}
      {stack === null && <p className="text-[15px] text-muted-foreground">Reading the schedule…</p>}
      {stack && !top && <p className="text-[15px] text-muted-foreground">No schedule loaded. New schedule makes one.</p>}

      {top && (room || servesRooms.length > 0 || ownEvents.length > 0) && (
        <section className="min-w-0 space-y-2">
          <h3 className="text-[15px] font-semibold">Run now</h3>
          {room ? (
            <EventsList room={room.id} destination={dest.id} empty="Nothing to run by hand." />
          ) : servesRooms.length ? (
            servesRooms.map((r) => (
              <div key={r.id} className="space-y-1.5">
                {servesRooms.length > 1 && <p className="text-sm text-muted-foreground">{r.name}</p>}
                <EventsList room={r.id} />
              </div>
            ))
          ) : (
            <LocalEvents destId={dest.id} events={ownEvents} />
          )}
        </section>
      )}

      {top && hasTimed && (
        <section className="min-w-0 space-y-2">
          <h3 className="text-[15px] font-semibold">How often</h3>
          <HowOften
            destId={dest.id}
            schedule={top}
            topIndex={topIndex}
            onSaved={() => {
              setReload((n) => n + 1);
              onChanged();
            }}
            onEditEverything={() => onEdit(dest.id, topIndex)}
          />
        </section>
      )}
      {top && !hasTimed && (
        <Button type="button" variant="ghost" className="h-11 rounded-xl px-3 text-[15px] text-teal-800" onClick={() => onEdit(dest.id, topIndex)}>
          Edit everything…
        </Button>
      )}

      {queue.length > 0 && (
        <section className="min-w-0 space-y-2">
          <h3 className="text-[15px] font-semibold">Waiting to run ({queue.length})</h3>
          <Queue items={queue} />
        </section>
      )}

      <Unroll title="Advanced" className="min-w-0 border-t border-stone-200/80 pt-1" contentClassName="min-w-0 space-y-4 pt-2">
        <div className="flex flex-wrap gap-2">
          <Button type="button" variant="outline" className={BTN} onClick={() => onEdit(dest.id, null)}>
            <Plus aria-hidden /> New schedule on top
          </Button>
          {top && (
            <Button type="button" variant="outline" className={BTN} disabled={!!busy} onClick={() => void act("unload", () => schedulerApi.unloadSchedule(dest.id))}>
              {spin("unload")} Remove the top schedule
            </Button>
          )}
        </div>
        {stack && stack.length > 0 && (
          <div className="min-w-0 space-y-2">
            {stack
              .map((layer, i) => ({ layer, i }))
              .reverse()
              .map(({ layer, i }) => (
                <Layer
                  key={i}
                  destId={dest.id}
                  index={i}
                  onTop={i === stack.length - 1}
                  layer={layer}
                  context={contexts[i]}
                  onEdit={() => onEdit(dest.id, i)}
                  onVarsSaved={() => setReload((n) => n + 1)}
                />
              ))}
          </div>
        )}
        <Unroll title="Log" contentClassName="min-w-0 pt-1">
          <LogView destId={dest.id} />
        </Unroll>
        {stack && allEventNames(stack).length > 0 && (
          <Unroll title="Trigger an event" hint="by its name in the schedule" contentClassName="min-w-0 pt-1">
            <RawEvents destId={dest.id} names={allEventNames(stack)} />
          </Unroll>
        )}
      </Unroll>
    </>
  );
}

/** "Sent" for a moment after a tap, as EventsList does. */
function useSent() {
  const [state, setState] = useState<Record<string, "sending" | "sent">>({});
  const [error, setError] = useState<string | null>(null);
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);
  useEffect(() => () => timers.current.forEach(clearTimeout), []);
  const send = async (key: string, label: string, fn: () => Promise<unknown>) => {
    setError(null);
    setState((s) => ({ ...s, [key]: "sending" }));
    try {
      await fn();
      setState((s) => ({ ...s, [key]: "sent" }));
      timers.current.push(
        setTimeout(() => {
          setState((s) => {
            const { [key]: _gone, ...rest } = s;
            return rest;
          });
        }, 2500),
      );
    } catch (e) {
      setState((s) => {
        const { [key]: _gone, ...rest } = s;
        return rest;
      });
      setError(`${label}: ${e instanceof Error ? e.message : String(e)}`);
    }
  };
  return { state, error, send };
}

function EventButton({ label, title, st, onClick, mono }: { label: string; title?: string; st?: "sending" | "sent"; onClick: () => void; mono?: boolean }) {
  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      title={title}
      disabled={st === "sending"}
      onClick={onClick}
      className={cn("h-auto min-h-9 max-w-full whitespace-normal break-all rounded-full bg-card text-left", mono && "font-mono text-[13px]", st === "sent" && "border-primary/50 text-primary")}
    >
      {st === "sending" && <Loader2 aria-hidden className="motion-safe:animate-spin" />}
      {st === "sent" && <Check aria-hidden />}
      {st === "sent" ? "Sent" : label}
    </Button>
  );
}

/** A screen in no room: its events by the Room page's rule, read from its own schedule. */
function LocalEvents({ destId, events }: { destId: string; events: ScheduleEvent[] }) {
  const { state, error, send } = useSent();
  if (!events.length) return <p className="text-sm text-muted-foreground">Nothing to run by hand.</p>;
  return (
    <div>
      <div className="flex flex-wrap gap-2">
        {events.map((ev) => (
          <EventButton
            key={ev.event}
            label={ev.label}
            title={ev.description ?? undefined}
            st={state[ev.event]}
            onClick={() => void send(ev.event, ev.label, () => throwRoomEvent({ event: ev.event, scope: destId }))}
          />
        ))}
      </div>
      {error && (
        <p role="alert" className="mt-2 break-words text-sm text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}

/** Advanced: every event of the stack by its raw name, internal ones too. */
function RawEvents({ destId, names }: { destId: string; names: string[] }) {
  const { state, error, send } = useSent();
  return (
    <div>
      <div className="flex flex-wrap gap-2">
        {names.map((n) => (
          <EventButton
            key={n}
            mono
            label={n}
            st={state[n]}
            onClick={() => void send(n, n, () => eventsApi.throwEvent({ event: n, scope: destId, ttl: "60s" }))}
          />
        ))}
      </div>
      {error && (
        <p role="alert" className="mt-2 break-words text-sm text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}

function Queue({ items }: { items: Json[] }) {
  return (
    <ul className="min-w-0 space-y-2">
      {items.map((it, i) => (
        <li
          key={i}
          className={cn(
            "min-w-0 rounded-xl border px-3 py-2",
            it.urgent ? "border-rose-200 bg-rose-50" : it.important ? "border-amber-200 bg-amber-50" : "border-stone-200 bg-card",
          )}
        >
          <p className="break-words text-[15px] font-medium">
            {String(it.action ?? "Step")}
            {it.urgent ? " · urgent" : it.important ? " · important" : ""}
          </p>
          {it.details && typeof it.details === "object" && (
            <dl className="mt-1 grid min-w-0 grid-cols-1 gap-x-3 text-sm text-muted-foreground sm:grid-cols-[auto_1fr]">
              {Object.entries(it.details as Record<string, unknown>).map(([k, v]) => (
                <div key={k} className="contents">
                  <dt className="font-medium text-stone-600">{k}</dt>
                  <dd className="min-w-0 break-words">{typeof v === "string" ? v : JSON.stringify(v)}</dd>
                </div>
              ))}
            </dl>
          )}
        </li>
      ))}
    </ul>
  );
}

/** One schedule of the stack: Edit, Change a value, its values and its script. */
function Layer({
  destId,
  index,
  onTop,
  layer,
  context,
  onEdit,
  onVarsSaved,
}: {
  destId: string;
  index: number;
  onTop: boolean;
  layer: Json;
  context: Json;
  onEdit: () => void;
  onVarsSaved: () => void;
}) {
  const [varsOpen, setVarsOpen] = useState(false);
  const vars: Record<string, unknown> = context?.vars && typeof context.vars === "object" ? context.vars : {};
  const keys = Object.keys(vars);
  return (
    <div className="min-w-0 rounded-xl border border-stone-200/80 bg-stone-50/60 px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <p className="min-w-0 flex-1 basis-40 text-[15px] font-semibold">
          Schedule {index + 1}
          {onTop && <span className="font-normal text-muted-foreground"> (on top, running)</span>}
        </p>
        <Button type="button" variant="outline" size="sm" className="h-9 rounded-lg" onClick={onEdit}>
          Edit
        </Button>
        {keys.length > 0 && (
          <Button type="button" variant="outline" size="sm" className="h-9 rounded-lg" onClick={() => setVarsOpen(true)}>
            Change a value
          </Button>
        )}
      </div>
      <Unroll title="Values" hint={keys.length ? `${keys.length}` : "none"} contentClassName="min-w-0 pt-1">
        {keys.length ? (
          <dl className="min-w-0 space-y-1.5 text-sm">
            {keys.map((k) => {
              const v = vars[k];
              return (
                <div key={k} className="min-w-0 sm:flex sm:gap-3">
                  <dt className="shrink-0 font-medium sm:w-40 sm:truncate" title={k}>
                    {k}
                  </dt>
                  <dd className="min-w-0 flex-1 break-words font-mono text-[13px] text-muted-foreground">
                    <div className="max-h-32 overflow-auto whitespace-pre-wrap rounded bg-card px-1.5 py-1">{typeof v === "object" ? JSON.stringify(v, null, 2) : String(v)}</div>
                  </dd>
                </div>
              );
            })}
            {context?.last_generated && (
              <div className="min-w-0 sm:flex sm:gap-3">
                <dt className="shrink-0 font-medium sm:w-40">Last made</dt>
                <dd className="min-w-0 break-words text-muted-foreground">{String(context.last_generated)}</dd>
              </div>
            )}
          </dl>
        ) : (
          <p className="text-sm text-muted-foreground">No values.</p>
        )}
      </Unroll>
      <Unroll title="Script" contentClassName="min-w-0 pt-1">
        <pre className="max-h-64 min-w-0 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-card p-2 text-xs">{JSON.stringify(layer, null, 2)}</pre>
      </Unroll>
      {varsOpen && (
        <SetVarsModal
          open={varsOpen}
          onOpenChange={setVarsOpen}
          destination={destId}
          contextVars={vars}
          onSave={() => {
            setVarsOpen(false);
            onVarsSaved();
          }}
        />
      )}
    </div>
  );
}

/** The scheduler's log, read every 5 s while shown and the page is visible; selecting text holds it still. */
function LogView({ destId }: { destId: string }) {
  const [lines, setLines] = useState<string[] | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const read = useCallback(() => {
    const sel = window.getSelection?.();
    if (sel && sel.toString().length > 0 && box.current?.contains(sel.anchorNode)) return;
    schedulerApi
      .getSchedulerLogs(destId)
      .then((r: Json) => setLines(Array.isArray(r?.log) ? r.log : []))
      .catch(() => setLines((l) => l ?? []));
  }, [destId]);
  usePoll(read, 5000, true);
  useEffect(() => {
    if (box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [lines]);
  return (
    <div ref={box} className="max-h-56 min-w-0 overflow-auto rounded-lg bg-stone-900 p-2 font-mono text-[11px] leading-4 text-emerald-300">
      {lines === null ? (
        <p className="text-stone-400">Reading…</p>
      ) : lines.length ? (
        lines.map((l, i) => (
          <div key={i} className="break-all">
            {l}
          </div>
        ))
      ) : (
        <p className="text-stone-400">Nothing logged.</p>
      )}
    </div>
  );
}
