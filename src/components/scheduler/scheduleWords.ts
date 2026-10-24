// Plain words for the Schedules page (and anything else that shows a screen's schedule): its state, when it next runs
// and how often, and the timed blocks and events of a schedule. Everything comes from the data: the scheduler's status
// (GET /api/schedulers/all/status, with interval_min / until / next_label from scheduler_api.timed_block_info) and the
// schedule JSON itself. No event or variable name is written here; labels are made from the names, as
// routes/scheduler_room.py makes them ("Develop_theme" -> "Develop theme"), and names starting with "_" are internal.

import type { Tone } from "@/pages/Room/tones";

export interface NextActionView {
  has_next_action?: boolean;
  next_time?: string | null;
  minutes_until_next?: number | null;
  description?: string | null;
  error?: boolean;
}

/** One destination's entry in /api/schedulers/all/status. */
export interface SchedStatus {
  status?: string;
  is_running?: boolean;
  is_paused?: boolean;
  error?: string;
  next_action?: NextActionView | null;
  /** How often the next run's block repeats, in minutes (null: it doesn't repeat). */
  interval_min?: number | null;
  /** When that block stops repeating ("23:59" when the schedule doesn't say). */
  until?: string | null;
  /** What that block does ("Generate"). */
  next_label?: string | null;
}

export type RunState = "running" | "paused" | "stopped" | "error";

export function runState(s: SchedStatus | undefined): RunState {
  if (!s) return "stopped";
  if (s.status === "error") return "error";
  if (s.is_paused || s.status === "paused") return "paused";
  if (s.is_running || s.status === "running") return "running";
  return "stopped";
}

export const STATE_WORDS: Record<RunState, { word: string; tone: Tone }> = {
  running: { word: "Running", tone: "ok" },
  paused: { word: "Paused", tone: "warn" },
  stopped: { word: "Not running", tone: "muted" },
  error: { word: "Something went wrong", tone: "bad" },
};

/** "Develop_theme" -> "Develop theme" (scheduler_room.label_for). */
export function labelFor(name: string): string {
  const w = String(name).replace(/[_-]/g, " ").split(/\s+/).filter(Boolean).join(" ");
  return w ? w[0].toUpperCase() + w.slice(1) : String(name);
}

/** 15 -> "15 min", 60 -> "1 h", 90 -> "1 h 30 min", 0.5 -> "30 s". */
export function everyWords(min: number): string {
  if (!(min > 0)) return `${min} min`;
  if (min < 1) return `${Math.round(min * 60)} s`;
  const h = Math.floor(min / 60);
  const m = Math.round(min % 60);
  if (!h) return `${m} min`;
  return m ? `${h} h ${m} min` : `${h} h`;
}

/** "23:59" (the scheduler's end of day) -> "midnight". */
export function untilWords(until: string): string {
  return until === "23:59" || until === "24:00" || until === "00:00" ? "midnight" : until;
}

/** 13 -> "in 13 min", 150 -> "in 2 h 30 min". */
export function inWords(min: number): string {
  if (!(min >= 1)) return "in under a minute";
  if (min < 60) return `in ${Math.round(min)} min`;
  if (min >= 24 * 60) {
    const d = Math.round(min / (24 * 60));
    return d === 1 ? "tomorrow" : `in ${d} days`;
  }
  const h = Math.floor(min / 60);
  const m = Math.round(min % 60);
  return m ? `in ${h} h ${m} min` : `in ${h} h`;
}

/** "Next: Generate at 20:04 (in 13 min) · every 2 h until midnight", or a plain state line. */
export function nextLine(s: SchedStatus | undefined): string {
  const st = runState(s);
  if (st === "error") return s?.error ? `Couldn't read it: ${s.error}` : "Couldn't read it";
  if (st === "paused") return "Paused: nothing runs until it's resumed";
  if (st === "stopped") return "Not running";
  const n = s?.next_action;
  if (!n || !n.has_next_action || !n.next_time || n.error) return "Running · nothing more due today";
  const what = s?.next_label ? `Next: ${s.next_label} at ${n.next_time}` : `Next run at ${n.next_time}`;
  const when = typeof n.minutes_until_next === "number" && Number.isFinite(n.minutes_until_next) ? ` (${inWords(n.minutes_until_next)})` : "";
  const often = s?.interval_min ? ` · every ${everyWords(s.interval_min)}${s.until ? ` until ${untilWords(s.until)}` : ""}` : "";
  return `${what}${when}${often}`;
}

