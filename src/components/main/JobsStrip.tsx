// The generator's running jobs, in the page's flow under the prompt (no floating box over the pictures):
//   "Making 2 pictures for North screen · 60%"   Hide · Cancel all
// with a line per job underneath (its stage and progress). Hide only hides the strip (the jobs keep going); Cancel
// all stops every running generation (there is no per-job cancel route).

import { useState } from 'react';
import { Loader2, Square } from 'lucide-react';
import { toast } from 'sonner';
import { generationApi } from '@/shared/api';
import { cn } from '@/lib/utils';

export interface StripJob {
  id: string;
  /** The status text as the backend sent it (may hold HTML and a "<destination>: " prefix). */
  message?: string;
  progress?: number | null;
  /** Screens the job's status went to. */
  screens?: string[];
}

function plain(html: string): string {
  const d = document.createElement('div');
  d.innerHTML = html;
  return (d.textContent || d.innerText || html).replace(/\s+/g, ' ').trim();
}

export function JobsStrip({
  jobs,
  nameOf,
  onHide,
}: {
  jobs: StripJob[];
  /** A destination id → its name ("north-screen" → "North screen"); undefined when it isn't one. */
  nameOf: (id: string) => string | undefined;
  onHide: () => void;
}) {
  const [cancelling, setCancelling] = useState(false);
  if (jobs.length === 0) return null;

  const rows = jobs.map((j) => {
    let text = plain(j.message || '');
    let dest: string | undefined;
    const m = text.match(/^([^:]{1,64}):\s*(.*)$/);
    if (m && nameOf(m[1].trim())) {
      dest = nameOf(m[1].trim());
      text = m[2];
    }
    if (!dest) dest = (j.screens ?? []).map(nameOf).find(Boolean);
    text = text.replace(/^\d+%\s*-\s*/, '');
    const done = text === 'Done' || /^done\b/i.test(text);
    return { id: j.id, dest, text: done ? 'Done' : text || 'Working…', progress: typeof j.progress === 'number' ? j.progress : null, done };
  });

  const running = rows.filter((r) => !r.done);
  const dests = Array.from(new Set(running.map((r) => r.dest).filter(Boolean))) as string[];
  const pct = running.filter((r) => r.progress !== null);
  const avg = pct.length ? Math.round(pct.reduce((s, r) => s + (r.progress ?? 0), 0) / pct.length) : null;
  const n = running.length;
  const summary =
    n === 0
      ? `Finished ${rows.length === 1 ? 'a picture' : `${rows.length} pictures`}`
      : `Making ${n === 1 ? 'a picture' : `${n} pictures`}${dests.length ? ` for ${dests.join(' and ')}` : ''}${avg !== null ? ` · ${avg}%` : ''}`;

  const cancelAll = async () => {
    setCancelling(true);
    try {
      const r = await generationApi.cancelAllJobs();
      if (r.success) toast.success(r.cancelled ? `Cancelled ${r.cancelled === 1 ? 'a job' : `${r.cancelled} jobs`}` : 'Nothing was running');
      else toast.error(`Couldn't cancel: ${r.error ?? 'no answer'}`);
    } catch {
      toast.error("Couldn't cancel the jobs");
    } finally {
      setCancelling(false);
    }
  };

  return (
    <section
      aria-label="Running jobs"
      data-testid="jobs-strip"
      className="mb-4 rounded-2xl border border-teal-200 bg-teal-50/70 px-3 py-2 text-teal-950 shadow-sm sm:px-4"
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="flex min-w-0 flex-1 items-center gap-2 text-[15px] font-semibold">
          {n > 0 ? <Loader2 className="h-4 w-4 shrink-0 animate-spin" aria-hidden /> : <span aria-hidden className="h-2 w-2 shrink-0 rounded-full bg-teal-600" />}
          <span className="min-w-0">{summary}</span>
        </span>
        <span className="flex shrink-0 items-center gap-1">
          <button
            type="button"
            onClick={onHide}
            className="inline-flex h-9 items-center rounded-full px-3 text-[14px] font-medium hover:bg-teal-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            Hide
          </button>
          {n > 0 && (
            <button
              type="button"
              onClick={() => void cancelAll()}
              disabled={cancelling}
              data-testid="jobs-cancel-all"
              className="inline-flex h-9 items-center gap-1.5 rounded-full border border-teal-300 bg-white px-3 text-[14px] font-medium hover:bg-teal-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60"
            >
              {cancelling ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : <Square className="h-3 w-3 fill-current" aria-hidden />}
              Cancel all
            </button>
          )}
        </span>
      </div>
      {rows.length > 1 || (rows[0] && rows[0].text !== 'Done') ? (
        <ul className="mt-1 space-y-1 pb-0.5">
          {rows.map((r) => (
            <li key={r.id} className="flex items-center gap-2 text-[14px] leading-snug">
              <span className={cn('min-w-0 flex-1 truncate', r.done ? 'text-teal-800/70' : 'text-teal-900')}>
                {r.dest && rows.length > 1 ? <span className="font-medium">{r.dest} · </span> : null}
                {r.text}
              </span>
              {r.progress !== null && !r.done && (
                <span className="flex shrink-0 items-center gap-2">
                  <span className="hidden h-1.5 w-20 overflow-hidden rounded-full bg-teal-100 sm:block">
                    <span className="block h-full rounded-full bg-teal-600" style={{ width: `${Math.max(0, Math.min(100, r.progress))}%` }} />
                  </span>
                  <span className="font-mono text-[13px] tabular-nums">{r.progress}%</span>
                </span>
              )}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
