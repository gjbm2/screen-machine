// Media-server admin page (src/pages/AdminPanel.tsx).
//
// Every URL here is SAME-ORIGIN RELATIVE ('/api/...'), never API_URL: the admin bearer
// token must not be sent via the public-IP origin baked into VITE_API_URL.
//
// None of these check response.ok. Most resolve with the raw Response because the page
// interleaves state updates with body parsing (it clears its token prompt before parsing
// /status, and the light poll applies each parsed body as it goes, so one bad body does not
// discard the ones before it); parsing them here would change what the page shows on errors.
// Network failures reject, as fetch() does. The *Response names flag the raw ones; lightApi/publishApi have
// same-purpose functions that go via API_URL, parse, and throw on non-2xx.

import { JSON_HEADERS, jsonInit, sendRelative } from '@/shared/http/client';

/** localStorage key holding the admin bearer token (read at call time by every admin request). */
export const ADMIN_TOKEN_KEY = 'sm_admin_token';

const ADMIN_PATH = '/admin-355729b8';

function authHeaders(): Record<string, string> {
  const t = localStorage.getItem(ADMIN_TOKEN_KEY) || '';
  return t ? { Authorization: `Bearer ${t}` } : {};
}

/** GET /api/publish-destinations: the parsed, unfiltered list (hidden ones included), or [] on a non-2xx status. Rejects on a network error or an unparseable 2xx body. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function getRawPublishDestinations(): Promise<any> {
  return sendRelative('/publish-destinations').then(r => (r.ok ? r.json() : []));
}

/** GET /api/admin-355729b8/status with the bearer token (when one is stored). Raw response; the caller checks for 401 and parses. */
export function getStatusResponse(): Promise<Response> {
  return sendRelative(`${ADMIN_PATH}/status`, { headers: authHeaders() });
}

/**
 * POST /api/admin-355729b8/{action} with the bearer token and an optional JSON body.
 * A 401 resolves { status: 401 } without reading the body; any other status resolves its parsed
 * body as data (no ok check). Rejects on a network error or an unparseable body.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export async function runAction(action: string, body?: object): Promise<{ status: number; data?: any }> {
  const res = await sendRelative(`${ADMIN_PATH}/${action}`, {
    method: 'POST',
    headers: { ...JSON_HEADERS, ...authHeaders() },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) {
    return { status: res.status };
  }
  return { status: res.status, data: await res.json() };
}

/** GET /api/lightsensor/lightsense. Raw response. */
export function getLightsenseResponse(): Promise<Response> {
  return sendRelative('/lightsensor/lightsense');
}

/** GET /api/{screen}/mask. Raw response. The screen id is interpolated raw (not encoded), as before. */
export function getMaskResponse(screen: string): Promise<Response> {
  return sendRelative(`/${screen}/mask`);
}

/** GET /api/{screen}/brightness-override. Raw response. */
export function getBrightnessOverrideResponse(screen: string): Promise<Response> {
  return sendRelative(`/${screen}/brightness-override`);
}

/** POST /api/{screen}/brightness-override with JSON { brightness }. Raw response (callers ignore it). */
export function setBrightnessOverride(screen: string, brightness: number): Promise<Response> {
  return sendRelative(`/${screen}/brightness-override`, jsonInit('POST', { brightness }));
}

/** DELETE /api/{screen}/brightness-override. Raw response (callers ignore it). */
export function clearBrightnessOverride(screen: string): Promise<Response> {
  return sendRelative(`/${screen}/brightness-override`, { method: 'DELETE' });
}
