// Scheduler events: throw, list (per destination and across all), and clear.

import { toast } from 'sonner';
import { jsonInit, requestJson, send } from '@/shared/http/client';
import { getPublishDestinations } from './publish';
import type { SchedulerEvent } from './types';

export async function throwEvent(eventData: Record<string, unknown>) {
  try {
    const payload = { ...eventData };
    // Legacy callers may send destination/group instead of scope; an empty-string scope counts as missing.
    if (!payload.scope) {
      if (payload.destination) {
        payload.scope = payload.destination;
        delete payload.destination;
      } else if (payload.group) {
        payload.scope = payload.group;
        delete payload.group;
      } else {
        payload.scope = 'global';
      }
    }

    const response = await send('/schedulers/events/throw', jsonInit('POST', payload));
    if (!response.ok) {
      // A non-JSON error body rejects with the parse error, as before.
      const err = await response.json();
      throw new Error(err.error || err.message || 'Failed to throw event');
    }
    return await response.json();
  } catch (error) {
    toast.error('Failed to throw event');
    throw error;
  }
}

export async function getEventsForDestination(destinationId: string) {
  return requestJson(
    `/schedulers/events?destination=${encodeURIComponent(destinationId)}`,
    undefined,
    'Failed to fetch events for destination',
  );
}

export async function getAllEvents() {
  try {
    const destinations = await getPublishDestinations();
    const allEvents: { queue: SchedulerEvent[]; history: SchedulerEvent[] } = {
      queue: [],
      history: [],
    };

    // Sequential on purpose (as before); a failing destination is logged and skipped.
    for (const dest of destinations) {
      try {
        const destEvents = await getEventsForDestination(dest.id);

        const queueWithDest = destEvents.queue.map((event: SchedulerEvent) => ({
          ...event,
          destination_id: dest.id,
          destination_name: dest.name || dest.id,
        }));

        const historyWithDest = destEvents.history.map((event: SchedulerEvent) => ({
          ...event,
          destination_id: dest.id,
          destination_name: dest.name || dest.id,
        }));

        allEvents.queue.push(...queueWithDest);
        allEvents.history.push(...historyWithDest);
      } catch (err) {
        console.error(`Error fetching events for ${dest.id}:`, err);
      }
    }

    return allEvents;
  } catch (error) {
    // The original error is replaced, so log it here.
    console.error('Error fetching all events:', error);
    throw new Error('Failed to fetch all events');
  }
}

export async function clearEventByKey(destinationId: string, eventKey: string, clearHistory: boolean = false) {
  const params = new URLSearchParams({
    destination: destinationId,
    clear_history: clearHistory.toString(),
  });
  return requestJson(
    `/schedulers/events/${encodeURIComponent(eventKey)}?${params}`,
    { method: 'DELETE' },
    response => `Failed to clear event: ${response.statusText}`,
  );
}

export async function clearEventById(destinationId: string, eventId: string, clearHistory: boolean = false) {
  const params = new URLSearchParams({
    destination: destinationId,
    event_id: eventId,
    clear_history: clearHistory.toString(),
  });
  return requestJson(
    `/schedulers/events/by-id?${params}`,
    { method: 'DELETE' },
    response => `Failed to clear event: ${response.statusText}`,
  );
}

export async function clearAllEvents(destinationId: string, clearHistory: boolean = false) {
  const params = new URLSearchParams({
    destination: destinationId,
    clear_history: clearHistory.toString(),
  });
  return requestJson(
    `/schedulers/events?${params}`,
    { method: 'DELETE' },
    response => `Failed to clear events: ${response.statusText}`,
  );
}
