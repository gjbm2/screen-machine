// /jobs: every generation job (docs/JOBS.md), from the generator, the schedules, Alexa or the API.
//
// The app's header (PageHeader: "‹ Generator" back, "Jobs", Mic and AI, the menu), then:
//   - Running now: live from the job tracker's stream (useJobs), each with its progress and Cancel; "Cancel all".
//   - History: finished jobs, newest first, a page at a time ("Load more"), filtered by how it ended, the screen,
//     who asked and words in the prompt. A job that finishes while the page is open joins the top of it live.
// A history row unrolls in place for the details (HistoryRow.tsx).

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { JobRow } from "@/components/jobs/JobRow";
import { HistoryRow } from "@/pages/Jobs/HistoryRow";
import { cancelEveryJob, SOURCE_WORDS, useNow } from "@/components/jobs/jobWords";
import { AppMenu } from "@/components/nav/AppMenu";
import { generatorUrl } from "@/components/nav/appNav";
import { PageHeader } from "@/components/nav/PageHeader";
import { usePublishDestinations } from "@/hooks/usePublishDestinations";
import { cn } from "@/lib/utils";
import { Notice, Spinner } from "@/pages/Room/bits";
import { CARD_CLASS, RoomThemeStyle, THEME_CLASS } from "@/pages/Room/theme";
import { isActive, listJobs, type Job, type JobSource, type JobStatus } from "@/shared/api/jobs";
import { useJobs } from "@/shared/jobs/store";

const PAGE = 30;
/** A filter the server doesn't do (how it ended) can leave a page nearly empty: read on, up to this many pages. */
const MAX_PAGES_PER_LOAD = 5;

type Ended = "all" | Extract<JobStatus, "completed" | "failed" | "cancelled">;
const ENDED: Array<{ value: Ended; label: string }> = [
  { value: "all", label: "All" },
  { value: "completed", label: "Done" },
  { value: "failed", label: "Failed" },
  { value: "cancelled", label: "Cancelled" },
];

interface Filters {
  ended: Ended;
  target: string;
  source: JobSource | "";
  q: string;
}

function matches(job: Job, f: Filters): boolean {
  if (f.ended !== "all" && job.status !== f.ended) return false;
  if (f.target && job.target !== f.target) return false;
  if (f.source && job.source !== f.source) return false;
  if (f.q && !job.prompt.toLowerCase().includes(f.q.toLowerCase())) return false;
  return true;
}

function usePageTitle(title: string) {
  useEffect(() => {
    const prev = document.title;
    document.title = title;
    return () => {
      document.title = prev;
    };
  }, [title]);
}

const SELECT =
  "h-11 min-w-0 rounded-xl border border-stone-200 bg-card px-3 text-[15px] text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