// ── the schedule JSON ───────────────────────────────────────────────────────────

/* eslint-disable @typescript-eslint/no-explicit-any */
type Json = any;

/** Instructions that say nothing about what a block does (scheduler_api._PLUMBING_ACTIONS). */
const PLUMBING = new Set(["import_var", "export_var", "set_var", "terminate", "log", "wait", "sleep"]);

/** What a block of instructions does, as a label: its first thrown event, else its first real action. */
export function blockLabel(block: Json): string {
  const list: Json[] = Array.isArray(block?.instructions_block) ? block.instructions_block : [];
  const ev = list.find((i) => i?.action === "throw_event" && i.event && !String(i.event).startsWith("_"));
  if (ev) return labelFor(ev.event);
  const act = list.find((i) => i?.action && !PLUMBING.has(i.action));
  return act ? labelFor(act.action) : "Runs its steps";
}

export interface TimedBlock {
  /** Index of its day_of_week trigger, and of the block in that trigger's scheduled_actions. */
  trigger: number;
  index: number;
  time: string;
  /** Minutes, as the schedule stores it (a string); null when it runs once. */
  every: string | null;
  /** The end of the repeat; null when the schedule doesn't say (the scheduler then stops at 23:59). */
  until: string | null;
  label: string;
}

export interface TimedTrigger {
  trigger: number;
  days: string[];
  blocks: TimedBlock[];
}

/** The day-of-week triggers of a schedule and their timed blocks, in file order. */
export function timedTriggers(schedule: Json): TimedTrigger[] {
  const out: TimedTrigger[] = [];
  (Array.isArray(schedule?.triggers) ? schedule.triggers : []).forEach((t: Json, ti: number) => {
    if (t?.type !== "day_of_week") return;
    const blocks: TimedBlock[] = [];
    (Array.isArray(t.scheduled_actions) ? t.scheduled_actions : []).forEach((sa: Json, si: number) => {
      if (typeof sa?.time !== "string") return;
      const rep = sa.repeat_schedule && typeof sa.repeat_schedule === "object" ? sa.repeat_schedule : null;
      blocks.push({
        trigger: ti,
        index: si,
        time: sa.time,
        every: rep && rep.every != null ? String(rep.every) : null,
        until: rep && typeof rep.until === "string" ? rep.until : null,
        label: blockLabel(sa.trigger_actions),
      });
    });
    if (blocks.length) out.push({ trigger: ti, days: Array.isArray(t.days) ? t.days : [], blocks });
  });
  return out;
}

export interface ScheduleEvent {
  event: string;
  label: string;
  description: string | null;
}

/** The events a schedule listens for, by the Room page's rule: no "_" names, no "room": false. */
export function scheduleEvents(schedule: Json): ScheduleEvent[] {
  const seen = new Set<string>();
  const out: ScheduleEvent[] = [];
  for (const t of Array.isArray(schedule?.triggers) ? schedule.triggers : []) {
    if (t?.type !== "event" || typeof t.value !== "string") continue;
    if (t.value.startsWith("_") || t.room === false || seen.has(t.value)) continue;
    seen.add(t.value);
    out.push({
      event: t.value,
      label: typeof t.display_name === "string" && t.display_name.trim() ? t.display_name : labelFor(t.value),
      description: typeof t.description === "string" ? t.description : null,
    });
  }
  return out;
}

/** Every event name of every schedule in a stack, internal ones too (Advanced › Trigger an event). */
export function allEventNames(stack: Json[]): string[] {
  const names = new Set<string>();
  for (const layer of stack) {
    for (const t of Array.isArray(layer?.triggers) ? layer.triggers : []) {
      if (t?.type === "event" && typeof t.value === "string") names.add(t.value);
    }
  }
  return [...names];
}

export const WEEK = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
