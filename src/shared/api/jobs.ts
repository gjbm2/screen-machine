// Generation jobs: the job tracker's REST API and live stream (docs/JOBS.md).
import { apiUrl, jsonInit, requestJson } from '@/shared/http/client';

export type JobStatus = 'preparing' | 'queued' | 'running' | 'publishing' | 'completed' | 'failed' | 'cancelled';
export type JobSource = 'generator' | 'scheduler' | 'alexa' | 'art-director' | 'api';

/**
 * Usual seconds for the predictable phases, learned from recent jobs (docs/JOBS.md). Waiting for a GPU (queue, cold
 * boot, model load) varies too much and is never estimated.
 */
export interface JobEstimate {
  prepare_s: number;
  /** generating_at -> publishing_at on a warm GPU */
  generate_s: number;
  publish_s: number;
  samples: number;
  basis: 'workflow' | 'all' | 'default';
}

export interface Job {
  id: string;
  batch_id: string | null;
  batch_index: number | null;
  placeholder_id: string | null;
  source: JobSource;
  kind: 'image' | 'video';
  target: string | null;
  target_name: string | null;
  prompt: string;
  refined_prompt: string | null;
  workflow: string | null;
  refiner: string | null;
  status: JobStatus;
  message: string | null;
  stages: string[];
  stage_index: number | null;
  percent: number | null;
  error: string | null;
  runpod_job_id: string | null;
  endpoint_id: string | null;
  created_at: number;
  submitted_at: number | null;
  started_at: number | null;
  /** First real progress frame from the worker (models loaded, sampling begun); null without the relay. */
  generating_at?: number | null;
  /** When it began publishing. Both optional: absent on jobs from before the fields existed. */
  publishing_at?: number | null;
  finished_at: number | null;
  updated_at: number;
  duration_s: number | null;
  cost_gbp: number | null;
  seed: number | null;
  result_url: string | null;
  published_path: string | null;
  cancel_requested: boolean;
  /** How long each phase usually takes; null (or absent, from an older server) when not known. */
  estimate?: JobEstimate | null;
}

export const ACTIVE_STATUSES: readonly JobStatus[] = ['preparing', 'queued', 'running', 'publishing'];

export function isActive(job: Pick<Job, 'status'>): boolean {
  return ACTIVE_STATUSES.includes(job.status);
}

export interface JobListQuery {
  state?: 'active' | 'finished' | 'all';
  limit?: number;
  before?: number;
  target?: string;
  source?: JobSource;
  q?: string;
}

export interface JobPage {
  jobs: Job[];
  next_before: number | null;
}

function queryString(query: object): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== '') params.set(key, String(value));
  }
  const text = params.toString();
  return text ? `?${text}` : '';
}

export function listJobs(query: JobListQuery = {}): Promise<JobPage> {
  return requestJson(`/jobs${queryString(query)}`, undefined, 'Could not load jobs');
}

export function getJob(id: string): Promise<Job> {
  return requestJson(`/jobs/${encodeURIComponent(id)}`, undefined, 'Could not load the job');
}

export function cancelJob(id: string): Promise<Job> {
  return requestJson(`/jobs/${encodeURIComponent(id)}/cancel`, jsonInit('POST', {}), 'Could not cancel the job');
}

export function cancelAllJobs(): Promise<{ cancelled: number }> {
  return requestJson('/jobs/cancel-all', jsonInit('POST', {}), 'Could not cancel the jobs');
}

export interface JobStreamHandlers {
  /** Active jobs plus those finished in the last minute, and the server's clock (epoch s); sent on every (re)connect. */
  onSnapshot: (jobs: Job[], serverTime: number | null) => void;
  /** The whole job after any change. */
  onJob: (job: Job) => void;
  onConnectedChange?: (connected: boolean) => void;
}

/** Open the live job stream (SSE). With `target`, only jobs for that publish destination. Returns a closer. */
export function openJobStream(handlers: JobStreamHandlers, target?: string): () => void {
  const source = new EventSource(apiUrl(`/jobs/stream${queryString({ target })}`));
  source.addEventListener('snapshot', (event) => {
    try {
      const data = JSON.parse((event as MessageEvent).data);
      handlers.onSnapshot((data.jobs || []) as Job[], typeof data.server_time === 'number' ? data.server_time : null);
    } catch {
      /* malformed frame: wait for the next */
    }
  });
  source.addEventListener('job', (event) => {
    try {
      handlers.onJob(JSON.parse((event as MessageEvent).data) as Job);
    } catch {
      /* malformed frame: wait for the next */
    }
  });
  source.onopen = () => handlers.onConnectedChange?.(true);
  // EventSource reconnects by itself; the next snapshot replaces whatever was missed.
  source.onerror = () => handlers.onConnectedChange?.(false);
  return () => source.close();
}
