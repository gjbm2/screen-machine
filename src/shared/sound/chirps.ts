// The calibration chirps (docs/SOUND.md), as smsound/chirps.py builds them: Hann-windowed linear sweeps, each one its
// own, rendered at their server times through Web Audio, with a quiet noise bed under them from now (it wakes the
// speaker: an idle Sonos or TV buffers more as it wakes). The display page of the screen being measured plays them.

export interface ChirpSpec {
  f0: number;
  f1: number;
  dur_s: number;
}

/** Schedules the chirps (`clicks`: server seconds; `skew`: server clock - this one, s). Returns how many were placed
 *  (the ones still in the future); 0 when the context isn't running yet. */
export function playChirps(ctx: AudioContext, clicks: number[], specs: ChirpSpec[], skew: number): number {
  if (ctx.state !== 'running' || !specs.length || !clicks.length) return 0;
  const sr = ctx.sampleRate;
  const sweep = (spec: ChirpSpec) => {
    const n = Math.max(8, Math.round(spec.dur_s * sr));
    const buf = ctx.createBuffer(1, n, sr);
    const d = buf.getChannelData(0);
    for (let k = 0; k < n; k++) {
      const t = k / sr;
      const ph = 2 * Math.PI * (spec.f0 * t + ((spec.f1 - spec.f0) * t * t) / (2 * spec.dur_s));
      d[k] = 0.9 * Math.sin(ph) * (0.5 - 0.5 * Math.cos((2 * Math.PI * k) / (n - 1)));
    }
    return buf;
  };
  const toCtx = (T: number) => ctx.currentTime + (T - (Date.now() / 1000 + skew));
  const bedEnd = toCtx(clicks[clicks.length - 1]) + 1.6;
  const bedLen = Math.max(0.5, bedEnd - ctx.currentTime);
  const bed = ctx.createBuffer(1, Math.round(bedLen * sr), sr);
  const b = bed.getChannelData(0);
  for (let k = 0; k < b.length; k++) b[k] = (Math.random() * 2 - 1) * 0.01; // about -45 dBFS
  const bs = ctx.createBufferSource();
  bs.buffer = bed;
  bs.connect(ctx.destination);
  bs.start();
  let made = 0;
  for (let i = 0; i < clicks.length; i++) {
    const when = toCtx(clicks[i]);
    if (when <= ctx.currentTime + 0.02) continue;
    const s = ctx.createBufferSource();
    s.buffer = sweep(specs[i % specs.length]);
    s.connect(ctx.destination);
    s.start(when);
    made++;
  }
  return made;
}

/**
 * The chirps leaving this device's speaker at `atPerfMs` (this page's performance.now() clock), by the audio output's
 * own timestamps (AudioContext.getOutputTimestamp: when a context time actually leaves the device), with the noise bed
 * under them. The sound-in calibration plays them (its times are show_service's, matched to this page's clock).
 * Returns how many were placed; 0 when the context isn't running or has no output timestamp yet.
 */
export function playChirpsAt(ctx: AudioContext, atPerfMs: number[], specs: ChirpSpec[]): number {
  if (ctx.state !== 'running' || !specs.length || !atPerfMs.length) return 0;
  const ts = ctx.getOutputTimestamp?.();
  if (!ts || ts.contextTime == null || ts.performanceTime == null || ts.performanceTime <= 0) return 0;
  const toCtx = (perf: number) => (ts.contextTime as number) + (perf - (ts.performanceTime as number)) / 1000;
  const sr = ctx.sampleRate;
  const sweep = (spec: ChirpSpec) => {
    const n = Math.max(8, Math.round(spec.dur_s * sr));
    const buf = ctx.createBuffer(1, n, sr);
    const d = buf.getChannelData(0);
    for (let k = 0; k < n; k++) {
      const t = k / sr;
      const ph = 2 * Math.PI * (spec.f0 * t + ((spec.f1 - spec.f0) * t * t) / (2 * spec.dur_s));
      d[k] = 0.9 * Math.sin(ph) * (0.5 - 0.5 * Math.cos((2 * Math.PI * k) / (n - 1)));
    }
    return buf;
  };
  const bedLen = Math.max(0.5, toCtx(atPerfMs[atPerfMs.length - 1]) + 1.6 - ctx.currentTime);
  const bed = ctx.createBuffer(1, Math.round(bedLen * sr), sr);
  const b = bed.getChannelData(0);
  for (let k = 0; k < b.length; k++) b[k] = (Math.random() * 2 - 1) * 0.01;
  const bs = ctx.createBufferSource();
  bs.buffer = bed;
  bs.connect(ctx.destination);
  bs.start();
  let made = 0;
  for (let i = 0; i < atPerfMs.length; i++) {
    const when = toCtx(atPerfMs[i]);
    if (when <= ctx.currentTime + 0.02) continue;
    const s = ctx.createBufferSource();
    s.buffer = sweep(specs[i % specs.length]);
    s.connect(ctx.destination);
    s.start(when);
    made++;
  }
  return made;
}
