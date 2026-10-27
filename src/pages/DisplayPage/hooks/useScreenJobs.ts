// src/pages/DisplayPage/hooks/useScreenJobs.ts
// What the job pill shows for this screen, from the job tracker's stream (docs/JOBS.md).
import { useEffect, useRef, useState } from "react";
import { isActive, type Job } from "@/shared/api/jobs";
import { useJobs } from "@/shared/jobs/store";

/** How long a finished job stays on the pill. */
const PUBLISHED_MS = 2000;
const FAILED_MS = 6000;

export type ScreenJobView =
  | { kind: "active"; job: Job; more: number }
  | { kind: "published"; job: Job }
  | { kind: "failed"; job: Job }
  | null;

/**
 * Jobs rendering to `screenId` (its publish destination id). A finish is shown only when this page saw
 * the job active, so a reload or the snapshot after a reconnect never replays an old "Published".
 * Timers run whether or not the pill is visible, so it is right again when art returns after live/freeze.
 */
export function useScreenJobs(screenId: string): ScreenJobView {
  const { jobs, list } = useJobs(screenId);
  const seenActive = useRef(new Set<string>());
  const [flash, setFlash] = useState<{ kind: "published" | "failed"; job: Job } | null>(null);

  useEffect(() => {
    for (const job of Object.values(jobs)) {
      if (isActive(job)) {
        seenActive.current.add(job.id);
      } else if (seenActive.current.delete(job.id)) {
        if (job.status === "completed") setFlash({ kind: "published", job });
        else if (job.status === "failed") setFlash({ kind: "failed", job });
      }
    }
  }, [jobs]);

  useEffect(() => {
    if (!flash) return;
    const t = setTimeout(() => setFlash((f) => (f === flash ? null : f)), flash.kind === "failed" ? FAILED_MS : PUBLISHED_MS);
    return () => clearTimeout(t);
  }, [flash]);

  if (flash) return flash;
  const active = list.filter(isActive);
  return active.length ? { kind: "active", job: active[0], more: active.length - 1 } : null;
}