export default function JobsPage() {
  usePageTitle("Jobs · screen/machine");
  const [menuOpen, setMenuOpen] = useState(false);
  const { list, connected, clockOffset } = useJobs();
  const now = useNow(30_000) / 1000 + clockOffset;
  const { destinations } = usePublishDestinations();
  const screens = useMemo(
    () => destinations.filter((d) => d.id !== "_recent").sort((a, b) => (a.name || a.id).localeCompare(b.name || b.id)),
    [destinations],
  );

  const [ended, setEnded] = useState<Ended>("all");
  const [target, setTarget] = useState("");
  const [source, setSource] = useState<JobSource | "">("");
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setQ(search.trim()), 300);
    return () => clearTimeout(t);
  }, [search]);
  const filters = useMemo<Filters>(() => ({ ended, target, source, q }), [ended, target, source, q]);

  const [history, setHistory] = useState<Job[]>([]);
  const [nextBefore, setNextBefore] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const readId = useRef(0);

  /** Read the next page(s) of history: from the top (`before` null) or after what's shown. */
  const read = useCallback(
    async (before: number | null) => {
      const id = ++readId.current;
      setLoading(true);
      setError(null);
      const found: Job[] = [];
      let cursor = before;
      try {
        for (let n = 0; n < MAX_PAGES_PER_LOAD; n++) {
          const page = await listJobs({
            state: "finished",
            limit: PAGE,
            before: cursor ?? undefined,
            target: filters.target || undefined,
            source: filters.source || undefined,
            q: filters.q || undefined,
          });
          if (id !== readId.current) return;
          found.push(...page.jobs.filter((j) => matches(j, filters)));
          cursor = page.next_before;
          if (cursor === null || found.length >= PAGE / 2) break;
        }
        setHistory((h) => (before === null ? found : [...h, ...found]));
        setNextBefore(cursor);
      } catch (e) {
        if (id === readId.current) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (id === readId.current) setLoading(false);
      }
    },
    [filters],
  );

  useEffect(() => {
    void read(null);
  }, [read]);

  const running = list.filter(isActive);
  // the history, with jobs that finished while the page is open on top (the stream's copy is the newer)
  const shown = useMemo(() => {
    const byId = new Map<string, Job>();
    for (const j of history) if (matches(j, filters)) byId.set(j.id, j);
    for (const j of list) {
      if (isActive(j) || !matches(j, filters)) continue;
      const known = byId.get(j.id);
      if (!known || known.updated_at <= j.updated_at) byId.set(j.id, j);
    }
    return [...byId.values()].sort((a, b) => b.created_at - a.created_at);
  }, [history, list, filters]);
  const filtered = ended !== "all" || !!target || !!source || !!q;

  return (
    <div className={cn(THEME_CLASS, "min-h-screen bg-background text-base text-foreground antialiased")}>
      <RoomThemeStyle />
      <div className="mx-auto w-full min-w-0 max-w-3xl px-4 pb-28 sm:px-6">
        <PageHeader title="Jobs" current="jobs" onMenu={() => setMenuOpen(true)} toggles="auto" back={{ to: generatorUrl() }} compact />

        <section aria-labelledby="jobs-running" className="mt-3">
          <div className="mb-2 flex min-h-11 items-center justify-between gap-3">
            <h2 id="jobs-running" className="text-[17px] font-semibold">
              Running now{running.length > 0 && <span className="font-normal text-muted-foreground"> · {running.length}</span>}
            </h2>
            {running.length > 0 && (
              <Button type="button" variant="outline" onClick={() => void cancelEveryJob()} className="h-11 rounded-xl px-4 text-base">
                Cancel all
              </Button>
            )}
          </div>
          {running.length > 0 ? (
            <ul className={cn(CARD_CLASS, "divide-y divide-stone-200/70")} data-testid="jobs-running">
              {running.map((job) => (
                <li key={job.id} className="py-2 pl-4 pr-1">
                  <JobRow job={job} showSource />
                </li>
              ))}
            </ul>
          ) : (
            <p className="rounded-2xl border border-dashed border-stone-300 px-4 py-4 text-[15px] text-muted-foreground">
              {connected ? "Nothing is being made right now." : "Connecting to the live jobs…"}
            </p>
          )}
        </section>

        <section aria-labelledby="jobs-history" className="mt-8">
          <h2 id="jobs-history" className="mb-2 text-[17px] font-semibold">
            History
          </h2>

          <div className="space-y-2">
            <div role="radiogroup" aria-label="How it ended" className="flex w-full gap-1 overflow-x-auto rounded-full bg-stone-200/70 p-1 sm:w-auto sm:inline-flex">
              {ENDED.map((o) => (
                <button
                  key={o.value}
                  type="button"
                  role="radio"
                  aria-checked={ended === o.value}
                  onClick={() => setEnded(o.value)}
                  className={cn(
                    "h-9 flex-1 shrink-0 rounded-full px-3.5 text-[15px] font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:flex-none",
                    ended === o.value ? "bg-white text-teal-800 shadow-sm ring-1 ring-stone-200" : "text-stone-600 hover:text-stone-900",
                  )}
                >
                  {o.label}
                </button>
              ))}
            </div>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-[1fr_1fr_2fr]">
              <select aria-label="Screen" value={target} onChange={(e) => setTarget(e.target.value)} className={SELECT}>
                <option value="">All screens</option>
                {screens.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name || d.id}
                  </option>
                ))}
              </select>
              <select aria-label="Who asked" value={source} onChange={(e) => setSource(e.target.value as JobSource | "")} className={SELECT}>
                <option value="">Anyone</option>
                {(Object.keys(SOURCE_WORDS) as JobSource[]).map((s) => (
                  <option key={s} value={s}>
                    {SOURCE_WORDS[s]}
                  </option>
                ))}
              </select>
              <label className="relative col-span-2 sm:col-span-1">
                <span className="sr-only">Search the prompts</span>
                <Search aria-hidden className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <input
                  type="search"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Search the prompts"
                  className={cn(SELECT, "w-full pl-9")}
                />
              </label>
            </div>
          </div>

          {error && (
            <Notice tone="warn" className="mt-3">
              The jobs can't be read just now ({error}).{" "}
              <button type="button" className="font-medium underline" onClick={() => void read(history.length ? nextBefore : null)}>
                Try again
              </button>
            </Notice>
          )}

          {shown.length > 0 ? (
            <ul className="mt-3 divide-y divide-stone-200/80 border-y border-stone-200/80" data-testid="jobs-history">
              {shown.map((job) => (
                <HistoryRow key={job.id} job={job} now={now} />
              ))}
            </ul>
          ) : (
            !loading &&
            !error && <p className="py-8 text-center text-[15px] text-muted-foreground">{filtered ? "No jobs match." : "No finished jobs yet."}</p>
          )}

          <div className="mt-4 flex justify-center">
            {loading ? (
              <span className="inline-flex items-center gap-2 py-3 text-[15px] text-muted-foreground">
                <Spinner /> Reading the jobs…
              </span>
            ) : (
              nextBefore !== null && (
                <Button type="button" variant="outline" onClick={() => void read(nextBefore)} className="h-11 rounded-xl px-5 text-base">
                  Load more
                </Button>
              )
            )}
          </div>
        </section>
      </div>

      <AppMenu open={menuOpen} onOpenChange={setMenuOpen} current="jobs" />
    </div>
  );
}
