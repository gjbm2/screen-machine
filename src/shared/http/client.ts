// HTTP client for the Flask API. src/shared/api/* build and send every request through these helpers.
// Still outside it: src/pages/DisplayPage (peer-owned; MaskPoller, file polling), SchemaEditModal's
// generic save, and the legacy /display tree (D18). New code should not call fetch() on the API directly.
//
// The base URL is still baked in at build time from VITE_API_URL (the public-IP origin in
// .env), exactly as the old src/utils/api.ts resolved it. Deriving it from window.location
// instead is REFACTOR_PLAN P3 step 1 and an owner decision.

// Exception (owner, 14 Oct): loaded over HTTPS (only the friendly LAN name https://screen.finsburybankforsavings.com,
// Caddy -> Flask), the API is same-origin: an https page can't call the http public-IP origin (mixed content).
// The TVs and the public address load over http and keep VITE_API_URL.
export const API_URL: string =
  typeof window !== 'undefined' && window.location.protocol === 'https:'
    ? `${window.location.origin}/api`
    : import.meta.env.VITE_API_URL || 'http://localhost:8000';

/** Same-origin API prefix. Only the admin page uses it (its token must not go via the public IP). */
export const RELATIVE_API_URL = '/api';

/** Absolute URL for an API path, e.g. apiUrl('/buckets/x') → 'http://host:5000/api/buckets/x'. No trailing slash is added. */
export function apiUrl(path = ''): string {
  return `${API_URL}${path}`;
}

export const JSON_HEADERS = { 'Content-Type': 'application/json' } as const;

/** Error message for a non-2xx response: a fixed string, or built from the response (e.g. its statusText). */
export type FailMessage = string | ((response: Response) => string);

/** Init for a JSON request with a body. */
export function jsonInit(method: string, body: unknown): RequestInit {
  return { method, headers: JSON_HEADERS, body: JSON.stringify(body) };
}

/** fetch() an API path on API_URL, with no status handling. */
export function send(path: string, init?: RequestInit): Promise<Response> {
  return fetch(apiUrl(path), init);
}

/** fetch() a full URL already built from apiUrl() (e.g. with URLSearchParams), with no status handling. */
export function sendUrl(url: string, init?: RequestInit): Promise<Response> {
  return fetch(url, init);
}

/** fetch() a same-origin '/api' path, with no status handling. */
export function sendRelative(path: string, init?: RequestInit): Promise<Response> {
  return fetch(`${RELATIVE_API_URL}${path}`, init);
}

/** fetch() an API path; throw Error(fail) on a non-2xx status, otherwise return the response. */
export async function request(path: string, init: RequestInit | undefined, fail: FailMessage): Promise<Response> {
  const response = await send(path, init);
  if (!response.ok) {
    throw new Error(typeof fail === 'function' ? fail(response) : fail);
  }
  return response;
}

/**
 * fetch() an API path and parse the JSON body; throw Error(fail) on a non-2xx status.
 * Responses are untyped until the backend contracts are generated (REFACTOR_PLAN T7).
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export async function requestJson<T = any>(path: string, init: RequestInit | undefined, fail: FailMessage): Promise<T> {
  const response = await request(path, init, fail);
  return response.json();
}

/** Like request(), but a non-2xx throws Error(body.error || fallback); a non-JSON error body rejects with the parse error. */
export async function requestWithErrorBody(path: string, init: RequestInit | undefined, fallback: string): Promise<Response> {
  const response = await send(path, init);
  if (!response.ok) {
    const err = await response.json();
    throw new Error(err.error || fallback);
  }
  return response;
}

/** Parse an error body as JSON, falling back to { error: <text or fallback> } when it is not JSON. */
export async function readErrorBody(response: Response, fallback: string): Promise<{ error?: string; [key: string]: unknown }> {
  const text = await response.text();
  try {
    return JSON.parse(text);
  } catch {
    return { error: text || fallback };
  }
}
