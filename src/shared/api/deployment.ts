// GET /api/deployment: which installation this app is (deployments/<id>/deployment.yaml, read by smconfig on the
// server), what it can do, and its screens. See deployments/README.md.
//
// Pages use it instead of naming a site's screens or assuming a feature exists: the admin page's light masks come
// from the screens with `mask`, and the generator page checks `capabilities.generation`.

import { requestJson, sendRelative } from '@/shared/http/client';

/** One screen of the deployment (publish-destinations.json). */
export interface DeploymentScreen {
  id: string;
  name: string;
  /** It can go live (it has a `live` block). */
  live: boolean;
  /** It has a light mask (the admin page shows its mask and brightness override). */
  mask: boolean;
}

export interface DeploymentInfo {
  id: string;
  name: string;
  /** Feature switches (generation, alexa, live, ...). A capability left out is off. */
  capabilities: Record<string, boolean>;
  default_room: string | null;
  screens: DeploymentScreen[];
}

/** GET /api/deployment, parsed. Throws on a non-2xx status or a network error. */
export function getDeployment(): Promise<DeploymentInfo> {
  return requestJson<DeploymentInfo>('/deployment', undefined, 'Failed to fetch the deployment');
}

/**
 * GET /api/deployment SAME-ORIGIN RELATIVE ('/api/deployment', never API_URL), for the admin page, whose every call
 * stays on its own origin (src/shared/api/admin.ts). Parsed; throws on a non-2xx status or a network error.
 */
export async function getDeploymentRelative(): Promise<DeploymentInfo> {
  const r = await sendRelative('/deployment');
  if (!r.ok) throw new Error('Failed to fetch the deployment');
  return r.json();
}

/**
 * Is `capability` on? true / false once the deployment is known; undefined while it is unknown (not loaded yet, or the
 * request failed), so callers can leave a feature as it is rather than switch it off on a network blip.
 */
export function capabilityOn(deployment: DeploymentInfo | null | undefined, capability: string): boolean | undefined {
  if (!deployment || !deployment.capabilities) return undefined;
  return deployment.capabilities[capability] === true;
}

/** The ids of the screens that have a light mask, in file order. */
export function maskScreenIds(deployment: DeploymentInfo | null | undefined): string[] {
  return (deployment?.screens ?? []).filter(s => s && s.mask).map(s => s.id);
}
