// One live job (the generator's jobs dock, the Jobs page's "Running now"): the screen it's for, its prompt, what
// it's doing in plain words and its progress ring, with Cancel while it runs. A finished one shows its result
// (Done, with a thumbnail and a link), its error (Failed, dismissable) or that it was cancelled.

import { Ban, ExternalLink, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { isActive, type Job } from "@/shared/api/jobs";
import { JobThumb, ProgressRing } from "./JobBits";
import { cancelOneJob, jobLine, screenName, SOURCE_WORDS } from "./jobWords";

const ICON_BUTTON =
  "grid h-11 w-11 shrink-0 place-items-center rounded-full text-stone-500 hover:bg-stone-100 hover:text-stone-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50";

export function JobRow({ job, onDismiss, showSource = false, className }: { job: Job; onDismiss?: () => void; showSource?: boolean; className?: string }) {
  const active = isActive(job);
  const done = job.status === "completed";
  const failed = job.status === "failed";
  return (
    <div className={cn("flex min-w-0 items-center gap-3", className)} data-testid="job-row">
      {active ? (
        <ProgressRing percent={job.percent} />
      ) : done ? (
        <JobThumb job={job} className="h-10 w-10" />
      ) : (
        <span aria-hidden className={cn("grid h-8 w-8 shrink-0 place-items-center rounded-full", failed ? "bg-rose-50 text-rose-600" : "bg-stone-100 text-stone-500")}>
          {failed ? <X className="h-4 w-4" /> : <Ban className="h-4 w-4" />}
        </span>
      )}
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-baseline gap-2">
          <span className="truncate text-[15px] font-semibold text-foreground">{screenName(job)}</span>
          {showSource && <span className="shrink-0 text-xs text-muted-foreground">{SOURCE_WORDS[job.source] ?? job.source}</span>}
        </div>
        <p className="truncate text-sm text-muted-foreground" title={job.prompt}>
          {job.prompt || "No prompt"}
        </p>
        <p className={cn("text-sm", failed ? "line-clamp-2 text-rose-700" : "truncate text-stone-700")}>
          {done ? "Done" : jobLine(job)}
          {active && job.percent !== null && <span className="tabular-nums text-muted-foreground"> · {Math.round(job.percent)}%</span>}
        </p>
      </div>
      {active && (
        <button
          type="button"
          className={ICON_BUTTON}
          onClick={() => void cancelOneJob(job)}
          disabled={job.cancel_requested}
          aria-label={`Cancel the job for ${screenName(job)}`}
          title="Cancel"
        >
          <X className="h-5 w-5" />
        </button>
      )}
      {done && job.result_url && (
        <a
          href={job.result_url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex h-11 shrink-0 items-center gap-1 rounded-full px-3 text-sm font-medium text-teal-800 hover:bg-stone-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          Open <ExternalLink aria-hidden className="h-3.5 w-3.5" />
        </a>
      )}
      {!active && onDismiss && (
        <button type="button" className={ICON_BUTTON} onClick={onDismiss} aria-label="Dismiss" title="Dismiss">
          <X className="h-5 w-5" />
        </button>
      )}
    </div>
  );
}
