// A screen's day played back here only (owner, 25 Nov: "a way to review the progressions etc. in the webapp without
// triggering publishing events"): each picture held a few seconds, then a short fade into the transition to the next,
// in the order they were made, as on the screen. Nothing is sent to a screen.
import { useEffect, useMemo, useRef, useState } from "react";
import { Pause, Play, SkipBack, SkipForward, X } from "lucide-react";
import type { Transition } from "@/shared/api/transitions";

const HOLD_S = 5;
const FADE_MS = 400;

type Step = { kind: "still"; url: string } | { kind: "video"; url: string; t: Transition; of: number };

function stepsOf(list: Transition[]): Step[] {
  const out: Step[] = [];
  let last: string | undefined;
  list.forEach((t, k) => {
    if (t.from_url && t.from_url !== last) out.push({ kind: "still", url: t.from_url });
    out.push({ kind: "video", url: t.url, t, of: k + 1 });
    if (t.to_url) out.push({ kind: "still", url: t.to_url });
    last = t.to_url;
  });
  return out;
}

export function RunPlayer({ list, title, onClose }: { list: Transition[]; title: string; onClose: () => void }) {
  const steps = useMemo(() => stepsOf(list), [list]);
  const [n, setN] = useState(0);
  const [paused, setPaused] = useState(false);
  const [base, setBase] = useState<string | undefined>(steps.find((s) => s.kind === "still")?.url);
  const [video, setVideo] = useState<string | null>(null);
  const [videoOn, setVideoOn] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);
  const step = steps[n];
  const next = () => setN((k) => Math.min(k + 1, steps.length));
  const jump = (dir: 1 | -1) => {
    // to the next (or previous) transition, from the picture before it
    let k = n + dir;
    while (k >= 0 && k < steps.length && steps[k].kind !== "video") k += dir;
    if (k < 0 || k >= steps.length) return;
    const before = steps[k - 1];
    setBase(before?.kind === "still" ? before.url : base);
    setN(k);
  };

  useEffect(() => {
    if (!step) return;
    if (step.kind === "still") {
      setBase(step.url);
      setVideoOn(false);
      const gone = setTimeout(() => setVideo(null), FADE_MS);
      const hold = paused ? null : setTimeout(next, HOLD_S * 1000);
      return () => {
        clearTimeout(gone);
        if (hold) clearTimeout(hold);
      };
    }
    setVideo(step.url);
    setVideoOn(false);
  }, [n, paused]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const v = videoRef.current;
    if (!v || step?.kind !== "video") return;
    if (paused) v.pause();
    else void v.play().catch(() => undefined);
  }, [paused, video, step]);

  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === " ") setPaused((p) => !p);
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [onClose]);

  const shown = step?.kind === "video" ? step : [...steps.slice(0, n)].reverse().find((s): s is Extract<Step, { kind: "video" }> => s.kind === "video");
  const upcoming = steps.slice(n + 1).find((s) => s.kind === "still")?.url;
  const ended = n >= steps.length;
  const btn = "flex h-11 w-11 items-center justify-center rounded-xl bg-white/10 text-white hover:bg-white/20 disabled:opacity-40";

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-black" role="dialog" aria-label={`The run: ${title}`}>
      <div className="relative min-h-0 flex-1">
        {base && <img src={base} alt="" className="absolute inset-0 h-full w-full object-contain" />}
        {video && (
          <video
            key={video}
            ref={videoRef}
            src={video}
            muted
            playsInline
            autoPlay={!paused}
            onPlaying={() => setVideoOn(true)}
            onEnded={next}
            onError={next}
            className="absolute inset-0 h-full w-full object-contain transition-opacity"
            style={{ opacity: videoOn ? 1 : 0, transitionDuration: `${FADE_MS}ms` }}
          />
        )}
        {upcoming && <img src={upcoming} alt="" className="hidden" />}
        {ended && (
          <div className="absolute inset-0 flex items-center justify-center">
            <button type="button" onClick={() => { setBase(steps.find((s) => s.kind === "still")?.url); setN(0); }} className="rounded-xl bg-white/15 px-5 py-3 text-white">
              Watch again
            </button>
          </div>
        )}
      </div>
      <div className="flex items-center gap-2 px-4 py-3 text-white">
        <button type="button" className={btn} onClick={() => jump(-1)} aria-label="Previous transition"><SkipBack size={20} /></button>
        <button type="button" className={btn} onClick={() => setPaused((p) => !p)} aria-label={paused ? "Play" : "Pause"} disabled={ended}>
          {paused ? <Play size={20} /> : <Pause size={20} />}
        </button>
        <button type="button" className={btn} onClick={() => jump(1)} aria-label="Next transition"><SkipForward size={20} /></button>
        <div className="min-w-0 flex-1 truncate text-[15px]">
          {title}
          {shown && ` · ${shown.of} of ${list.length} · ${shown.t.idea ?? ""}`}
        </div>
        <button type="button" className={btn} onClick={onClose} aria-label="Close"><X size={20} /></button>
      </div>
    </div>
  );
}
