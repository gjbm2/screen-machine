// The generator's jobs dock, bottom left (docs/JOBS.md): every job being made right now, from any source, live
// from the job tracker; a finished one stays a moment (Done ~10 s with its result, Cancelled ~5 s) and a failed one
// stays, with its reason, until dismissed. Folded it's a small pill (owner, 27 Oct: "MUCH smaller ... only expand
// if user engages"): the lead job's ring and one word; a tap opens the list and "All jobs". Hidden when there's
// nothing to show.

import { useState } from "react";
import { ChevronDown } from "lucide-react";
import { AppLink } from "@/components/nav/AppLink";
import { jobsUrl } from "@/components/nav/appNav";
import { cn } from "@/lib/utils";
import { RoomThemeStyle, THEME_CLASS } from "@/pages/Room/theme";
import { isActive, type Job } from "@/shared/api/jobs";
import { useJobs } from "@/shared/jobs/store";
import { ProgressRing } from "./JobBits";
import { JobRow } from "./JobRow";
import { useNow } from "./jobWords";

const SHOW_DONE_S = 10;
const SHOW_CANCELLED_S = 5;

function shown(job: Job, now: number, dismissed: Set<string>): boolean {
  if (isActive(job)) return true;
  if (dismissed.has(job.id)) return false;
  const age = now - (job.finished_at ?? job.updated_at);
  if (job.status === "completed") return age < SHOW_DONE_S;
  if (job.status === "cancelled") return age < SHOW_CANCELLED_S;
  return true; // failed: until dismissed
}

export function JobsDock() {
  const { list, clockOffset } = useJobs();
  const [open, setOpen] = useState(false);
  const [dismissed, setDismissed] = useState<Set<string>>(() => new Set());
  // on the server's clock, like the jobs' times; re-rendered each second while a Done or Cancelled row is up
  const now = Date.now() / 1000 + clockOffset;
  const jobs = list.filter((j) => shown(j, now, dismissed));
  useNow(1000, jobs.some((j) => j.status === "completed" || j.status === "cancelled"));
  if (jobs.length === 0) return null;

  const active = jobs.filter(isActive);
  const lead = active.find((j) => j.percent !== null) ?? active[0]; // the one furthest along
  const dismiss = (id: string) => setDismissed((s) => new Set(s).add(id));

  const failed = jobs.filter((j) => j.status === "failed").length;
  const summary = lead
    ? lead.percent !== null
      ? `${Math.round(lead.percent)}%`
      : lead.status === "queued"
        ? "Queued"
        : lead.status === "preparing"
          ? "Preparing"
          : "Working"
    : failed
      ? "Failed"
      : jobs[0].status === "completed"
        ? "Done"
        : "Cancelled";
  const count = active.length > 1 ? active.length : 0;

  if (!open) {
    return (
      <div
        className={cn(THEME_CLASS, "fixed bottom-[calc(var(--dock-h,0px)_+_var(--app-nav-bottom,0px)_+_0.75rem)] left-3 z-50")}
        data-testid="jobs-dock"
      >
        <RoomThemeStyle />
        <button
          type="button"
          onClick={() => setOpen(true)}
          aria-expanded={false}
          aria-label={`Jobs: ${summary}${count ? `, ${count} running` : ""}. Show jobs`}
          className={cn(
            "flex h-9 items-center gap-1.5 rounded-full border bg-card pl-1.5 pr-3 text-[13px] font-medium text-card-foreground",
            "shadow-[0_1px_2px_rgba(68,48,28,0.06),0_6px_16px_-6px_rgba(68,48,28,0.3)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            failed && !lead ? "border-red-200 text-red-800" : "border-stone-200/80",
          )}
        >
          {lead ? (
            <ProgressRing percent={lead.percent} size={22} />
          ) : (
            <span aria-hidden className={cn("ml-1 h-2.5 w-2.5 rounded-full", failed ? "bg-red-500" : "bg-teal-600")} />
          )}
          <span className="tabular-nums">{summary}</span>
          {count > 0 && <span className="text-muted-foreground">· {count}</span>}
        </button>
      </div>
    );
  }

  return (
    <div
      className={cn(
        THEME_CLASS,
        "fixed bottom-[calc(var(--dock-h,0px)_+_var(--app-nav-bottom,0px)_+_0.75rem)] left-3 right-3 z-50 sm:right-auto sm:w-[24rem]",
      )}
      data-testid="jobs-dock"
    >
      <RoomThemeStyle />
      <section
        aria-label="Jobs"
        className="overflow-hidden rounded-2xl border border-stone-200/80 bg-card text-card-foreground shadow-[0_1px_2px_rgba(68,48,28,0.06),0_12px_32px_-8px_rgba(68,48,28,0.25)]"
      >
        <div className="flex items-center gap-1 pl-3 pr-1">
          <span className="flex-1 text-[15px] font-semibold">Jobs ({active.length || jobs.length})</span>
          <AppLink
            href={jobsUrl()}
            className="inline-flex h-10 shrink-0 items-center rounded-full px-3 text-sm font-medium text-teal-800 hover:bg-stone-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            All jobs
          </AppLink>
          <button
            type="button"
            onClick={() => setOpen(false)}
            aria-expanded
            aria-label="Hide jobs"
            className="grid h-10 w-10 place-items-center rounded-full text-muted-foreground hover:bg-stone-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <ChevronDown aria-hidden className="h-4 w-4" />
          </button>
        </div>
        <ul className="max-h-[min(45vh,22rem)] divide-y divide-stone-200/70 overflow-y-auto border-t border-stone-200/80">
          {jobs.map((job) => (
            <li key={job.id} className="py-1.5 pl-3 pr-1">
              <JobRow job={job} onDismiss={job.status === "failed" ? () => dismiss(job.id) : undefined} />
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
