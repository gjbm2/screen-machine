// A placeholder tile for an image still being made (the generator's Recent view): that image's own job, from the
// shared job stream (docs/JOBS.md), as a progress ring and the friendly stage words. The job is found by the
// placeholder id the page sent with the request (job.placeholder_id), else by its batch and index. Until the job
// shows up, the old spinner. Large (the selected view) adds the stage and the time left under the ring; small (a
// thumbnail, a few dozen pixels) is the ring alone, with the words as its tooltip.

import { useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { Ban, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { isActive, type Job } from "@/shared/api/jobs";
import { useJobs } from "@/shared/jobs/store";
import { useJobProgress } from "@/shared/jobs/estimate";
import { remainingLabel, stageLabel, STAGE_WORDS } from "@/shared/jobs/words";
import type { ImageItem } from "@/types/image-types";
import { PlaceholderImage } from "@/components/recent/PlaceholderImage";
import { ProgressRing } from "./JobBits";

/** A job made this long (s) before its placeholder still counts for the batch-and-index match (clock slack). */
const SINCE_SLACK_S = 10;

export interface PlaceholderKey {
  placeholderId: string;
  batchId?: string;
  batchIndex?: number;
  /** When the placeholder was made (epoch s, this device's clock); guards the batch-and-index match. */
  since?: number;
}

/** Of two jobs for one placeholder (one per screen it goes to), the one still running, else the newest. */
function better(a: Job | null, b: Job): Job {
  if (!a) return b;
  if (isActive(a) !== isActive(b)) return isActive(b) ? b : a;
  return b.created_at > a.created_at ? b : a;
}

/** The job behind a placeholder: by placeholder_id, else by batch_id + batch_index (made after the placeholder). */
export function findPlaceholderJob(jobs: Record<string, Job>, key: PlaceholderKey, clockOffset: number): Job | null {
  let byId: Job | null = null;
  let byIndex: Job | null = null;
  const after = key.since != null ? key.since + clockOffset - SINCE_SLACK_S : null;
  for (const job of Object.values(jobs)) {
    if (job.placeholder_id && job.placeholder_id === key.placeholderId) {
      byId = better(byId, job);
    } else if (
      !byId &&
      key.batchId &&
      key.batchIndex != null &&
      job.batch_id === key.batchId &&
      job.batch_index === key.batchIndex &&
      (after === null || job.created_at >= after)
    ) {
      byIndex = better(byIndex, job);
    }
  }
  return byId ?? byIndex;
}

/** True while the element is on screen (true where IntersectionObserver isn't available). */
function useOnScreen<T extends Element>(): [RefObject<T>, boolean] {
  const ref = useRef<T>(null);
  const [visible, setVisible] = useState(true);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver((entries) => setVisible(entries.some((e) => e.isIntersecting)));
    io.observe(el);
    return () => io.disconnect();
  }, []);
  return [ref, visible];
}

export function JobPlaceholder({
  placeholder,
  aspectRatio = 16 / 9,
  large = false,
  className,
}: {
  placeholder: PlaceholderKey;
  aspectRatio?: number;
  large?: boolean;
  className?: string;
}) {
  const { jobs, clockOffset } = useJobs();
  const { placeholderId, batchId, batchIndex, since } = placeholder;
  const job = useMemo(
    () => findPlaceholderJob(jobs, { placeholderId, batchId, batchIndex, since }, clockOffset),
    [jobs, placeholderId, batchId, batchIndex, since, clockOffset],
  );
  const [ref, visible] = useOnScreen<HTMLDivElement>();
  const progress = useJobProgress(job, clockOffset, visible);

  if (!job) return <PlaceholderImage aspectRatio={aspectRatio} className={className} />;

  const stopped = job.status === "failed" || job.status === "cancelled";
  const stage = stageLabel(job, progress.phase);
  const time = isActive(job) && !job.cancel_requested ? remainingLabel(progress.remainingS, progress.overrunS) : "";
  const words = time ? `${stage} · ${time}` : stage;
  const StopIcon = job.status === "failed" ? X : Ban;

  // Inline positioning, not Tailwind's .absolute: the Recent thumbnail strip hides every .absolute child on hover.
  return (
    <div ref={ref} className={cn("relative w-full", className)} style={{ paddingBottom: `${(1 / aspectRatio) * 100}%` }} title={words}>
      <div
        className="flex flex-col items-center justify-center gap-1 overflow-hidden rounded bg-gray-100 px-1 text-center dark:bg-gray-800"
        style={{ position: "absolute", inset: 0 }}
        data-job-placeholder
        aria-label={words}
      >
        {stopped ? (
          <span
            aria-hidden
            className={cn(
              "grid shrink-0 place-items-center rounded-full",
              large ? "h-10 w-10" : "h-[60%] max-h-8 min-h-3 aspect-square",
              job.status === "failed" ? "bg-rose-50 text-rose-600" : "bg-stone-200 text-stone-500",
            )}
          >
            <StopIcon className={large ? "h-5 w-5" : "h-[60%] w-[60%]"} />
          </span>
        ) : large ? (
          <ProgressRing fraction={progress.fraction} waiting={progress.phase === "waiting"} size={48} />
        ) : (
          <span className="block h-[62%] max-h-8 min-h-3 aspect-square">
            <ProgressRing fraction={progress.fraction} waiting={progress.phase === "waiting"} className="!h-full !w-full" />
          </span>
        )}
        {large && (
          <>
            <span className="max-w-full truncate text-sm font-medium text-gray-700 dark:text-gray-200">
              {job.status === "failed" ? STAGE_WORDS.failed : stage}
            </span>
            {time && <span className="max-w-full truncate text-xs text-gray-500 dark:text-gray-400">{time}</span>}
          </>
        )}
      </div>
    </div>
  );
}

/** The placeholder key a Recent placeholder item carries (RecentView puts batchId/batchIndex in its metadata). */
export function placeholderKeyOf(image: Pick<ImageItem, "id" | "createdAt" | "metadata">): PlaceholderKey {
  const meta = image.metadata ?? {};
  const created = Date.parse(image.createdAt);
  return {
    placeholderId: image.id,
    batchId: typeof meta.batchId === "string" ? meta.batchId : undefined,
    batchIndex: typeof meta.batchIndex === "number" ? meta.batchIndex : undefined,
    since: Number.isFinite(created) ? created / 1000 : undefined,
  };
}
