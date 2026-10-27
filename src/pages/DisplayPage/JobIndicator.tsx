// src/pages/DisplayPage/JobIndicator.tsx
// The pill top-left while a generation job renders to this screen (docs/JOBS.md); the look of the old
// overlay_generating template, drawn by React from the job tracker instead of server HTML.
import React from "react";
import type { Job } from "@/shared/api/jobs";
import { useScreenJobs } from "./hooks/useScreenJobs";

const STATUS_TEXT: Record<string, string> = {
  preparing: "Preparing",
  queued: "Waiting for a GPU",
  running: "Rendering",
  publishing: "Publishing",
};

const KEYFRAMES =
  "@keyframes smJobPulse{0%,100%{opacity:1}50%{opacity:.6}}" +
  "@keyframes smJobIn{from{opacity:0}to{opacity:1}}";

const pillStyle = (alert: boolean): React.CSSProperties => ({
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
  background: alert ? "rgba(255, 0, 0, 0.3)" : "rgba(0, 0, 0, 0.3)",
  color: "rgba(255, 255, 255, 0.5)",
  fontWeight: 400,
  backdropFilter: "blur(6px)",
  WebkitBackdropFilter: "blur(6px)",
  borderRadius: 12,
  boxShadow: "0 10px 30px rgba(0, 0, 0, 0.2)",
  animation: "smJobIn 400ms ease",
});

function Ring({ percent }: { percent: number }) {
  const p = Math.max(0, Math.min(100, percent));
  return (
    <svg width="24" height="24" viewBox="0 0 36 36" style={{ flex: "none" }}>
      <circle cx="18" cy="18" r="16" fill="none" stroke="rgba(255, 255, 255, 0.3)" strokeWidth="4" />
      <circle
        cx="18" cy="18" r="16" fill="none" stroke="white" strokeWidth="4" pathLength={100}
        strokeDasharray="100" strokeDashoffset={100 - p} strokeLinecap="round" transform="rotate(-90 18 18)"
        style={{ transition: "stroke-dashoffset 400ms ease" }}
      />
    </svg>
  );
}

const Dot = () => (
  <div style={{ flex: "none", width: 14, height: 14, borderRadius: "50%", background: "red", animation: "smJobPulse 2s infinite ease-in-out" }} />
);

function activeText(job: Job): string {
  const text = job.message || (job.stage_index != null ? job.stages[job.stage_index] : null) || STATUS_TEXT[job.status] || job.status;
  return job.percent != null ? `${Math.round(job.percent)}% · ${text}` : text;
}

/** `hidden` (frozen or live) hides the pill at once but keeps the subscription; it takes no part in settle. */
export function JobIndicator({ screenId, hidden }: { screenId: string; hidden: boolean }) {
  const view = useScreenJobs(screenId);
  if (!view || hidden) return null;

  let body: React.ReactNode;
  if (view.kind === "active") {
    body = (
      <>
        {view.job.percent != null ? <Ring percent={view.job.percent} /> : <Dot />}
        <div>{activeText(view.job)}</div>
        {view.more > 0 && <div style={{ opacity: 0.8 }}>+{view.more}</div>}
      </>
    );
  } else if (view.kind === "published") {
    body = (
      <>
        <Ring percent={100} />
        <div>Published</div>
      </>
    );
  } else {
    body = <div>{view.job.error || view.job.message || "Generation failed"}</div>;
  }

  return (
    <>
      <style>{KEYFRAMES}</style>
      <div data-sm-job={view.job.id} style={pillStyle(view.kind === "failed")}>
        {body}
      </div>
    </>
  );
}
