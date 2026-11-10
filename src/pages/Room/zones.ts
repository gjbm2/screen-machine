// Mode zones on the Room page and in Settings (rooms.json "own_modes"): pure helpers, no React.
//
// room_service's status keeps the site's mode at the top (status.mode, config, stored), and lists each room that has
// its own modes under status.zones (present only when one exists). A zone's switches arrive as "room_mode.transition"
// events (data.room); the site's are "mode.transition". The page tracks each zone's transition the same way it tracks
// the site's (trackFromStatus / trackFromEvent), so its mode strip shows the same progress line.
//
// Without zones nothing here changes the page: zonesOf is [], and the captions are null.

import type { ModeStatus, ModeTransitionEventData, RoomStatus, TrackedTransition, ZoneStatus } from "./types";

/** The zones in status order ([] on a deployment without any). */
export function zonesOf(status: RoomStatus | null | undefined): ZoneStatus[] {
  return Array.isArray(status?.zones) ? status.zones : [];
}

/** The zone of `room`, or undefined (the room shares the site's modes). */
export function zoneOf(status: RoomStatus | null | undefined, room: string | null | undefined): ZoneStatus | undefined {
  return room ? zonesOf(status).find((z) => z.room === room) : undefined;
}

function andList(xs: string[]): string {
  return xs.length <= 1 ? (xs[0] ?? "") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`;
}

/** The site switcher's caption while zones exist: the rooms the site's modes cover ("Drawing room"); null without
 *  zones (the page is as before). A site whose rooms all have their own modes still has screens in no room: "Other". */
export function siteModesCaption(status: RoomStatus | null | undefined): string | null {
  if (!zonesOf(status).length) return null;
  const names = (status?.rooms ?? []).filter((r) => !r.own_modes).map((r) => r.name || r.id);
  return names.length ? andList(names) : "Everything else";
}

/** The Look fold's line while zones exist or a room is outside the show: "The show plays in Drawing room and Lab";
 *  null otherwise (the page is as before). */
export function showRoomsLine(status: RoomStatus | null | undefined): string | null {
  const rooms = status?.rooms ?? [];
  if (!zonesOf(status).length && !rooms.some((r) => r.show === false)) return null;
  const names = rooms.filter((r) => r.show !== false).map((r) => r.name || r.id);
  return names.length ? `The show plays in ${andList(names)}` : "No room is in the show";
}

/**
 * The tracked transition after a status (the site's status.mode, or a zone's mode): the running one, else the last
 * finished one with its real outcome, else the previous one marked done. Exactly the site's rule since before zones.
 */
export function trackFromStatus(prev: TrackedTransition | null, mode: ModeStatus | null | undefined, now = Date.now()): TrackedTransition | null {
  const t = mode?.transition;
  // Not in LIVE_V1 §3, read if present: room_service keeps the finished transition here
  // (transition goes null when it ends), with its real outcome.
  const last = mode?.last_transition;
  if (t && t.id) {
    const finished = prev && prev.id === t.id && (prev.phase === "done" || prev.phase === "cancelled");
    return { ...t, phase: finished ? prev.phase : "running", finishedAt: finished ? prev.finishedAt : undefined };
  }
  if (last && last.id) {
    // Also covers a page opened after the transition ended, or one that missed the events.
    const seen = prev && prev.id === last.id ? prev.finishedAt : undefined;
    // status sends finished_age_s (seconds since it ended) when it has no finished_at: a switch 87 min old
    // is 87 min old, not "just now" (it showed "Now Normal" for 15 s on every page open).
    const ageS = (last as { finished_age_s?: number | null }).finished_age_s;
    const at =
      seen ??
      (last.finished_at
        ? Date.parse(last.finished_at)
        : typeof ageS === "number" && Number.isFinite(ageS)
          ? now - ageS * 1000
          : NaN);
    return {
      ...last,
      phase: last.phase === "cancelled" ? "cancelled" : "done",
      finishedAt: Number.isFinite(at) ? at : now,
    };
  }
  // Status dropped the transition, so it is over. Keep it on screen with its outcome.
  if (prev && prev.phase === "running") return { ...prev, phase: "done", finishedAt: now };
  return prev;
}

/** A transition (tracked, or status's) with one step event applied: matched on the step's id (room_service), else on
 *  subsystem + action; a step not seen yet (the started event has only a step count) is appended. */
export function patchSteps<T extends { id?: string; steps?: TrackedTransition["steps"] }>(t: T, step: ModeTransitionEventData["step"]): T {
  if (!step || typeof step !== "object") return t;
  const steps = t.steps ?? [];
  const i = steps.findIndex((s) => (step.id && s.id ? s.id === step.id : s.subsystem === step.subsystem && s.action === step.action));
  return {
    ...t,
    steps: i >= 0 ? steps.map((s, j) => (j === i ? { ...s, ...step } : s)) : [...steps, step],
  };
}

/** The tracked transition after a transition event ("mode.transition" for the site, "room_mode.transition" for a
 *  zone): a new one on "started", its steps patched, its outcome on "done" / "cancelled". */
export function trackFromEvent(prev: TrackedTransition | null, td: ModeTransitionEventData, now = Date.now()): TrackedTransition | null {
  if (!prev || prev.id !== td.id) {
    return td.phase === "started" && td.id
      ? {
          id: td.id,
          from: td.from ?? null,
          to: td.to ?? null,
          steps: [],
          phase: "running",
          ...(td.source !== undefined ? { source: td.source } : {}),
          ...(td.reason !== undefined ? { reason: td.reason } : {}),
          ...(td.skip !== undefined ? { skip: td.skip } : {}),
        }
      : prev;
  }
  const next = patchSteps(prev, td.step);
  if (td.phase === "done" || td.phase === "cancelled")
    return {
      ...next,
      phase: td.phase,
      finishedAt: now,
      ...(td.status !== undefined ? { status: td.status } : {}),
      ...(td.detail !== undefined ? { detail: td.detail } : {}),
    };
  return next;
}

/** The status with a zone's running transition's steps patched by its event (as the site's status.mode.transition
 *  is); the same status object when the event isn't for that zone's running transition. */
export function patchZoneStatus(prev: RoomStatus | null, td: ModeTransitionEventData): RoomStatus | null {
  const zones = prev?.zones;
  if (!prev || !zones || !td.room) return prev;
  const i = zones.findIndex((z) => z.room === td.room);
  const t = i >= 0 ? zones[i].mode?.transition : null;
  if (!t || t.id !== td.id) return prev;
  const z = zones[i];
  return { ...prev, zones: zones.map((x, j) => (j === i ? { ...z, mode: { ...z.mode, transition: patchSteps(t, td.step) } } : x)) };
}
