// The GPU's time by who used it, over the last 15 min (GET /api/perf/gpu, utils/gpu_watch.py): a stacked area of the
// 3D/compute engine's users, the card's own busy % as a line, now's jobs with their CPU, and each run at 95 %+ with
// what ran then. Polled every 4 s while it is on screen. Read-only.

import { useEffect, useMemo, useRef, useState } from "react";
import { sendRelative } from "@/shared/http/client";

type Lanes = Record<string, number>;
interface Job {
  l: string;
  cpu: number;
  n: number;
  gpu?: boolean;
  sid?: string;
  song?: string;
}
interface Sample {
  t: number;
  gpu: { util?: number; mem?: number; mem_total?: number; mhz?: number; max_mhz?: number; temp?: number; w?: number; thr?: string[] };
  render: Record<string, { ms: number; fps: number; pct: number; late?: number; scene?: string }>;
  jobs: Job[];
  lanes: Lanes;
}
interface Hot {
  from: number;
  s: number;
  lanes: Lanes;
  jobs: string[];
  late_pct: number;
}
interface View {
  running: boolean;
  now?: Sample | null;
  series?: { t: number; total: number | null; lanes: Lanes }[];
  summary?: { samples: number; util_mean?: number; util_p95?: number; hot_s?: number; lanes?: Lanes; hot?: Hot[] };
}

/** The 3D/compute engine's users, stacked in this order, each its fixed categorical slot. */
const STACK: { key: string; color: string }[] = [
  { key: "Renderer", color: "#257de0" },
  { key: "Karaoke prep", color: "#f47230" },
  { key: "Other WSL", color: "#00b894" },
  { key: "Browser", color: "#f2a600" },
  { key: "Desktop", color: "#ee7fac" },
  { key: "Other Windows", color: "#008a17" },
];
const ENGINES = ["Video encode", "Video decode"];
const MINUTES = 15;
const POLL_MS = 4000;
const W = 640;
const H = 150;

const clock = (t: number) => new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const pct = (v: number | null | undefined) => (v == null ? "–" : `${Math.round(v)}%`);

