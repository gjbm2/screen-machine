// "How often", in an open schedule card on the Schedules page: the top schedule's timed blocks in plain controls.
// Each block says what it does (its first event or action, from the data), when it starts, and, when it repeats, how
// often and until when; each day-of-week trigger has its day chips. Save writes the whole top schedule back through
// the existing PUT /api/schedulers/<id>/schedule/<index> (validated by the scheduler's schema there). The full
// schema editor stays one tap away ("Edit everything…").

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { jsonInit, requestWithErrorBody } from "@/shared/http/client";
import { everyWords, timedTriggers, untilWords, WEEK } from "./scheduleWords";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Json = any;

/** The repeat choices offered (minutes); the schedule's own value is added when it isn't one of them. */
const EVERY = [15, 30, 60, 120, 240];

const clone = (v: Json): Json => (v == null ? v : JSON.parse(JSON.stringify(v)));

const CHIP =
  "inline-flex min-h-10 items-center rounded-full border px-3.5 text-[15px] font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
const CHIP_ON = "border-teal-700 bg-teal-700 text-white";
const CHIP_OFF = "border-stone-200 bg-card text-stone-700 hover:bg-stone-50";
const TIME =
  "h-10 rounded-lg border border-input bg-card px-2 font-mono text-[15px] tabular-nums focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

/** "Every day", "Weekdays", "Weekends", "Mon, Wed, Fri", "No days". */
function daysWords(days: string[]): string {
  const set = WEEK.filter((w) => days.includes(w));
  if (set.length === 7) return "Every day";
  if (!set.length) return "No days";
  if (set.length === 5 && set.every((d) => WEEK.slice(0, 5).includes(d))) return "Weekdays";
  if (set.length === 2 && set.every((d) => WEEK.slice(5).includes(d))) return "Weekends";
  return set.map((d) => d.slice(0, 3)).join(", ");
}

