// The generator's jobs dock, bottom left (docs/JOBS.md): every job being made right now, from any source, live
// from the job tracker; a finished one stays a moment (Done ~10 s with its result, Cancelled ~5 s) and a failed one
// stays, with its reason, until dismissed. "Jobs (N)" folds it; "All jobs" goes to the Jobs page. Hidden when
// there's nothing to show.

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
  const [open, setOpen] = useState(true);
  const [dismissed, setDismissed] = useState<Set<string>>(() => new Set());
  // on the server's clock, like the jobs' times; re-rendered each second while a Done or Cancelled row is up
  const now = Date.now() / 1000 + clockOffset;
  const jobs = list.filter((j) => shown(j, now, dismissed));
  useNow(1000, jobs.some((j) => j.status === "completed" || j.status === "cancelled"));
  if (jobs.length === 0) return null;

  const active = jobs.filter(isActive);
  const lead = active[0];
  const dismiss = (id: string) => setDismissed((s) => new Set(s).add(id));

  return (
    <div
      className={cn(
        THEME_CLASS,
        "fixed bottom-[calc(var(--dock-h,0px)_+_var(--app-nav-bottom,0px)_+_1rem)] left-4 right-4 z-50 sm:right-auto sm:w-[24rem]",
      )}
      data-testid="jobs-dock"
    >
      <RoomThemeStyle />
      <section
        aria-label="Jobs"
        className="overflow-hidden rounded-2xl border border-stone-200/80 bg-card text-card-foreground shadow-[0_1px_2px_rgba(68,48,28,0.06),0_12px_32px_-8px_rgba(68,48,28,0.25)]"
      >
        <div className="flex items-center gap-1 pl-1 pr-2">
          <button
            type="button"
            onClick={() => setOpen((o) => !o)}
            aria-expanded={open}
            className="flex min-h-12 min-w-0 flex-1 items-center gap-2 rounded-xl px-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {!open && lead && <ProgressRing percent={lead.percent} size={24} />}
            <span className="text-[15px] font-semibold">Jobs ({active.length || jobs.length})</span>
            <ChevronDown aria-hidden className={cn("h-4 w-4 text-muted-foreground transition-transform motion-reduce:transition-none", !open && "rotate-180")} />
          </button>
          <AppLink
            href={jobsUrl()}
            className="inline-flex h-11 shrink-0 items-center rounded-full px-3 text-sm font-medium text-teal-800 hover:bg-stone-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            All jobs
          </AppLink>
        </div>
        {open && (
          <ul className="max-h-[min(50vh,24rem)] divide-y divide-stone-200/70 overflow-y-auto border-t border-stone-200/80">
            {jobs.map((job) => (
              <li key={job.id} className="py-1.5 pl-3 pr-1">
                <JobRow job={job} onDismiss={job.status === "failed" ? () => dismiss(job.id) : undefined} />
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
