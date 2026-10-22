// The 3 s mask poll for the controlled page (today's useMask.ts cadence and endpoint).
// Runs only while enabled (socket down for 5 s, or the server does not push masks).
// A failed poll keeps the last mask (LIVE_V1 §2.2 rule 2); each fetch has a 4 s timeout.
import { Api } from "@/utils/api";
import type { MaskValues } from "./protocol";

const POLL_MS = 3_000;
const FETCH_TIMEOUT_MS = 4_000;

/**
 * The fields of a `/api/<id>/mask` body that are usable (MaskController.normaliseMask validates and
 * clamps them), or null if there are none.
 */
export function pickMask(body: unknown): Partial<MaskValues> | null {
  if (!body || typeof body !== "object") return null;
  const b = body as Record<string, unknown>;
  const m: Partial<MaskValues> = {};
  if (typeof b.brightness === "number" && Number.isFinite(b.brightness)) m.brightness = b.brightness;
  if (typeof b.warm_hex === "string") m.warm_hex = b.warm_hex;
  if (typeof b.warm_alpha === "number" && Number.isFinite(b.warm_alpha)) m.warm_alpha = b.warm_alpha;
  return Object.keys(m).length ? m : null;
}

export class MaskPoller {
  private enabled = false;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private inflight: AbortController | null = null;
  failures = 0;

  constructor(
    private destinationId: string,
    /** `startedAt`: performance.now() when the fetch began (lets a newer pushed value win). */
    private onMask: (m: Partial<MaskValues>, startedAt: number) => void,
    private log?: (msg: string, detail?: unknown) => void,
  ) {}

  setEnabled(on: boolean): void {
    if (on === this.enabled) return;
    this.enabled = on;
    if (on) {
      void this.poll();
    } else {
      if (this.timer) clearTimeout(this.timer);
      this.timer = null;
      this.inflight?.abort();
      this.inflight = null;
    }
  }

  get isEnabled(): boolean {
    return this.enabled;
  }

  stop(): void {
    this.setEnabled(false);
  }

  private async poll(): Promise<void> {
    if (!this.enabled || this.inflight) return;
    this.timer = null;
    const ctl = new AbortController();
    this.inflight = ctl;
    const startedAt = performance.now();
    const kill = setTimeout(() => ctl.abort(), FETCH_TIMEOUT_MS);
    try {
      const url = `${new Api().getApiUrl()}/${this.destinationId}/mask`;
      const res = await fetch(url, { signal: ctl.signal, cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const body = await res.json();
      // Only well-formed fields are passed on; a missing or malformed one keeps its last value
      // (useMask.ts maps a missing brightness to 0, which blacks the screen: DCL v3 §8.1).
      const m = pickMask(body);
      if (!m) throw new Error("mask response has no usable field");
      if (this.enabled && this.inflight === ctl) this.onMask(m, startedAt);
      this.failures = 0;
    } catch (e) {
      // Hold the last mask: never reset to brightness 0 (that blacks the screen).
      // (An abort because polling was switched off is not a failure.)
      if (this.inflight === ctl) {
        this.failures++;
        this.log?.("mask poll failed; holding last mask", String(e));
      }
    } finally {
      clearTimeout(kill);
      if (this.inflight === ctl) {
        this.inflight = null;
        if (this.enabled) this.timer = setTimeout(() => void this.poll(), POLL_MS);
      }
    }
  }
}
