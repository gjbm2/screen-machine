// The Lights setup page's client: show_service's rig routes (contract A), reached through room_service's /show
// pass-through the way the Room page's Music card reaches show_service (room_service/renderer_proxy.py: any method,
// with room_service's CORS policy). The room's address is the Room page's own (?room=, then the remembered one, then
// the default: same-origin /rs over HTTPS, this host's :8700 over http). No auth.

import { resolveRoomUrl } from "@/pages/Room/api";
import { problemWords, type FixtureBody } from "./lib";

/** A request that hasn't answered in this long counts as unreachable (keeps the 2 s poll from piling up). */
const REQUEST_TIMEOUT_MS = 10_000;

export class LightsApiError extends Error {
  /** HTTP status, or 0 when nothing answered. */
  status: number;
  /** The server's `error` code, or a local one ("network"). */
  code: string;
  detail: string;
  /** The server's problems with the request, in its words. */
  problems: string[];

  constructor(status: number, code: string, detail: string, problems: string[] = []) {
    super(detail ? `${code}: ${detail}` : code);
    this.status = status;
    this.code = code;
    this.detail = detail;
    this.problems = problems;
  }
}

/** The lights side couldn't be reached at all (not a refusal of what was asked). */
export function unreached(e: unknown): boolean {
  return e instanceof LightsApiError && (e.status === 0 || e.status === 502 || e.status === 503 || e.status === 504);
}

/** One sentence for the person; the server's problems are listed separately (LightsApiError.problems). */
export function explainLightsError(e: unknown): string {
  if (!(e instanceof LightsApiError)) return e instanceof Error ? e.message : String(e);
  if (e.status === 0) return "Can't reach the room. Check you're on the house Wi-Fi; the page keeps trying.";
  if (unreached(e)) return "The lights side isn't answering just now; the page keeps trying.";
  if (e.status === 404 && /^http_404$|^not_found$/.test(e.code)) {
    return "The lights side doesn't know how to do that yet (it needs updating).";
  }
  if (e.detail) return e.detail;
  if (e.problems.length) return "That wasn't accepted:";
  return `That didn't work (${e.status}).`;
}

export interface IdentifyResult {
  ok: boolean;
  why?: string;
}

export class LightsApi {
  /** room_service's address (the Room page's). */
  readonly roomUrl: string;

  constructor(roomUrl: string = resolveRoomUrl(window.location.search)) {
    this.roomUrl = roomUrl;
  }

