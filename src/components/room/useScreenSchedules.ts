// Every screen's schedule state for the Room page's screen cards, from one shared read of
// GET /api/schedulers/all/status (one request for the whole page, however many cards use it), re-read every
// 5 minutes while the page is visible and someone is listening, and when the page comes back into view.
// scheduleWords() turns one screen's entry into the card's plain line:
//   "New art every 2 h · next 20:04", "Schedule paused", "No schedule running".
// The interval is the status's interval_min when the server sends it, else the "every N min" in its description.

import { useEffect, useState } from "react";
import { getAllSchedulerStatuses } from "@/shared/api/scheduler";

export interface ScheduleStatus {
  status?: string;
  is_running?: boolean;
  is_paused?: boolean;
  /** Minutes between runs, when the server says (Schedules page work adds it). */
  interval_min?: number | null;
  until?: string | null;
  next_action?: {
    description?: string | null;
    next_time?: string | null;
    minutes_until_next?: number | null;
    time_until_display?: string | null;
    interval_min?: number | null;
  } | null;
}

const POLL_MS = 5 * 60_000;

let data: Record<string, ScheduleStatus> | null = null;
let failed = false;
let inflight: Promise<void> | null = null;
let timer: ReturnType<typeof setInterval> | null = null;
const listeners = new Set<() => void>();

/** Re-read every screen's schedule state now (after a start, pause or resume); every user of it updates. */
export function refreshScreenSchedules(): Promise<void> {
  return refresh();
}

function refresh(): Promise<void> {
  if (inflight) return inflight;
  inflight = Promise.resolve(getAllSchedulerStatuses())
    .then((r: { statuses?: Record<string, ScheduleStatus> } | null) => {
      data = r?.statuses ?? {};
      failed = false;
    })
    .catch(() => {
      failed = true;
    })
    .finally(() => {
      inflight = null;
      listeners.forEach((fn) => fn());
    });
  return inflight;
}

/** One screen's schedule state: undefined while the first read is under way, null when it can't be read. */
export function useScreenSchedule(screenId: string): ScheduleStatus | null | undefined {
  const [, bump] = useState(0);
  useEffect(() => {
    const fn = () => bump((n) => n + 1);
    listeners.add(fn);
    if (!data && !inflight) void refresh();
    if (!timer) {
      timer = setInterval(() => {
        if (!document.hidden) void refresh();
      }, POLL_MS);
    }
    const onVisible = () => {
      if (!document.hidden) void refresh();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      listeners.delete(fn);
      if (!listeners.size && timer) {
        clearInterval(timer);
        timer = null;
      }
    };
  }, []);
  if (!data) return failed ? null : undefined;
  return data[screenId] ?? {};
}

/** "30 min", "1 h", "2 h", "1 h 30 min". */
export function everyWords(min: number): string {
  const m = Math.round(min);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  const r = m % 60;
  return r ? `${h} h ${r} min` : `${h} h`;
}

function intervalOf(s: ScheduleStatus): number | null {
  const n = s.interval_min ?? s.next_action?.interval_min;
  if (typeof n === "number" && n > 0) return n;
  const m = /every\s+(\d+(?:\.\d+)?)\s*min/i.exec(s.next_action?.description ?? "");
  return m ? Number(m[1]) : null;
}

/** The screen card's line for its schedule, or null when there is nothing to say (not read yet, or unreadable). */
export function scheduleWords(s: ScheduleStatus | null | undefined): string | null {
  if (!s) return null;
  if (s.is_paused || s.status === "paused") return "Schedule paused";
  const running = s.status === "running" || s.is_running;
  if (!running) return "No schedule running";
  const next = s.next_action;
  const mins = next?.minutes_until_next;
  // Beyond today, "next 03:00" would read as tonight: say how far off instead.
  const when =
    next?.next_time && (typeof mins !== "number" || mins < 18 * 60)
      ? `next ${next.next_time}`
      : next?.time_until_display
        ? `next in ${next.time_until_display.replace(/\s+from now$/, "")}`
        : null;
  const every = intervalOf(s);
  if (every) return when ? `New art every ${everyWords(every)} · ${when}` : `New art every ${everyWords(every)}`;
  if (when) return `Schedule running · ${when}`;
  return "Schedule running · nothing more today";
}
