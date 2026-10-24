// A room's schedules for the Room page (routes/scheduler_room.py): the variables exported to its scheduler group,
// its room actions and each screen's events. Nothing here names a variable or an event: they all come from the data.
//   GET  /api/rooms/<room>/schedule
//   POST /api/schedulers/exported-vars/<name> {value, set_by}   (the owner's context, then every importer)
//   POST /api/schedulers/events/throw {event, scope}

import { jsonInit, requestWithErrorBody } from '@/shared/http/client';

export interface RoomVariable {
  name: string;
  label: string;
  description: string | null;
  value: unknown;
  editable: boolean;
  owner: string;
  owner_name: string;
  /** ISO local time of the last export by the owner's schedule, or of a change by hand when newer. */
  set_at: string | null;
  set_by: string;
  used_by: { id: string; name: string }[];
}

export interface RoomEvent {
  event: string;
  label: string;
  description: string | null;
  /** The scope to throw it to: the destination whose schedule listens for it. */
  scope: string;
  dest?: string;
  dest_name?: string;
}

export interface RoomSchedule {
  room: string;
  group: string;
  variables: RoomVariable[];
  /** Events of the room's non-screen schedules (e.g. the cloud's cascade). */
  actions: RoomEvent[];
  /** Each of the room's screens' events. */
  screens: Record<string, RoomEvent[]>;
}

export async function getRoomSchedule(room: string, signal?: AbortSignal): Promise<RoomSchedule> {
  const r = await requestWithErrorBody(`/rooms/${encodeURIComponent(room)}/schedule`, { cache: 'no-store', signal }, "Couldn't read the room's schedule");
  return r.json();
}

/** Change an exported variable; `setBy` is shown as who set it until the schedule exports it again. */
export async function setRoomVariable(name: string, value: unknown, setBy = 'Room page') {
  const r = await requestWithErrorBody(
    `/schedulers/exported-vars/${encodeURIComponent(name)}`,
    jsonInit('POST', { value, set_by: setBy }),
    "Couldn't save it",
  );
  return r.json();
}

export async function throwRoomEvent(ev: Pick<RoomEvent, 'event' | 'scope'>) {
  const r = await requestWithErrorBody('/schedulers/events/throw', jsonInit('POST', { event: ev.event, scope: ev.scope, ttl: '60s' }), "Couldn't send it");
  return r.json();
}
