// Variables registry and exported scheduler variables.

import { jsonInit, requestJson } from '@/shared/http/client';
import { toastedJson } from './toast';

export async function getVarsRegistry() {
  return requestJson('/vars-registry', undefined, 'Failed to fetch variables registry');
}

// varName is interpolated unencoded, as before.
export async function setExportedVar(varName: string, value: unknown) {
  return toastedJson(`/schedulers/exported-vars/${varName}`, jsonInit('POST', { value }), 'Failed to set exported variable');
}

export async function deleteExportedVar(varName: string) {
  return toastedJson(`/schedulers/exported-vars/${varName}`, { method: 'DELETE' }, 'Failed to delete exported variable');
}
