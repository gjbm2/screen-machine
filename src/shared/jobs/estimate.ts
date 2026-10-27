// How far along a generation job is, for its progress ring (docs/JOBS.md, Job.estimate).
//
// The ring measures generation, the part that is predictable per workflow once a GPU is warm (owner, 27 Oct: cold
// starts vary a lot and must not dominate). Getting a GPU ready (queue, cold boot, model load) is never estimated:
// the ring holds a small slice and creeps very slowly while it waits, and no time left is given.
//   preparing   0    -> 0.08  over estimate.prepare_s
//   waiting     0.08 -> 0.12  an extremely slow creep (submitted, no generation frame yet)
//   generating  0.12 -> 0.95  by elapsed / estimate.generate_s, pulled ahead by the job's real percent
//   publishing  0.95 -> 0.99  over estimate.publish_s
//   done        1 when completed
// A phase that runs long approaches its end ever more slowly: it never stalls dead, never jumps, never reaches the
// next phase's share early. Worked out on the client from the job's timestamps, so it moves between server events.
import { useEffect, useRef, useState } from 'react';
import { isActive, type Job, type JobEstimate } from '@/shared/api/jobs';

export type JobPhase = 'preparing' | 'waiting' | 'generating' | 'publishing' | 'done';

export interface JobProgress {
  /** 0..1 overall. */
  fraction: number;
  /** Seconds left (generate + publish still to come), or null while waiting for a GPU (we honestly don't know). */
  remainingS: number | null;
  phase: JobPhase;
  /** Seconds generating or publishing has run past what it usually takes (0 when on time). */
  overrunS: number;
}

/** The tracker's own defaults (basis "default"), used when a job carries no estimate. */
const DEFAULT_ESTIMATE: JobEstimate = { prepare_s: 4, generate_s: 30, publish_s: 3, samples: 0, basis: 'default' };

const PREPARE_END = 0.08;
const WAIT_END = 0.12;
const GENERATE_END = 0.95;
const PUBLISH_END = 0.99;
/** Seconds of waiting to cover half the waiting slice (0.08 -> 0.10 after a minute, ~0.11 after three). */
const WAIT_HALF_S = 60;

/** Linear up to the knee, then a hyperbolic tail toward 1 with the same slope: smooth, slow, never quite 1. */
const KNEE = 0.8;
function soft(x: number): number {
  if (!(x > 0)) return 0;
  if (x <= KNEE) return x;
  const t = (x - KNEE) / (1 - KNEE);
  return KNEE + (1 - KNEE) * (t / (1 + t));
}

function secs(v: number | undefined): number {
  return typeof v === 'number' && Number.isFinite(v) && v > 0 ? v : 0;
}

function estimateOf(job: Job): JobEstimate {
  const e = job.estimate;
  if (!e) return DEFAULT_ESTIMATE;
  return {
    ...e,
    prepare_s: secs(e.prepare_s) || DEFAULT_ESTIMATE.prepare_s,
    generate_s: secs(e.generate_s) || DEFAULT_ESTIMATE.generate_s,
    publish_s: secs(e.publish_s) || DEFAULT_ESTIMATE.publish_s,
  };
}

function stamp(t: number | null | undefined): number | null {
  return typeof t === 'number' && Number.isFinite(t) && t > 0 ? t : null;
}

/** Which part of the run the job is in, from its status and timestamps. */
export function jobPhase(job: Job): JobPhase {
  if (!isActive(job)) return 'done';
  if (job.status === 'publishing' || stamp(job.publishing_at)) return 'publishing';
  // Generation starts at the first real progress frame (models loaded, sampling). A running job without one is
  // still loading or booting: that's cold-start time, so it waits with the queue rather than filling the ring.
  if (stamp(job.generating_at)) return 'generating';
  if (job.status === 'preparing' && !stamp(job.submitted_at)) return 'preparing';
  return 'waiting';
}