  private async json<T>(method: string, path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
    const ac = new AbortController();
    const onAbort = () => ac.abort();
    signal?.addEventListener("abort", onAbort);
    if (signal?.aborted) ac.abort();
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      ac.abort();
    }, REQUEST_TIMEOUT_MS);
    try {
      const res = await fetch(`${this.roomUrl}/show/api/v1/rig${path}`, {
        method,
        headers: body === undefined ? {} : { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: ac.signal,
        cache: "no-cache",
      });
      const text = res.status === 204 ? "" : await res.text();
      let parsed: unknown = undefined;
      try {
        parsed = text ? JSON.parse(text) : undefined;
      } catch {
        if (res.ok) throw new LightsApiError(502, "bad_json", "The lights side sent something unreadable.");
      }
      if (!res.ok) {
        // Errors are {"error": code, "detail": "…", ...extra} (show_service api.py _err); tolerate anything else.
        const j = parsed && typeof parsed === "object" ? (parsed as Record<string, unknown>) : {};
        throw new LightsApiError(
          res.status,
          typeof j.error === "string" ? j.error : `http_${res.status}`,
          typeof j.detail === "string" ? j.detail : "",
          problemWords(j.problems),
        );
      }
      return parsed as T;
    } catch (e) {
      if (e instanceof LightsApiError) throw e;
      if (timedOut) throw new LightsApiError(0, "network", `no reply in ${REQUEST_TIMEOUT_MS / 1000} s`);
      if (e instanceof DOMException && e.name === "AbortError") throw e;
      throw new LightsApiError(0, "network", e instanceof Error ? e.message : String(e));
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
    }
  }

  // -- the lights ----------------------------------------------------------------------------------------------------------

  /** The whole rig view (read with lib.rowsFromRig). */
  rig(signal?: AbortSignal) {
    return this.json<unknown>("GET", "", undefined, signal);
  }

  /** The kinds of light it can drive (read with lib.typesFrom). */
  types(signal?: AbortSignal) {
    return this.json<unknown>("GET", "/types", undefined, signal);
  }

  /** Add a DMX light: creates its box and universe if they're new. */
  add(body: FixtureBody) {
    return this.json<{ fixture?: unknown; problems?: unknown }>("POST", "/fixtures", body);
  }

  /** Change any part of one. */
  update(id: string, body: FixtureBody) {
    return this.json<{ fixture?: unknown; problems?: unknown }>("PUT", `/fixtures/${encodeURIComponent(id)}`, body);
  }

  remove(id: string) {
    return this.json<{ removed?: string }>("DELETE", `/fixtures/${encodeURIComponent(id)}`);
  }

  /** Full white for `seconds`, whatever it was doing. */
  identify(id: string, seconds: number) {
    return this.json<IdentifyResult>("POST", `/fixtures/${encodeURIComponent(id)}/identify`, { seconds });
  }

  // -- Move it (docs/LIGHTS.md §8.1) -------------------------------------------------------------------------------------

  /** Hold the light and move it: `{}` holds it where it is (or keeps holding), `release` hands it back to the show,
   *  `free` ignores its limits (while they're being set). */
  jog(id: string, values: Record<string, unknown>, opts: { release?: boolean; free?: boolean } = {}) {
    const body: Record<string, unknown> = { values };
    if (opts.release) body.release = true;
    if (opts.free) body.free = true;
    return this.json<JogResult>("POST", `/fixtures/${encodeURIComponent(id)}/jog`, body);
  }

  /** Keep something about the light: home / beam ("here" or a value), a limit end, limits, slot names, … */
  setup(id: string, body: Record<string, unknown>) {
    return this.json<{ setup?: unknown; problems?: unknown }>("POST", `/fixtures/${encodeURIComponent(id)}/setup`, body);
  }

  /** Save where it points now under a name. */
  savePosition(id: string, name: string, label?: string) {
    return this.json<{ positions?: unknown; point?: { name: string } | null; problems?: unknown }>(
      "POST",
      `/fixtures/${encodeURIComponent(id)}/positions`,
      label ? { name, label } : { name },
    );
  }

  removePosition(id: string, name: string) {
    return this.json<{ positions?: unknown }>("DELETE", `/fixtures/${encodeURIComponent(id)}/positions/${encodeURIComponent(name)}`);
  }

  /** "Point it at…": it points at that place now, so it learns where it hangs. */
  teach(id: string, point: string) {
    return this.json<TeachResult>("POST", `/fixtures/${encodeURIComponent(id)}/teach`, { point });
  }

  /** Forget one place it learnt (0 is the first), or all of them. */
  forget(id: string, index?: number) {
    const tail = index === undefined ? "" : `/${index}`;
    return this.json<TeachResult>("DELETE", `/fixtures/${encodeURIComponent(id)}/teach${tail}`);
  }

  // -- the Room map --------------------------------------------------------------------------------------------------------

  /** The room (read with lib.parseSpace). */
  space(signal?: AbortSignal) {
    return this.json<unknown>("GET", "/space", undefined, signal);
  }

  setRoom(body: { size?: [number, number, number] | null; front?: string }) {
    return this.json<unknown>("PUT", "/space/room", body);
  }

  /** Where a light or screen is: `at`, `mount`, `plane` (null clears one). */
  place(id: string, body: Record<string, unknown>) {
    return this.json<unknown>("PUT", `/space/appliances/${encodeURIComponent(id)}`, body);
  }

  unplace(id: string) {
    return this.json<unknown>("DELETE", `/space/appliances/${encodeURIComponent(id)}`);
  }

  setPlace(name: string, body: { at: [number, number, number]; label?: string }) {
    return this.json<unknown>("PUT", `/space/points/${encodeURIComponent(name)}`, body);
  }

  removePlace(name: string) {
    return this.json<unknown>("DELETE", `/space/points/${encodeURIComponent(name)}`);
  }

  setArea(name: string, body: { surface: string; centre: [number, number, number]; size: [number, number]; label?: string }) {
    return this.json<unknown>("PUT", `/space/regions/${encodeURIComponent(name)}`, body);
  }

  removeArea(name: string) {
    return this.json<unknown>("DELETE", `/space/regions/${encodeURIComponent(name)}`);
  }

  /** Where every beam lands now, and the flow (read with lib.parseLive). */
  live(signal?: AbortSignal) {
    return this.json<unknown>("GET", "/live", undefined, signal);
  }
}

export interface JogResult {
  held: boolean;
  until_s?: number | null;
  idle_s?: number;
  target?: Record<string, unknown>;
  now?: Record<string, unknown>;
  unknown?: string[];
  why?: string | null;
}

export interface FitResult {
  level: string;
  words: string;
  rms_deg?: number | null;
  worst_index?: number | null;
  pose?: unknown;
}

export interface TeachResult {
  samples?: unknown[];
  fit?: FitResult;
  problems?: unknown;
}
