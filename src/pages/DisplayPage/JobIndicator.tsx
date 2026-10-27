// src/pages/DisplayPage/JobIndicator.tsx
// The pill top-left while a generation job renders to this screen (docs/JOBS.md); the look of the old
// overlay_generating template, drawn by React from the job tracker instead of server HTML. The ring follows the
// learned time for this workflow (src/shared/jobs/estimate.ts), the word is src/shared/jobs/words.ts.
import React from "react";
import { useJobProgress } from "@/shared/jobs/estimate";
import { useJobs } from "@/shared/jobs/store";
import { STAGE_WORDS, stageLabel } from "@/shared/jobs/words";
import { useScreenJobs } from "./hooks/useScreenJobs";

// The ring eases between the ~500 ms ticks; while a GPU is getting ready its small held slice breathes slowly.
const KEYFRAMES =
  "@keyframes smJobBreathe{0%,100%{opacity:1}50%{opacity:.45}}" +
  "@keyframes smJobIn{from{opacity:0}to{opacity:1}}";

const pillStyle: React.CSSProperties = {
  position: "absolute",
  top: 20,
  left: 20,
  zIndex: 10000,
  pointerEvents: "none",
  display: "inline-flex",
  alignItems: "center",
  gap: 12,
  maxWidth: "calc(100% - 40px)",
  fontSize: "clamp(12px, 1.5vw, 16px)",
  padding: "12px 14px",
  background: "rgba(0, 0, 0, 0.3)",
  color: "rgba(255, 255, 255, 0.5)",
  fontWeight: 400,
  backdropFilter: "blur(6px)",
  WebkitBackdropFilter: "blur(6px)",
  borderRadius: 12,
  boxShadow: "0 10px 30px rgba(0, 0, 0, 0.2)",
  animation: "smJobIn 400ms ease",
};

function Ring({ fraction, waiting = false }: { fraction: number; waiting?: boolean }) {
  const p = Math.max(0, Math.min(1, Number.isFinite(fraction) ? fraction : 0)) * 100;
  return (
    <svg width="24" height="24" viewBox="0 0 36 36" style={{ flex: "none" }}>
      <circle cx="18" cy="18" r="16" fill="none" stroke="rgba(255, 255, 255, 0.3)" strokeWidth="4" />
      <circle
        cx="18" cy="18" r="16" fill="none" stroke="white" strokeWidth="4" pathLength={100}
        strokeDasharray="100" strokeDashoffset={100 - p} strokeLinecap="round" transform="rotate(-90 18 18)"
        style={{
          transition: "stroke-dashoffset 600ms linear",
          animation: waiting ? "smJobBreathe 3s ease-in-out infinite" : undefined,
        }}
      />
    </svg>
  );
}

/**
 * `hidden` (frozen or live) hides the pill at once but keeps the subscription; it takes no part in settle.
 * A guest sees one plain stage word beside the ring (owner, 27 Oct): no numbers, no technical words, no raw errors.
 */
export function JobIndicator({ screenId, hidden }: { screenId: string; hidden: boolean }) {
  const view = useScreenJobs(screenId);
  const { clockOffset } = useJobs(screenId);
  const activeJob = view?.kind === "active" ? view.job : null;
  // one small interval, only while a job is active and the pill is showing
  const progress = useJobProgress(activeJob, clockOffset, !hidden);
  if (!view || hidden) return null;

  let body: React.ReactNode;
  if (view.kind === "active") {
    body = (
      <>
        <Ring fraction={progress.fraction} waiting={progress.phase === "waiting"} />
        <div>{stageLabel(view.job, progress.phase)}</div>
      </>
    );
  } else if (view.kind === "published") {
    body = (
      <>
        <Ring fraction={1} />
        <div>{STAGE_WORDS.published}</div>
      </>
    );
  } else {
    body = <div>{STAGE_WORDS.failed}</div>;
  }

  return (
    <>
      <style>{KEYFRAMES}</style>
      <div data-sm-job={view.job.id} style={pillStyle}>
        {body}
      </div>
    </>
  );
}