const lerp = (a: number, b: number, f: number) => a + (b - a) * f;

export function jobProgress(job: Job, nowServerS: number): JobProgress {
  const phase = jobPhase(job);
  if (phase === 'done') {
    // failed or cancelled jobs report 0; useJobProgress holds wherever the ring had got to
    return { fraction: job.status === 'completed' ? 1 : 0, remainingS: job.status === 'completed' ? 0 : null, phase, overrunS: 0 };
  }
  const est = estimateOf(job);
  const since = (t: number | null) => Math.max(0, nowServerS - (t ?? job.created_at));

  if (phase === 'preparing') {
    const elapsed = since(job.created_at);
    return {
      fraction: PREPARE_END * soft(elapsed / est.prepare_s),
      // assumes a warm GPU; once it's waiting for one the time left is withheld
      remainingS: Math.max(0, est.prepare_s - elapsed) + est.generate_s + est.publish_s,
      phase,
      overrunS: 0, // a slow prepare just eats into the slice; the time left still counts what's to come
    };
  }

  if (phase === 'waiting') {
    const waited = since(stamp(job.submitted_at) ?? stamp(job.started_at));
    return { fraction: PREPARE_END + (WAIT_END - PREPARE_END) * (waited / (waited + WAIT_HALF_S)), remainingS: null, phase, overrunS: 0 };
  }

  if (phase === 'generating') {
    // generating_at is set for this phase; started_at only as a fallback (a worker without the progress relay)
    const elapsed = since(stamp(job.generating_at) ?? stamp(job.started_at) ?? stamp(job.submitted_at));
    const timeBased = soft(elapsed / est.generate_s);
    const real = job.percent != null && Number.isFinite(job.percent) ? Math.min(1, Math.max(0, job.percent / 100)) : 0;
    const g = Math.max(timeBased, real);
    let remainingGen: number;
    let overrunS = 0;
    if (real > timeBased) {
      // real steps lead: go by their rate once there's enough of it to trust, else by the usual time
      remainingGen = real >= 0.15 ? (elapsed * (1 - real)) / real : est.generate_s * (1 - real);
    } else {
      remainingGen = Math.max(0, est.generate_s - elapsed);
      overrunS = Math.max(0, elapsed - est.generate_s);
    }
    return { fraction: lerp(WAIT_END, GENERATE_END, g), remainingS: remainingGen + est.publish_s, phase, overrunS };
  }

  // publishing
  const elapsed = since(stamp(job.publishing_at) ?? job.updated_at);
  return {
    fraction: lerp(GENERATE_END, PUBLISH_END, soft(elapsed / est.publish_s)),
    remainingS: Math.max(0, est.publish_s - elapsed),
    phase,
    overrunS: Math.max(0, elapsed - est.publish_s),
  };
}

const TICK_MS = 500;
const IDLE: JobProgress = { fraction: 0, remainingS: null, phase: 'done', overrunS: 0 };

/**
 * jobProgress, re-worked about every 500 ms while `enabled` and the job is active (one small interval, none
 * otherwise). The fraction shown never goes backwards for a job, even when its estimate is revised.
 */
export function useJobProgress(job: Job | null | undefined, clockOffset: number, enabled = true): JobProgress {
  const ticking = !!job && enabled && isActive(job);
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!ticking) return;
    const id = setInterval(() => setTick((t) => (t + 1) % 1_000_000), TICK_MS);
    return () => clearInterval(id);
  }, [ticking]);

  const high = useRef<{ id: string; fraction: number } | null>(null);
  if (!job) return IDLE;
  const p = jobProgress(job, Date.now() / 1000 + clockOffset);
  const prev = high.current && high.current.id === job.id ? high.current.fraction : 0;
  const fraction = job.status === 'completed' ? 1 : Math.max(prev, p.fraction);
  high.current = { id: job.id, fraction };
  return { ...p, fraction };
}
