// Holds a picture's own overlay (the prompt caption the publisher sends with it) until that picture is on glass.
//
// The publisher sends the caption the moment the file lands on disk, but the page finds the file by polling (1 s) and
// then decodes and cross-fades it in, so the caption used to pop up over the old picture. Now the controlled page
// tells this when a base change starts and when it has landed (cross-fade done); a caption waits for that:
//   * a change already in flight: shown when it lands;
//   * one landed moments ago (the poll beat the caption): shown at once;
//   * none yet: wait GRACE_MS for one to start (the poll's lag), else show it anyway (a re-publish of the same file
//     changes nothing on glass). Never held past MAX_WAIT_MS: degrade, never refuse.

export const GRACE_MS = 3000;
export const MAX_WAIT_MS = 8000;
export const RECENT_MS = 2000;

interface Waiter {
  show: () => void;
  timer: ReturnType<typeof setTimeout> | null;
}

export class BaseLanding {
  private busy = false;
  private landedAt = Number.NEGATIVE_INFINITY;
  private waiting: Waiter[] = [];
  private now: () => number;

  constructor(now: () => number = () => performance.now()) {
    this.now = now;
  }

  /** A base change has started (the new picture is decoding or fading in). */
  started(): void {
    this.busy = true;
    for (const w of this.waiting) this.arm(w, MAX_WAIT_MS);
  }

  /** The new picture is fully on glass. */
  landed(): void {
    this.busy = false;
    this.landedAt = this.now();
    this.flush();
  }

  /** Run `show` once the base it belongs to is on glass. */
  whenLanded(show: () => void): void {
    if (!this.busy && this.now() - this.landedAt < RECENT_MS) {
      show();
      return;
    }
    const w: Waiter = { show, timer: null };
    this.waiting.push(w);
    this.arm(w, this.busy ? MAX_WAIT_MS : GRACE_MS);
  }

  dispose(): void {
    for (const w of this.waiting) if (w.timer) clearTimeout(w.timer);
    this.waiting = [];
  }

  private arm(w: Waiter, ms: number): void {
    if (w.timer) clearTimeout(w.timer);
    w.timer = setTimeout(() => this.release(w), ms);
  }

  private release(w: Waiter): void {
    const i = this.waiting.indexOf(w);
    if (i < 0) return;
    this.waiting.splice(i, 1);
    if (w.timer) clearTimeout(w.timer);
    w.show();
  }

  private flush(): void {
    for (const w of [...this.waiting]) this.release(w);
  }
}
