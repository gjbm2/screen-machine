// Scheduler control and inspection. destinationId is interpolated unencoded, as before.

import { jsonInit, requestJson, requestWithErrorBody } from '@/shared/http/client';
import { getPublishDestinations } from './publish';
import { toastedJson, toastOnFailure } from './toast';

// Returns the raw text (keeps the backend's key order); it is parsed only to check it is JSON with .properties.
export async function getSchedulerSchema() {
  return toastOnFailure('Failed to fetch scheduler schema', async () => {
    const response = await requestWithErrorBody(
      '/scheduler/schema',
      { method: 'GET', headers: { Accept: 'application/json' } },
      'Failed to fetch scheduler schema',
    );
    const schemaText = await response.text();
    Object.keys(JSON.parse(schemaText).properties);
    return schemaText;
  });
}

export async function listSchedulers() {
  return requestJson('/schedulers', undefined, 'Failed to list schedulers');
}

export async function getDestinations() {
  const publishDestinations = await getPublishDestinations();
  const statusesResponse = await getAllSchedulerStatuses();
  const statuses = statusesResponse.statuses || {};
  const destinations = publishDestinations.map((dest) => ({
    id: dest.id,
    name: dest.name,
    scheduler_running: statuses[dest.id]?.is_running || false,
  }));
  return { destinations };
}

export async function getSchedulerLogs(destinationId: string) {
  return requestJson(`/schedulers/${destinationId}`, undefined, 'Failed to fetch scheduler logs');
}

export async function startScheduler(destinationId: string, schedule: unknown) {
  return toastedJson(`/schedulers/${destinationId}`, jsonInit('POST', schedule), 'Failed to start scheduler');
}

export async function stopScheduler(destinationId: string) {
  return toastedJson(`/schedulers/${destinationId}`, { method: 'DELETE' }, 'Failed to stop scheduler');
}

// Polled by the display page; a failure toasts on the TV, as before.
export async function getSchedule(destinationId: string) {
  return toastedJson(`/schedulers/${destinationId}/schedule`, { method: 'GET' }, 'Failed to get schedule');
}

export async function pauseScheduler(destinationId: string) {
  return toastedJson(`/schedulers/${destinationId}/pause`, { method: 'POST' }, 'Failed to pause scheduler');
}

export async function unpauseScheduler(destinationId: string) {
  return toastedJson(`/schedulers/${destinationId}/unpause`, { method: 'POST' }, 'Failed to unpause scheduler');
}

export async function getSchedulerStatus(destinationId: string) {
  return requestJson(`/schedulers/${destinationId}/status`, undefined, 'Failed to fetch scheduler status');
}

export async function getSchedulerContext(destinationId: string) {
  return requestJson(`/schedulers/${destinationId}/context`, undefined, 'Failed to fetch scheduler context');
}

export async function getInstructionQueue(destinationId: string) {
  return requestJson(`/schedulers/${destinationId}/instructions`, undefined, 'Failed to fetch instruction queue');
}

export async function unloadSchedule(destinationId: string) {
  return toastedJson(`/schedulers/${destinationId}/schedule`, { method: 'DELETE' }, 'Failed to unload schedule');
}

export async function getScheduleStack(destinationId: string) {
  return requestJson(`/schedulers/${destinationId}/schedule/stack`, undefined, 'Failed to fetch schedule stack');
}

// A null varValue deletes the variable.
export async function setSchedulerContextVar(destinationId: string, varName: string, varValue: unknown) {
  return toastedJson(
    `/schedulers/${destinationId}/context`,
    jsonInit('POST', { var_name: varName, var_value: varValue }),
    'Failed to set scheduler context variable',
  );
}

export async function getAllSchedulerStatuses() {
  return requestJson('/schedulers/all/status', undefined, 'Failed to fetch scheduler statuses');
}

export async function getNextScheduledAction(destinationId: string) {
  return requestJson(`/schedulers/${destinationId}/next_action`, undefined, 'Failed to fetch next scheduled action');
}

// Fetches the whole stack (there is no per-position endpoint). A negative position is not rejected.
export async function getScheduleAtPosition(destinationId: string, position: number) {
  return toastOnFailure('Failed to get schedule at position', async () => {
    const response = await requestWithErrorBody(
      `/schedulers/${destinationId}/schedule/stack`,
      undefined,
      'Failed to get schedule stack',
    );
    const data = await response.json();
    if (!data.stack || !Array.isArray(data.stack) || position >= data.stack.length) {
      throw new Error(`No schedule found at position ${position}`);
    }
    return { success: true, schedule: data.stack[position], position };
  });
}
