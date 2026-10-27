// The friendly words for a generation job's stage and time left (owner, 27 Oct: no technical language; elegant,
// shows progress, manages expectation). One place for all of them, so they're easy to change. Used by the TV pill,
// the generator's jobs dock and the Jobs page; the TVs show the stage only, never the time left.
import type { Job } from '@/shared/api/jobs';
import type { JobPhase } from './estimate';

export const STAGE_WORDS = {
  preparing: 'Imagining', // refining the prompt, loading the workflow
  waiting: 'Getting ready', // waiting for a GPU to start and load
  painting: 'Painting', // an image rendering (or generating, no stage named)
  animating: 'Animating', // a video rendering
  detail: 'Adding detail', // Upscaling / Scaling
  smoothing: 'Smoothing the motion', // Interpolating
  working: 'Working', // a stage name we don't know
  publishing: 'Almost there',
  completed: 'Ready',
  published: 'Published', // completed, on a TV
  failed: "Couldn't finish",
  cancelled: 'Cancelled',
  cancelling: 'Cancelling',
} as const;

/** Workflow stage names (workflows.json `processing_stages`, lower case) -> the words. */
const STAGE_NAMES: Record<string, keyof typeof STAGE_WORDS> = {
  rendering: 'painting',
  upscaling: 'detail',
  scaling: 'detail',
  interpolating: 'smoothing',
};

export const TIME_WORDS = {
  fewSeconds: 'A few seconds',
  seconds: (n: number) => `About ${n} seconds`,
  aMinute: 'About a minute',
  minutes: (n: number) => `About ${n} minutes`,
  nearly: 'Nearly there', // a little past the usual time
  longer: 'Taking a little longer', // well past it
};

/** Past the usual time by more than this, it's "taking a little longer" rather than "nearly there". */
const NEARLY_S = 15;

type StageJob = Pick<Job, 'status' | 'kind' | 'stages' | 'stage_index' | 'cancel_requested'>;

/** What it's doing now, in one or two plain words. `phase` from jobProgress / useJobProgress. */
export function stageLabel(job: StageJob, phase: JobPhase): string {
  if (job.status === 'completed') return STAGE_WORDS.completed;
  if (job.status === 'failed') return STAGE_WORDS.failed;
  if (job.status === 'cancelled') return STAGE_WORDS.cancelled;
  if (job.cancel_requested) return STAGE_WORDS.cancelling;
  switch (phase) {
    case 'preparing':
      return STAGE_WORDS.preparing;
    case 'waiting':
      return STAGE_WORDS.waiting;
    case 'publishing':
      return STAGE_WORDS.publishing;
    case 'generating': {
      const name = job.stage_index != null ? job.stages[job.stage_index] : undefined;
      const key = name ? STAGE_NAMES[name.trim().toLowerCase()] ?? 'working' : 'painting';
      if (key === 'painting' && job.kind === 'video') return STAGE_WORDS.animating;
      return STAGE_WORDS[key];
    }
    default:
      return STAGE_WORDS.working;
  }
}

/**
 * The time left, roughly: "A few seconds", "About 30 seconds", "About a minute", "About 4 minutes"; past the usual
 * time (`overrunS` > 0) "Nearly there", then "Taking a little longer". Unknown (waiting for a GPU): "".
 */
export function remainingLabel(remainingS: number | null, overrunS = 0): string {
  if (remainingS === null || !Number.isFinite(remainingS)) return '';
  if (overrunS > 0) return overrunS < NEARLY_S ? TIME_WORDS.nearly : TIME_WORDS.longer;
  const s = Math.max(0, remainingS);
  if (s < 10) return TIME_WORDS.fewSeconds;
  if (s < 55) return TIME_WORDS.seconds(s < 30 ? Math.round(s / 5) * 5 : Math.round(s / 10) * 10);
  if (s < 90) return TIME_WORDS.aMinute;
  return TIME_WORDS.minutes(Math.round(s / 60));
}

/** "Painting · About 30 seconds", or just the stage when the time left isn't known. */
export function progressLine(job: StageJob, p: { phase: JobPhase; remainingS: number | null; overrunS: number }): string {
  const stage = stageLabel(job, p.phase);
  const time = p.phase === 'done' || job.cancel_requested ? '' : remainingLabel(p.remainingS, p.overrunS);
  return time ? `${stage} · ${time}` : stage;
}