/** The days a trigger runs on, in words; "Change days" shows the chips. */
function Days({ days, onToggle }: { days: string[]; onToggle: (day: string, on: boolean) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="min-w-0 space-y-2">
      <div className="flex flex-wrap items-center gap-x-3">
        <span className="text-[15px] font-medium">{daysWords(days)}</span>
        <button
          type="button"
          aria-expanded={open}
          onClick={() => setOpen(!open)}
          className="min-h-9 rounded-md text-[15px] text-teal-800 underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {open ? "Done" : "Change days"}
        </button>
      </div>
      {open && (
        <div role="group" aria-label="Days" className="flex flex-wrap gap-1.5">
          {WEEK.map((day) => {
            const on = days.includes(day);
            return (
              <button
                key={day}
                type="button"
                aria-pressed={on}
                aria-label={day}
                onClick={() => onToggle(day, on)}
                className={cn(CHIP, "min-h-9 px-2.5 text-sm", on ? CHIP_ON : CHIP_OFF)}
              >
                {day.slice(0, 3)}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

export function HowOften({
  destId,
  schedule,
  topIndex,
  onSaved,
  onEditEverything,
}: {
  destId: string;
  /** The top schedule (the one running). */
  schedule: Json;
  topIndex: number;
  onSaved: () => void;
  onEditEverything: () => void;
}) {
  const [draft, setDraft] = useState<Json>(() => clone(schedule));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const base = useRef(JSON.stringify(schedule));
  const dirty = JSON.stringify(draft) !== base.current;
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;

  // a fresh read of the schedule replaces the draft, unless something is being changed
  useEffect(() => {
    const next = JSON.stringify(schedule);
    if (next === base.current) return;
    base.current = next;
    if (!dirtyRef.current) setDraft(clone(schedule));
  }, [schedule]);

  const triggers = timedTriggers(draft);
  if (!triggers.length) return null;

  const edit = (fn: (d: Json) => void) => {
    setSaved(false);
    setError(null);
    setDraft((d: Json) => {
      const n = clone(d);
      fn(n);
      return n;
    });
  };
  const sa = (d: Json, ti: number, si: number) => d.triggers[ti].scheduled_actions[si];

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      await requestWithErrorBody(
        `/schedulers/${encodeURIComponent(destId)}/schedule/${topIndex}`,
        jsonInit("PUT", draft),
        "Couldn't save the schedule",
      );
      base.current = JSON.stringify(draft);
      setSaved(true);
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="min-w-0 space-y-3" data-testid={`how-often-${destId}`}>
      {triggers.map((t) => (
        <div key={t.trigger} className="min-w-0 space-y-3 rounded-xl border border-stone-200/80 bg-stone-50/60 p-3">
          <Days
            days={t.days}
            onToggle={(day, on) =>
              edit((d) => {
                const days: string[] = Array.isArray(d.triggers[t.trigger].days) ? d.triggers[t.trigger].days : [];
                const next = on ? days.filter((x) => x !== day) : [...days, day];
                d.triggers[t.trigger].days = WEEK.filter((w) => next.includes(w));
              })
            }
          />
          {t.blocks.map((b, bi) => {
            const mins = b.every != null ? Number(b.every) : null;
            const choices = mins != null && Number.isFinite(mins) && !EVERY.includes(mins) ? [...EVERY, mins].sort((x, y) => x - y) : EVERY;
            return (
              <div key={b.index} className={cn("min-w-0 space-y-2", bi > 0 && "border-t border-stone-200/70 pt-3")}>
                <p className="text-base font-medium leading-snug">{b.label}</p>
                {mins != null ? (
                  <>
                    <div role="radiogroup" aria-label={`${b.label}: how often`} className="flex flex-wrap items-center gap-1.5">
                      <span className="mr-1 text-[15px] text-muted-foreground">Every</span>
                      {choices.map((m) => (
                        <button
                          key={m}
                          type="button"
                          role="radio"
                          aria-checked={mins === m}
                          onClick={() => edit((d) => (sa(d, b.trigger, b.index).repeat_schedule.every = String(m)))}
                          className={cn(CHIP, mins === m ? CHIP_ON : CHIP_OFF)}
                        >
                          {everyWords(m)}
                        </button>
                      ))}
                    </div>
                    <div className="flex flex-wrap items-center gap-2 text-[15px]">
                      <label className="flex items-center gap-2">
                        <span className="text-muted-foreground">from</span>
                        <input
                          type="time"
                          step={60}
                          className={TIME}
                          value={b.time}
                          onChange={(e) => e.target.value && edit((d) => (sa(d, b.trigger, b.index).time = e.target.value))}
                        />
                      </label>
                      <label className="flex items-center gap-2">
                        <span className="text-muted-foreground">until</span>
                        <input
                          type="time"
                          step={60}
                          className={TIME}
                          value={b.until ?? "23:59"}
                          onChange={(e) => e.target.value && edit((d) => (sa(d, b.trigger, b.index).repeat_schedule.until = e.target.value))}
                        />
                      </label>
                      {(b.until ?? "23:59") === "23:59" && <span className="text-sm text-muted-foreground">({untilWords("23:59")})</span>}
                    </div>
                  </>
                ) : (
                  <label className="flex flex-wrap items-center gap-2 text-[15px]">
                    <span className="text-muted-foreground">Once, at</span>
                    <input
                      type="time"
                      step={60}
                      className={TIME}
                      value={b.time}
                      onChange={(e) => e.target.value && edit((d) => (sa(d, b.trigger, b.index).time = e.target.value))}
                    />
                  </label>
                )}
              </div>
            );
          })}
        </div>
      ))}
      {error && (
        <p role="alert" className="break-words text-sm text-destructive">
          {error}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {dirty && (
          <>
            <Button type="button" onClick={() => void save()} disabled={saving} className="h-11 rounded-xl px-5 text-base">
              {saving ? "Saving…" : "Save"}
            </Button>
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                setDraft(clone(JSON.parse(base.current)));
                setError(null);
              }}
              className="h-11 rounded-xl px-5 text-base"
            >
              Undo changes
            </Button>
          </>
        )}
        {saved && !dirty && <span className="text-sm text-emerald-700">Saved</span>}
        <Button type="button" variant="ghost" onClick={onEditEverything} className="h-11 rounded-xl px-3 text-[15px] text-teal-800">
          Edit everything…
        </Button>
      </div>
    </div>
  );
}
