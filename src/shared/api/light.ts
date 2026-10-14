// Ambient mask (per display) and light-sensor intensity settings.

import { jsonInit, requestJson } from '@/shared/http/client';
import type { MaskData } from './types';

// Path segments are interpolated raw (not encoded), as before.
// `init` lets a poller pass { signal, cache: 'no-store' }; DisplayPage/control/MaskPoller.ts still builds this URL itself.
export async function getMask(destinationId: string, init?: RequestInit): Promise<MaskData> {
  return requestJson(`/${destinationId}/mask`, init, 'Failed to fetch mask');
}

export async function enableMask(destinationId: string): Promise<{ status: string; destination: string }> {
  return requestJson(`/${destinationId}/maskon`, { method: 'POST' }, 'Failed to enable mask');
}

export async function disableMask(destinationId: string): Promise<{ status: string; destination: string }> {
  return requestJson(`/${destinationId}/maskoff`, { method: 'POST' }, 'Failed to disable mask');
}

export async function getMaskState(destinationId: string): Promise<{ enabled: boolean; destination: string }> {
  return requestJson(`/${destinationId}/maskstate`, undefined, 'Failed to get mask state');
}

export async function getLightsense() {
  return requestJson('/lightsensor/lightsense', undefined, 'Failed to fetch lightsense data');
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export async function getIntensitySettings(): Promise<any> {
  return requestJson('/lightsensor/intensity-settings', undefined, 'Failed to fetch intensity settings');
}

export async function updateIntensitySettings(
  sensorName: string,
  settings: { points: Array<{ lux: number; intensity: number }>; target_group: string },
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
): Promise<any> {
  return requestJson(
    `/lightsensor/intensity-settings/${sensorName}`,
    jsonInit('PUT', settings),
    'Failed to update intensity settings',
  );
}
