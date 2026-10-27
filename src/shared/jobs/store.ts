// Live generation jobs for this page: one shared SSE stream per target, read with useJobs() (docs/JOBS.md).
import { useMemo, useSyncExternalStore } from 'react';
import { openJobStream, type Job } from '@/shared/api/jobs';

export interface JobsState {
  /** Jobs by id: active ones plus recently finished ones (finished jobs drop out after RETAIN_FINISHED_MS). */
  jobs: Record<string, Job>;
  connected: boolean;
  /** Server clock minus this device's (s), from the last snapshot; job times are the server's. */
  clockOffset: number;
}

const RETAIN_FINISHED_MS = 10 * 60 * 1000;
const EMPTY: JobsState = { jobs: {}, connected: false, clockOffset: 0 };

/** Now on the server's clock (epoch s), for comparing with a job's times. */
export function serverNow(clockOffset: number): number {
  return Date.now() / 1000 + clockOffset;
}

class JobsFeed {
  private state: JobsState = EMPTY;
  private listeners = new Set<() => void>();
  private close: (() => void) | null = null;
  private stopTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(private readonly target?: string) {}

  getState = (): JobsState => this.state;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    if (this.stopTimer) {
      clearTimeout(this.stopTimer);
      this.stopTimer = null;
    }
    if (!this.close) this.start();
    return () => {
      this.listeners.delete(listener);
      // Linger briefly so a remount (route change, StrictMode) doesn't reopen the stream.
      if (this.listeners.size === 0) this.stopTimer = setTimeout(() => this.stop(), 5000);
    };
  };

  private start() {
    this.close = openJobStream(
      {
        onSnapshot: (jobs, serverTime) => {
          const clockOffset = serverTime === null ? this.state.clockOffset : serverTime - Date.now() / 1000;
          // Keep finished jobs we already know (the snapshot only carries the last minute's).
          const next: Record<string, Job> = {};
          for (const job of Object.values(this.state.jobs)) {
            if (job.finished_at) next[job.id] = job;
          }
          for (const job of jobs) next[job.id] = job;
          this.set({ ...this.state, clockOffset, jobs: this.prune(next, clockOffset) });
        },
        onJob: (job) => {
          const known = this.state.jobs[job.id];
          if (known && known.updated_at > job.updated_at) return;
          this.set({ ...this.state, jobs: this.prune({ ...this.state.jobs, [job.id]: job }, this.state.clockOffset) });
        },
        onConnectedChange: (connected) => {
          if (connected !== this.state.connected) this.set({ ...this.state, connected });
        },
      },
      this.target,
    );
  }

  private stop() {
    this.close?.();
    this.close = null;
    this.stopTimer = null;
    this.state = EMPTY;
  }

  private prune(jobs: Record<string, Job>, clockOffset: number): Record<string, Job> {
    const cutoff = serverNow(clockOffset) - RETAIN_FINISHED_MS / 1000;
    for (const [id, job] of Object.entries(jobs)) {
      if (job.finished_at && job.finished_at < cutoff) delete jobs[id];
    }
    return jobs;
  }

  private set(state: JobsState) {
    this.state = state;
    this.listeners.forEach((listener) => listener());
  }
}

const feeds = new Map<string, JobsFeed>();

function feedFor(target?: string): JobsFeed {
  const key = target ?? '*';
  let feed = feeds.get(key);
  if (!feed) {
    feed = new JobsFeed(target);
    feeds.set(key, feed);
  }
  return feed;
}

/** Live jobs (all targets, or one publish destination). Jobs are newest-first in `list`. */
export function useJobs(target?: string): JobsState & { list: Job[] } {
  const feed = feedFor(target);
  const state = useSyncExternalStore(feed.subscribe, feed.getState);
  const list = useMemo(() => Object.values(state.jobs).sort((a, b) => b.created_at - a.created_at), [state.jobs]);
  return { ...state, list };
}
