// Move it's sender (docs/LIGHTS.md §8.3): a finger on the pad makes far more moves than the light needs, so only the
// latest is kept, one request is out at a time, and at most `hz` go per second. show_service glides between the
// targets it gets, so the beam follows the finger smoothly. No React and nothing imported at run time, so lib.test.ts
// runs it under plain node (--experimental-strip-types), with a fake clock.

export interface JogClock {
  now(): number;
  setTimeout(fn: () => void, ms: number): unknown;
  clearTimeout(handle: unknown): void;
}

export const REAL_CLOCK: JogClock = {
  now: () => Date.now(),
  setTimeout: (fn, ms) => setTimeout(fn, ms),
  clearTimeout: (h) => clearTimeout(h as ReturnType<typeof setTimeout>),
};

export interface JogSenderOptions<T> {
  /** At most this many sends a second (show.yaml lights.jog.send_hz). */
  hz: number;
  /** How a newer value folds into one still waiting (default: the newer wins whole). */
  merge?: (older: T, newer: T) => T;
  clock?: JogClock;
}

/** Latest wins, one in flight, at most `hz` a second. `send`'s own failures are its business (it should catch). */
export class JogSender<T> {
  private readonly send: (value: T) => Promise<unknown>;
  private readonly merge: (older: T, newer: T) => T;
  private readonly clock: JogClock;
  private hz: number;
  private pending: { value: T } | null = null;
  private inFlight = false;
  private last = Number.NEGATIVE_INFINITY;
  private timer: unknown = null;
  private stopped = false;
  private waiters: (() => void)[] = [];
  /** How many sends have gone (tests, and the page's "is anything moving"). */
  sent = 0;

  constructor(send: (value: T) => Promise<unknown>, opts: JogSenderOptions<T>) {
    this.send = send;
    this.merge = opts.merge ?? ((_older, newer) => newer);
    this.clock = opts.clock ?? REAL_CLOCK;
    this.hz = opts.hz > 0 ? opts.hz : 20;
  }

  /** A new value: kept (folded into one still waiting) and sent as soon as the rate and the last reply allow. */
  push(value: T): void {
    if (this.stopped) return;
    this.pending = this.pending ? { value: this.merge(this.pending.value, value) } : { value };
    this.pump();
  }

  setHz(hz: number): void {
    if (hz > 0) this.hz = hz;
  }

  /** Something waiting or out. */
  get busy(): boolean {
    return this.inFlight || this.pending !== null;
  }

  /** Resolves once nothing waits and nothing is out (before reading back what the light holds: "Set as home"). */
  whenIdle(): Promise<void> {
    if (!this.busy || this.stopped) return Promise.resolve();
    return new Promise((resolve) => this.waiters.push(resolve));
  }

  /** Drop whatever waits; nothing more is sent. */
  stop(): void {
    this.stopped = true;
    this.pending = null;
    if (this.timer !== null) this.clock.clearTimeout(this.timer);
    this.timer = null;
    this.settle();
  }

  private settle(): void {
    if (this.busy && !this.stopped) return;
    const w = this.waiters;
    this.waiters = [];
    for (const resolve of w) resolve();
  }

  private pump(): void {
    if (this.stopped || this.inFlight || this.pending === null || this.timer !== null) return;
    const wait = this.last + 1000 / this.hz - this.clock.now();
    if (wait > 0) {
      this.timer = this.clock.setTimeout(() => {
        this.timer = null;
        this.pump();
      }, wait);
      return;
    }
    const { value } = this.pending;
    this.pending = null;
    this.inFlight = true;
    this.last = this.clock.now();
    this.sent += 1;
    const done = () => {
      this.inFlight = false;
      this.pump();
      this.settle();
    };
    let p: Promise<unknown>;
    try {
      p = this.send(value);
    } catch {
      p = Promise.resolve();
    }
    p.then(done, done);
  }
}
