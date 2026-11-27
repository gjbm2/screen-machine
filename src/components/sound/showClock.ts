// show_service, from a page: its API through room_service's /show pass-through, and its clock (time.monotonic, the
// look engine's and the beat clock's) matched to this page's performance.now() for the calibrations that time against
// it (CameraCalibration, SoundInCalibration).

import { resolveRoomUrl } from "@/pages/Room/api";
import { clockOffset, type ClockSample } from "./cameraCal";

export function showUrl(path: string): string {
  return `${resolveRoomUrl(window.location.search)}/show${path}`;
}

export async function showJson<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(showUrl(path), {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
    cache: "no-store",
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error((j as { detail?: string; error?: string }).detail || (j as { error?: string }).error || `show: ${r.status}`);
  return j as T;
}

/** show's clock - this page's (ms), from the quickest of `n` round trips. */
export async function showClockOffset(n = 12): Promise<{ offset: number; rtt: number }> {
  const samples: ClockSample[] = [];
  for (let i = 0; i < n; i++) {
    const sent = performance.now();
    const r = await showJson<{ mono_s: number }>("GET", "/api/v1/clock");
    samples.push({ sent, got: performance.now(), server: r.mono_s * 1000 });
  }
  const co = clockOffset(samples);
  if (!co) throw new Error("show's clock didn't answer");
  return co;
}
