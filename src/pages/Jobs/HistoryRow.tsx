// One finished job on the Jobs page: its result, prompt, screen, how it went, when, how long and what it cost.
// A tap unrolls the details (the refined prompt, the error, the seed, the ids, a link to the result).

import { useState, type ReactNode } from "react";
import { ChevronDown, ExternalLink } from "lucide-react";
import { JobThumb, StatusChip } from "@/components/jobs/JobBits";
import { formatCost, formatDuration, relativeTime, screenName, SOURCE_WORDS } from "@/components/jobs/jobWords";
import { cn } from "@/lib/utils";
import type { Job } from "@/shared/api/jobs";

function Detail({ label, children, mono = false }: { label: string; children: ReactNode; mono?: boolean }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className={cn("min-w-0 break-words", mono && "font-mono text-[13px]")}>{children}</dd>
    </>
  );
}

function clockTime(t: number | null): string | null {
  return t ? new Date(t * 1000).toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" }) : null;
}

export function HistoryRow({ job, now }: { job: Job; now: number }) {
  const [open, setOpen] = useState(false);
  const duration = formatDuration(job.duration_s);
  const cost = formatCost(job.cost_gbp);
  const meta = [screenName(job), SOURCE_WORDS[job.source] ?? job.source, job.workflow].filter(Boolean).join(" · ");
  const facts = [relativeTime(job.finished_at ?? job.created_at, now), duration, cost].filter(Boolean).join(" · ");
  return (
    <li data-testid="job-history-row">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full min-w-0 items-start gap-3 rounded-xl px-1 py-3 text-left hover:bg-stone-100/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <JobThumb job={job} className="h-16 w-16 sm:h-20 sm:w-20" />
        <span className="min-w-0 flex-1">
          <span className="line-clamp-2 text-[15px] leading-snug text-foreground">{job.prompt || "No prompt"}</span>
          <span className="mt-0.5 block truncate text-sm text-muted-foreground">{meta}</span>
          <span className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1">
            <StatusChip job={job} />
            <span className="text-sm tabular-nums text-muted-foreground">{facts}</span>
          </span>
          {job.status === "failed" && job.error && !open && <span className="mt-1 block truncate text-sm text-rose-700">{job.error}</span>}
        </span>
        <ChevronDown aria-hidden className={cn("mt-1 h-4 w-4 shrink-0 text-muted-foreground transition-transform motion-reduce:transition-none", open && "rotate-180")} />
      </button>
      {open && (
        <div className="pb-4 pl-1 pr-1 sm:pl-[5.75rem]">
          {job.result_url && job.kind === "video" && (
            <video src={job.result_url} controls muted playsInline preload="metadata" className="mb-3 max-h-72 w-full rounded-xl bg-black object-contain" />
          )}
          {job.result_url && job.kind === "image" && (
            <img src={job.result_url} alt="" loading="lazy" className="mb-3 max-h-72 w-auto max-w-full rounded-xl object-contain" />
          )}
          <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-sm">
            {job.error && <Detail label="Why it failed">{<span className="text-rose-700">{job.error}</span>}</Detail>}
            {job.refined_prompt && job.refined_prompt !== job.prompt && <Detail label="Prompt sent">{job.refined_prompt}</Detail>}
            {job.refiner && <Detail label="Refiner">{job.refiner}</Detail>}
            {job.workflow && <Detail label="Workflow">{job.workflow}</Detail>}
            {job.seed !== null && <Detail label="Seed" mono>{job.seed}</Detail>}
            <Detail label="Asked">{clockTime(job.created_at)}</Detail>
            {job.finished_at && <Detail label="Finished">{clockTime(job.finished_at)}</Detail>}
            {duration && <Detail label="Took">{duration}</Detail>}
            {cost && <Detail label="GPU cost">{cost}</Detail>}
            {job.published_path && <Detail label="Published to" mono>{job.published_path}</Detail>}
            <Detail label="Job" mono>{job.id}</Detail>
            {job.batch_id && <Detail label="Batch" mono>{job.batch_id}</Detail>}
            {job.runpod_job_id && <Detail label="RunPod job" mono>{job.runpod_job_id}</Detail>}
          </dl>
          {job.result_url && (
            <a
              href={job.result_url}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-3 inline-flex h-11 items-center gap-1.5 rounded-xl border border-stone-200 bg-card px-4 text-[15px] font-medium text-teal-800 hover:bg-stone-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              Open the {job.kind === "video" ? "video" : "image"} <ExternalLink aria-hidden className="h-4 w-4" />
            </a>
          )}
        </div>
      )}
    </li>
  );
}
