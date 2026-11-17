// What generation cost, on the Jobs page: RunPod's own bill for the period, the cost per image of each workflow,
// and the spend per day and per who asked. Costs are each job's share of RunPod's hourly bill (routes/jobs/billing.py),
// so the jobs add up to the bill; anything RunPod billed that no job used (tests, an idle start) is shown apart.

import { useEffect, useState } from "react";
import { ChevronDown } from "lucide-react";
import { formatCost, SOURCE_WORDS } from "@/components/jobs/jobWords";
import { cn } from "@/lib/utils";
import { CARD_CLASS } from "@/pages/Room/theme";
import { getJobCosts, type CostGroup, type JobCosts, type JobSource } from "@/shared/api/jobs";

const PERIODS = [7, 30, 90];

function money(gbp: number): string {
  return `£${gbp.toFixed(2)}`;
}

function workflowName(id: string): string {
  return id.replace(/\.json$/, "");
}

function Groups({ title, rows, name }: { title: string; rows: CostGroup[]; name: (key: string) => string }) {
  if (!rows.length) return null;
  return (
    <div className="mt-4">
      <h3 className="mb-1 text-[15px] font-semibold">{title}</h3>
      <table className="w-full text-[14px]">
        <thead className="text-left text-muted-foreground">
          <tr>
            <th className="py-1 font-normal"></th>
            <th className="py-1 text-right font-normal">Images</th>
            <th className="py-1 text-right font-normal">Each</th>
            <th className="py-1 text-right font-normal">Total</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-stone-200/70">
          {rows.map((r) => (
            <tr key={r.key}>
              <td className="max-w-0 truncate py-1.5 pr-2">{name(r.key)}</td>
              <td className="py-1.5 text-right tabular-nums">{r.images}</td>
              <td className="py-1.5 text-right tabular-nums" title={`mean ${formatCost(r.mean_gbp)}`}>
                {formatCost(r.per_image_gbp)}
              </td>
              <td className="py-1.5 text-right tabular-nums">{money(r.total_gbp)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Costs() {
  const [days, setDays] = useState(30);
  const [open, setOpen] = useState(false);
  const [costs, setCosts] = useState<JobCosts | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setError(null);
    getJobCosts(days)
      .then((c) => live && setCosts(c))
      .catch((e) => live && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      live = false;
    };
  }, [days]);

  const perImage = costs && costs.images ? costs.jobs_gbp / costs.images : null;
  const maxDay = Math.max(0.01, ...(costs?.by_day ?? []).map((d) => Math.max(d.billed_gbp, d.jobs_gbp)));
  const pending = costs ? costs.images - costs.reconciled : 0;

  return (
    <section aria-labelledby="jobs-costs" className="mt-8">
      <div className="mb-2 flex min-h-11 items-center justify-between gap-3">
        <h2 id="jobs-costs" className="text-[17px] font-semibold">
          Cost
        </h2>
        <div role="radiogroup" aria-label="Period" className="inline-flex gap-1 rounded-full bg-stone-200/70 p-1">
          {PERIODS.map((p) => (
            <button
              key={p}
              type="button"
              role="radio"
              aria-checked={days === p}
              onClick={() => setDays(p)}
              className={cn(
                "h-9 rounded-full px-3.5 text-[15px] font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                days === p ? "bg-white text-teal-800 shadow-sm ring-1 ring-stone-200" : "text-stone-600 hover:text-stone-900",
              )}
            >
              {p} days
            </button>
          ))}
        </div>
      </div>

      <div className={cn(CARD_CLASS, "px-4 py-3")} data-testid="jobs-costs">
        {error && <p className="text-[15px] text-muted-foreground">The costs can't be read just now ({error}).</p>}
        {!error && !costs && <p className="text-[15px] text-muted-foreground">Reading the costs…</p>}
        {costs && (
          <>
            <button
              type="button"
              onClick={() => setOpen((o) => !o)}
              aria-expanded={open}
              className="flex w-full items-start justify-between gap-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <span>
                <span className="block text-[22px] font-semibold tabular-nums">{money(costs.billed_gbp)}</span>
                <span className="block text-[15px] text-muted-foreground">
                  billed by RunPod · {costs.images} {costs.images === 1 ? "image" : "images"}
                  {perImage !== null && ` · ${formatCost(perImage)} each on average`}
                </span>
              </span>
              <ChevronDown aria-hidden className={cn("mt-2 h-5 w-5 shrink-0 text-muted-foreground transition-transform", open && "rotate-180")} />
            </button>

            {open && (
              <div className="mt-2">
                {costs.unattributed_gbp >= 0.05 && (
                  <p className="text-[14px] text-muted-foreground">
                    {money(costs.unattributed_gbp)} of it was GPU time no job used (tests, or a worker that started for nothing).
                  </p>
                )}
                {pending > 0 && (
                  <p className="text-[14px] text-muted-foreground">
                    {pending} recent {pending === 1 ? "job is" : "jobs are"} costed from {pending === 1 ? "its" : "their"} own timings until RunPod's bill
                    for the hour arrives.
                  </p>
                )}

                <div className="mt-3" aria-label="Per day">
                  <h3 className="mb-1 text-[15px] font-semibold">Per day</h3>
                  <ul className="space-y-1">
                    {costs.by_day.slice(-days).map((d) => (
                      <li key={d.day} className="grid grid-cols-[4.5rem_1fr_3.5rem] items-center gap-2 text-[13px]">
                        <span className="text-muted-foreground">
                          {new Date(d.day + "T12:00:00").toLocaleDateString([], { day: "numeric", month: "short" })}
                        </span>
                        <span className="h-2.5 rounded-full bg-stone-200/70">
                          <span className="block h-2.5 rounded-full bg-teal-700/70" style={{ width: `${(Math.max(d.billed_gbp, d.jobs_gbp) / maxDay) * 100}%` }} />
                        </span>
                        <span className="text-right tabular-nums">{money(Math.max(d.billed_gbp, d.jobs_gbp))}</span>
                      </li>
                    ))}
                  </ul>
                </div>

                <Groups title="Per workflow" rows={costs.by_workflow} name={workflowName} />
                <Groups title="Who asked" rows={costs.by_source} name={(k) => SOURCE_WORDS[k as JobSource] ?? k} />
              </div>
            )}
          </>
        )}
      </div>
    </section>
  );
}
