// Plain words for a generation job (docs/JOBS.md), shared by the generator's jobs dock and the Jobs page.

import { useEffect, useState } from "react";
import { toast } from "sonner";
import { cancelAllJobs, cancelJob, type Job, type JobSource, type JobStatus } from "@/shared/api/jobs";
import type { Tone } from "@/pages/Room/tones";

export const STATUS_WORDS: Record<JobStatus, string> = {
  preparing: "Getting ready",
  queued: "Waiting for a GPU",
  running: "Making it",
  publishing: "Finishing",
  completed: "Done",
  failed: "Failed",
  cancelled: "Cancelled",
};

export const STATUS_TONES: Record<JobStatus, Tone> = {
  preparing: "info",
  queued: "info",
  running: "live",
  publishing: "live",
  completed: "ok",
  failed: "bad",
  cancelled: "muted",
};

export const SOURCE_WORDS: Record<JobSource, string> = {
  generator: "Generator",
  scheduler: "Schedule",
  alexa: "Alexa",
  api: "API",
};

export function screenName(job: Pick<Job, "target" | "target_name">): string {
  return job.target_name || job.target || "Not published";
}

/** One line: what it's doing now ("Sampling 12/28"), else the status in words, with "step 2 of 3" when staged. */
export function jobLine(job: Job): string {
  if (job.cancel_requested && job.status !== "cancelled" && job.status !== "completed" && job.status !== "failed") return "Cancelling…";
  if (job.status === "failed") return job.error || "Failed";
  const words = job.message || STATUS_WORDS[job.status];
  const staged = job.stages.length > 1 && job.stage_index !== null && job.status === "running";
  return staged ? `${words} · step ${job.stage_index! + 1} of ${job.stages.length}` : words;
}

export function formatDuration(seconds: number | null): string | null {
  if (seconds === null || !Number.isFinite(seconds)) return null;
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ${String(s % 60).padStart(2, "0")} s`;
  return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} min`;
}

export function formatCost(gbp: number | null): string | null {
  if (gbp === null || !Number.isFinite(gbp)) return null;
  if (gbp > 0 && gbp < 0.001) return "under £0.001";
  return `£${gbp.toFixed(gbp < 0.1 ? 3 : 2)}`;
}

/** "just now", "4 min ago", "3 h ago", then the day and time. `now` and `t` on the same (server) clock. */
export function relativeTime(t: number, now: number): string {
  const ago = now - t;
  if (ago < 45) return "just now";
  if (ago < 3600) return `${Math.round(ago / 60)} min ago`;
  if (ago < 6 * 3600) return `${Math.round(ago / 3600)} h ago`;
  const d = new Date(t * 1000);
  const today = new Date(now * 1000);
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (d.toDateString() === today.toDateString()) return `today ${time}`;
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  if (d.toDateString() === yesterday.toDateString()) return `yesterday ${time}`;
  return `${d.toLocaleDateString([], { day: "numeric", month: "short" })} ${time}`;
}

/** The time, re-rendering every `ms` while `on`. */
export function useNow(ms: number, on = true): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!on) return;
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(id);
  }, [ms, on]);
  return now;
}

export async function cancelOneJob(job: Job): Promise<void> {
  try {
    await cancelJob(job.id);
  } catch (e) {
    toast.error(e instanceof Error ? e.message : "Could not cancel the job");
  }
}

export async function cancelEveryJob(): Promise<void> {
  const id = toast.loading("Cancelling all jobs…");
  try {
    const { cancelled } = await cancelAllJobs();
    toast.success(cancelled ? `Cancelled ${cancelled} job${cancelled === 1 ? "" : "s"}` : "Nothing was running", { id });
  } catch (e) {
    toast.error(e instanceof Error ? e.message : "Could not cancel the jobs", { id });
  }
}
