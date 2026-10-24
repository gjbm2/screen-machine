// One shared read of a room's schedules (GET /api/rooms/<room>/schedule) for every component that shows part of it
// (TodayCard, each screen's EventsList): one request per room, re-read every minute while the page is visible and
// someone is listening, and at once after a change (refreshRoomSchedule).

import { useEffect, useState } from "react";
import { getRoomSchedule, type RoomSchedule } from "@/shared/api/roomSchedule";

interface Entry {
  data: RoomSchedule | null;
  error: string | null;
  listeners: Set<() => void>;
  inflight: Promise<void> | null;
  timer: ReturnType<typeof setInterval> | null;
}

const POLL_MS = 60_000;
const entries = new Map<string, Entry>();

function entry(room: string): Entry {
  let e = entries.get(room);
  if (!e) {
    e = { data: null, error: null, listeners: new Set(), inflight: null, timer: null };
    entries.set(room, e);
  }
  return e;
}

/** Re-read a room's schedules now (after an edit or an event); every user of it updates. */
export function refreshRoomSchedule(room: string): Promise<void> {
  const e = entry(room);
  if (e.inflight) return e.inflight;
  e.inflight = getRoomSchedule(room)
    .then((d) => {
      e.data = d;
      e.error = null;
    })
    .catch((err: unknown) => {
      e.error = err instanceof Error ? err.message : String(err);
    })
    .finally(() => {
      e.inflight = null;
      e.listeners.forEach((fn) => fn());
    });
  return e.inflight;
}

export function useRoomSchedule(room: string | null | undefined): { data: RoomSchedule | null; error: string | null } {
  const [, bump] = useState(0);
  useEffect(() => {
    if (!room) return;
    const e = entry(room);
    const fn = () => bump((n) => n + 1);
    e.listeners.add(fn);
    if (!e.data && !e.inflight) void refreshRoomSchedule(room);
    if (!e.timer) {
      e.timer = setInterval(() => {
        if (!document.hidden) void refreshRoomSchedule(room);
      }, POLL_MS);
    }
    const onVisible = () => {
      if (!document.hidden) void refreshRoomSchedule(room);
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      e.listeners.delete(fn);
      if (!e.listeners.size && e.timer) {
        clearInterval(e.timer);
        e.timer = null;
      }
    };
  }, [room]);
  const e = room ? entries.get(room) : undefined;
  return { data: e?.data ?? null, error: e?.error ?? null };
}
