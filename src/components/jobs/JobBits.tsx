// Small pieces of a job's row: its progress ring, status chip and result thumbnail (the jobs dock and the Jobs page).

import { useState } from "react";
import { Film, ImageIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { Pill } from "@/pages/Room/bits";
import type { Job } from "@/shared/api/jobs";
import { STATUS_TONES, STATUS_WORDS } from "./jobWords";

/** A ring that fills with the job's percent; a pulsing dot while the percent isn't known. */
export function ProgressRing({ percent, size = 32, className }: { percent: number | null; size?: number; className?: string }) {
  const r = 15;
  const c = 2 * Math.PI * r;
  const p = percent === null ? null : Math.min(100, Math.max(0, percent));
  return (
    <span
      className={cn("relative inline-grid shrink-0 place-items-center", className)}
      style={{ width: size, height: size }}
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={p ?? undefined}
      aria-label={p === null ? "Working" : `${Math.round(p)}%`}
    >
      <svg viewBox="0 0 36 36" className="absolute inset-0 h-full w-full -rotate-90" aria-hidden>
        <circle cx="18" cy="18" r={r} fill="none" strokeWidth="4" className="stroke-stone-200" />
        {p !== null && (
          <circle
            cx="18"
            cy="18"
            r={r}
            fill="none"
            strokeWidth="4"
            strokeLinecap="round"
            strokeDasharray={c}
            strokeDashoffset={c * (1 - p / 100)}
            className="stroke-teal-600 transition-[stroke-dashoffset] duration-500 motion-reduce:transition-none"
          />
        )}
      </svg>
      {p === null ? (
        <span aria-hidden className="h-2 w-2 rounded-full bg-teal-600 motion-safe:animate-pulse" />
      ) : (
        size >= 32 && <span className="relative text-[10px] font-semibold tabular-nums text-stone-700">{Math.round(p)}</span>
      )}
    </span>
  );
}

export function StatusChip({ job, className }: { job: Pick<Job, "status">; className?: string }) {
  const active = job.status === "running" || job.status === "publishing";
  return (
    <Pill tone={STATUS_TONES[job.status]} pulse={active} className={className}>
      {STATUS_WORDS[job.status]}
    </Pill>
  );
}

/** The result: the image, a video's first frame, or an icon when there's nothing to show yet. */
export function JobThumb({ job, className }: { job: Pick<Job, "kind" | "result_url" | "status">; className?: string }) {
  const [broken, setBroken] = useState(false);
  const box = cn("grid shrink-0 place-items-center overflow-hidden rounded-lg bg-stone-100 text-stone-400", className);
  if (!job.result_url || broken) {
    const Icon = job.kind === "video" ? Film : ImageIcon;
    return (
      <span className={box} aria-hidden>
        <Icon className="h-5 w-5" />
      </span>
    );
  }
  if (job.kind === "video") {
    return (
      <span className={box}>
        {/* #t=0.1 asks for the first frame as the poster */}
        <video
          src={`${job.result_url}#t=0.1`}
          preload="metadata"
          muted
          playsInline
          className="h-full w-full object-cover"
          onError={() => setBroken(true)}
          aria-label="The video"
        />
      </span>
    );
  }
  return (
    <span className={box}>
      <img src={job.result_url} alt="" loading="lazy" decoding="async" className="h-full w-full object-cover" onError={() => setBroken(true)} />
    </span>
  );
}