export function GpuCard() {
  const box = useRef<HTMLDivElement>(null);
  const [view, setView] = useState<View | null>(null);
  const [hover, setHover] = useState<number | null>(null);

  useEffect(() => {
    let alive = true;
    let seen = false;
    const io = new IntersectionObserver(([e]) => {
      seen = e.isIntersecting;
      if (seen) read();
    });
    if (box.current) io.observe(box.current);
    const read = async () => {
      if (!seen || document.visibilityState === "hidden") return;
      try {
        const r = await sendRelative(`/perf/gpu?minutes=${MINUTES}`);
        if (r.ok && alive) setView(await r.json());
      } catch {
        /* the next poll */
      }
    };
    const id = window.setInterval(read, POLL_MS);
    return () => {
      alive = false;
      io.disconnect();
      window.clearInterval(id);
    };
  }, []);

  const series = view?.series ?? [];
  const t0 = series[0]?.t ?? 0;
  const t1 = series[series.length - 1]?.t ?? 1;
  const x = (t: number) => (t1 > t0 ? ((t - t0) / (t1 - t0)) * W : 0);
  const y = (v: number) => H - (Math.min(100, Math.max(0, v)) / 100) * H;

  const areas = useMemo(() => {
    const base = series.map(() => 0);
    return STACK.map(({ key, color }) => {
      const lo = [...base];
      series.forEach((s, i) => (base[i] += s.lanes[key] ?? 0));
      const top = series.map((s, i) => `${x(s.t).toFixed(1)},${y(base[i]).toFixed(1)}`);
      const bot = series.map((s, i) => `${x(s.t).toFixed(1)},${y(lo[i]).toFixed(1)}`).reverse();
      const any = series.some((s) => (s.lanes[key] ?? 0) > 0);
      return { key, color, d: any && series.length > 1 ? `M${top.join("L")}L${bot.join("L")}Z` : "" };
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [series]);
  const total = series.filter((s) => s.total != null).map((s) => `${x(s.t).toFixed(1)},${y(s.total!).toFixed(1)}`);

  const now = view?.now;
  const at = hover != null ? series[hover] : null;
  const shown: Lanes = at ? at.lanes : (now?.lanes ?? {});
  const sm = view?.summary;
  const g = now?.gpu ?? {};

  if (view && !view.running) return <div className="text-sm text-muted-foreground">No GPU here</div>;

  return (
    <div ref={box} className="flex flex-col gap-3" data-testid="gpu-card">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 text-sm">
        <span className="text-2xl font-semibold tabular-nums">{pct(at ? at.total : g.util)}</span>
        <span className="text-muted-foreground">{at ? clock(at.t) : "now"}</span>
        {!at && g.mhz != null && (
          <span className="tabular-nums text-muted-foreground">
            {g.mhz}/{g.max_mhz} MHz · {g.temp}°C · {g.w != null ? Math.round(g.w) : "–"} W ·{" "}
            {g.mem != null && g.mem_total ? `${(g.mem / 1024).toFixed(1)}/${(g.mem_total / 1024).toFixed(1)} GB` : ""}
          </span>
        )}
        {!at && (g.thr?.length ?? 0) > 0 && <span className="font-medium text-[#dc3c3d]">⚠ {g.thr!.join(", ")}</span>}
        {sm?.samples ? (
          <span className="ml-auto tabular-nums text-muted-foreground">
            {MINUTES} min: mean {pct(sm.util_mean)} · p95 {pct(sm.util_p95)} · 95%+ {sm.hot_s} s
          </span>
        ) : null}
      </div>

      <div className="relative">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          preserveAspectRatio="none"
          className="h-[150px] w-full rounded-lg bg-white"
          onMouseMove={(e) => {
            if (series.length < 2) return;
            const r = e.currentTarget.getBoundingClientRect();
            const t = t0 + ((e.clientX - r.left) / r.width) * (t1 - t0);
            let best = 0;
            series.forEach((s, i) => Math.abs(s.t - t) < Math.abs(series[best].t - t) && (best = i));
            setHover(best);
          }}
          onMouseLeave={() => setHover(null)}
        >
          {[25, 50, 75].map((v) => (
            <line key={v} x1={0} x2={W} y1={y(v)} y2={y(v)} stroke="#e5e5e5" strokeWidth={1} vectorEffect="non-scaling-stroke" />
          ))}
          {areas.map(
            (a) =>
              a.d && <path key={a.key} d={a.d} fill={a.color} stroke="#ffffff" strokeWidth={2} vectorEffect="non-scaling-stroke" />,
          )}
          {total.length > 1 && (
            <polyline points={total.join(" ")} fill="none" stroke="#191919" strokeWidth={2} vectorEffect="non-scaling-stroke" />
          )}
          {at && <line x1={x(at.t)} x2={x(at.t)} y1={0} y2={H} stroke="#666666" strokeWidth={1} vectorEffect="non-scaling-stroke" />}
        </svg>
        <div className="pointer-events-none absolute inset-y-0 right-1 flex flex-col justify-between py-0.5 text-[10px] text-muted-foreground">
          <span>100%</span>
          <span>0</span>
        </div>
      </div>

      <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-0.5 w-4 bg-[#191919]" />
          Card
        </span>
        {STACK.map(({ key, color }) => (
          <span key={key} className="flex items-center gap-1.5">
            <span className="inline-block h-3 w-3 rounded-sm" style={{ background: color }} />
            {key}
            <span className="tabular-nums text-muted-foreground">{pct(shown[key] ?? 0)}</span>
          </span>
        ))}
        {ENGINES.map((k) => (
          <span key={k} className="flex items-center gap-1.5">
            {k}
            <span className="tabular-nums text-muted-foreground">{pct(shown[k] ?? 0)}</span>
          </span>
        ))}
      </div>

      {now && (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-muted-foreground">
              <th className="font-normal">Running</th>
              <th className="w-20 text-right font-normal">CPU</th>
              <th className="w-12 text-right font-normal">GPU</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(now.render).map(([scr, r]) => (
              <tr key={`r-${scr}`}>
                <td>
                  Renderer · {scr} · {r.scene ?? ""}
                  <span className="text-muted-foreground">
                    {" "}
                    {r.ms} ms × {r.fps} fps{r.late ? ` · ${r.late}% late` : ""}
                  </span>
                </td>
                <td />
                <td className="text-right tabular-nums">{pct(r.pct)}</td>
              </tr>
            ))}
            {now.jobs.slice(0, 12).map((j) => (
              <tr key={`${j.l}-${j.sid ?? ""}`}>
                <td>
                  {j.l}
                  {j.song ? ` · ${j.song}` : ""}
                  {j.n > 1 ? <span className="text-muted-foreground"> ×{j.n}</span> : null}
                </td>
                <td className="text-right tabular-nums">{Math.round(j.cpu)}%</td>
                <td className="text-right">{j.gpu ? "●" : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {(sm?.hot?.length ?? 0) > 0 && (
        <div className="flex flex-col gap-1 text-sm">
          <div className="text-muted-foreground">95%+</div>
          {[...sm!.hot!].reverse().map((h) => (
            <div key={h.from} className="flex flex-wrap gap-x-3">
              <span className="tabular-nums">{clock(h.from)}</span>
              <span className="tabular-nums text-muted-foreground">{h.s} s</span>
              <span>
                {Object.entries(h.lanes)
                  .filter(([k]) => !ENGINES.includes(k))
                  .map(([k, v]) => `${k} ${Math.round(v)}%`)
                  .join(" · ")}
              </span>
              {h.late_pct > 0 && <span className="text-[#dc3c3d]">screens {h.late_pct}% late</span>}
              {h.jobs.length > 0 && <span className="text-muted-foreground">{h.jobs.join(", ")}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
