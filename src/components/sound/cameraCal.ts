// The lights' camera calibration, the maths (docs/calibration lighting brief, ch. 1; docs/SOUND.md "Lights"): find each
// flash in the camera's brightness trace, and turn the light's loop and the screen's loop into the light's own delay.
// Pure: no DOM, no fetch (CameraCalibration.tsx runs the session).
//
// One timebase: the page's performance.now() (ms). Camera frames are stamped when their callback runs, in both loops,
// so the camera's own delay is common to both and cancels: light = light loop - (screen loop - screen presentation).

/** One camera frame: when it reached the page, and its mean brightness (0..255). */
export interface Frame {
  t: number;
  v: number;
}

/** The trace's level in a window: the median of the frames in [from, to). null: no frames there. */
export function levelIn(frames: Frame[], from: number, to: number): number | null {
  const vs = frames.filter((f) => f.t >= from && f.t < to).map((f) => f.v);
  return vs.length ? median(vs) : null;
}

/**
 * When the trace crossed half way from its level before `at` to its level after the change (within `windowMs`):
 * rising (a flash on) or falling (off). Linear between the two frames either side of the crossing. null: no clear
 * change (its step under `minStep`, or no crossing).
 */
export function edgeAfter(frames: Frame[], at: number, rising: boolean, windowMs = 600, settleMs = 120, minStep = 6): number | null {
  const before = levelIn(frames, at - 300, at);
  const win = frames.filter((f) => f.t >= at && f.t < at + windowMs);
  if (before == null || win.length < 3) return null;
  const late = win.filter((f) => f.t >= at + windowMs - Math.max(settleMs, 0) - 150);
  const after = rising ? Math.max(...win.map((f) => f.v)) : late.length ? median(late.map((f) => f.v)) : Math.min(...win.map((f) => f.v));
  const step = after - before;
  if ((rising && step < minStep) || (!rising && -step < minStep)) return null;
  const half = before + step / 2;
  let prev: Frame | null = frames.filter((f) => f.t < at).slice(-1)[0] ?? null;
  for (const f of win) {
    const crossed = rising ? f.v >= half : f.v <= half;
    if (crossed) {
      if (!prev || prev.v === f.v) return f.t;
      const x = (half - prev.v) / (f.v - prev.v);
      return prev.t + Math.max(0, Math.min(1, x)) * (f.t - prev.t);
    }
    prev = f;
  }
  return null;
}

export function median(xs: number[]): number {
  const s = [...xs].sort((a, b) => a - b);
  const n = s.length;
  if (!n) return NaN;
  return n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2;
}

/** Half the spread of the middle half (the interquartile range / 2): the +- a result is quoted with. */
export function halfIqr(xs: number[]): number {
  const s = [...xs].sort((a, b) => a - b);
  if (s.length < 2) return 0;
  const q = (p: number) => {
    const i = p * (s.length - 1);
    const lo = Math.floor(i);
    return s[lo] + (s[Math.min(s.length - 1, lo + 1)] - s[lo]) * (i - lo);
  };
  return (q(0.75) - q(0.25)) / 2;
}

/** A clock sample: the page's time when it asked and when the answer came, and the server's clock in it (ms). */
export interface ClockSample {
  sent: number;
  got: number;
  server: number;
}

/** server - page (ms) from the quickest round trip (its answer is the least delayed): {offset, rtt}. */
export function clockOffset(samples: ClockSample[]): { offset: number; rtt: number } | null {
  const ok = samples.filter((s) => s.got >= s.sent);
  if (!ok.length) return null;
  const best = ok.reduce((a, b) => (b.got - b.sent < a.got - a.sent ? b : a));
  return { offset: best.server - (best.sent + best.got) / 2, rtt: best.got - best.sent };
}

/** One trial: when it was asked for (page ms) and when its light was seen, on and off (null: not seen). */
export interface Trial {
  on: number;
  off: number;
  seenOn: number | null;
  seenOff: number | null;
}

export interface Estimate {
  riseMs: number;
  fallMs: number | null;
  /** the screen loop minus its presentation: the camera's own delay, removed from the light's */
  cameraMs: number;
  spreadMs: number;
  used: number;
  of: number;
  screenUsed: number;
}

export const MIN_TRIALS = 5;
export const MAX_SPREAD_MS = 40;

/**
 * The light's delay (+ = seen that long after the frame was sent): the median of its loop minus the camera's own
 * delay, which is the median of the screen's loop less its presentation (`presentMs`: the frame painted a refresh
 * after it was drawn). null: too few trials seen on either side.
 */
export function estimate(light: Trial[], screen: Trial[], presentMs: number): Estimate | null {
  const lOn = light.filter((x) => x.seenOn != null).map((x) => (x.seenOn as number) - x.on);
  const sOn = screen.filter((x) => x.seenOn != null).map((x) => (x.seenOn as number) - x.on - presentMs);
  if (lOn.length < MIN_TRIALS || sOn.length < MIN_TRIALS) return null;
  const cam = median(sOn);
  const lOff = light.filter((x) => x.seenOff != null).map((x) => (x.seenOff as number) - x.off);
  const sOff = screen.filter((x) => x.seenOff != null).map((x) => (x.seenOff as number) - x.off - presentMs);
  const camOff = sOff.length >= MIN_TRIALS ? median(sOff) : cam;
  return {
    riseMs: median(lOn) - cam,
    fallMs: lOff.length >= MIN_TRIALS ? median(lOff) - camOff : null,
    cameraMs: cam,
    spreadMs: Math.max(halfIqr(lOn), halfIqr(sOn)),
    used: lOn.length,
    of: light.length,
    screenUsed: sOn.length,
  };
}

/** Whether a result is good enough to keep (the brief: never a negative or poorly supported figure). */
export function acceptable(e: Estimate | null): boolean {
  return !!e && e.riseMs >= 0 && e.spreadMs <= MAX_SPREAD_MS && e.used >= MIN_TRIALS;
}
