// Timed hard reloads for the controlled page (?ctl=), LIVE_V1 §2.2 rule 9.
//
// Same wall-clock schedule as today's page (02/06/10/14/18/22h, fade to black, reload; index.tsx
// scheduleNextHardReload), plus:
//   * `policy.timed_reload` false: no timed reloads at all; a scheduled one is cancelled and
//     next_reload_at is reported as null. Switching back on schedules the next slot from now.
//   * backstop, whatever the policy: a reload that falls due while live, frozen or between
//     live.attach and the end of teardown (or within 60 s of returning to art) waits; it is
//     re-checked every 5 s and runs only if timed reloads are still on.
// The default page (no ?ctl=) keeps today's scheduleNextHardReload() unchanged.

const RELOAD_HOURS = [2, 6, 10, 14, 18, 22];
const RECHECK_MS = 5_000;

/** The next reload slot after `now` (today's algorithm, index.tsx scheduleNextHardReload). */
export function nextReloadAfter(now: Date): Date {
  for (const hour of RELOAD_HOURS) {
    const scheduled = new Date(now);
    scheduled.setHours(hour, 0, 0, 0);
    if (scheduled > now) return scheduled;
  }
  const next = new Date(now);
  next.setDate(now.getDate() + 1);
  next.setHours(RELOAD_HOURS[0], 0, 0, 0);
  return next;
}

export interface ReloadSchedulerOptions {
  /** False while live, frozen, attaching/tearing down, or within 60 s of returning to art. */
  canReloadNow(): boolean;
  /** Today's fade to black and location.reload(). */
  reload(): void;
  log?: (msg: string, detail?: unknown) => void;
}

export class ReloadScheduler {
  private enabled = true;
  private running = false;
  private dueAt: Date | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private fired = false;

  constructor(private opts: ReloadSchedulerOptions) {}

  start(): void {
    if (this.running) return;
    this.running = true;
    if (this.enabled) this.arm();
  }

  stop(): void {
    this.running = false;
    this.clear();
  }

  /** Effective `timed_reload`. Off cancels the scheduled reload; on schedules the next slot. */
  setEnabled(on: boolean): void {
    if (on === this.enabled) return;
    this.enabled = on;
    this.clear();
    if (!this.running) return;
    if (on) {
      this.arm();
      this.opts.log?.("timed reloads on", this.nextReloadAt());
    } else {
      this.opts.log?.("timed reloads off (policy.timed_reload=false); scheduled reload cancelled");
    }
  }

  get isEnabled(): boolean {
    return this.enabled;
  }

  /** ISO time of the pending reload (in the past while one is deferred), or null when off. */
  nextReloadAt(): string | null {
    return this.enabled && this.dueAt ? this.dueAt.toISOString() : null;
  }

  private clear(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.dueAt = null;
  }

  private arm(): void {
    if (this.fired) return;
    const now = new Date();
    this.dueAt = nextReloadAfter(now);
    this.timer = setTimeout(this.fire, this.dueAt.getTime() - now.getTime());
    this.opts.log?.(`timed reload scheduled at ${this.dueAt.toLocaleTimeString()}`);
  }

  private fire = (): void => {
    this.timer = null;
    if (!this.running || !this.enabled) return;
    if (!this.opts.canReloadNow()) {
      // Backstop: live, frozen, mid-attach/teardown or just back from live. Keep the due time.
      this.timer = setTimeout(this.fire, RECHECK_MS);
      return;
    }
    this.fired = true;
    this.opts.log?.("timed reload: fading to black before reload");
    this.opts.reload();
  };
}
