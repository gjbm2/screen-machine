// The live input meter (owner, 16 Oct: "near real time ... the whole width shouldn't show more than 5 s, and almost no
// latency to input"). It reads show_service's WS /v1/levels (through room_service's /show pass-through): one frame per
// burst of audio packets, at most ~60 a second, each the loudest packet's peak in dBFS. Every sample is placed by the
// time it reached this page and the canvas is redrawn on every display frame, so the trace slides right to left
// continuously; the newest sample is at the right edge. Square edges: nothing clips it.
//
// Under it, the spectrum on the same time axis (a spectrogram): show_service's raw FFT of the newest 43 ms in 48
// log-spaced bands, 40 Hz at the bottom to 16 kHz at the top, colour = dBFS (SPEC_FLOOR dark .. SPEC_TOP bright).
// It scrolls in place: each display frame shifts it left by the time passed and paints the newest spectrum in.
//
// Scale: dBFS from FLOOR (bottom) to 0 (top), with lines at -60, -40 and -20. Measured 16 Oct (PC22 mic): capture to a
// client on PC22, through show_service in WSL and room_service's pass-through, 5 ms median, 13-20 ms p95; Windows'
// audio engine adds its own 10 ms bursts before that, and the screen one frame (up to 17 ms) after.

import { useEffect, useRef, useState } from "react";
import { isDark } from "@/darkMode";

const WINDOW_MS = 5000;
const FLOOR_DB = -72;
const GRID_DB = [-60, -40, -20];
const KEEP = 2000;                                     // samples kept (more than 5 s at 60 a second)

type Sample = { at: number; db: number; f?: number[] };

const SPEC_FLOOR = -100;
const SPEC_TOP = -20;
const SPEC_BG = "#0c0a09";
const SPEC_MARKS = [100, 1000, 10000];

/** dBFS -> colour: near-black, deep blue, teal, yellow, white. */
const LUT: string[] = (() => {
  const stops: [number, [number, number, number]][] = [
    [0, [12, 10, 9]],
    [0.3, [30, 58, 138]],
    [0.6, [13, 148, 136]],
    [0.85, [250, 204, 21]],
    [1, [255, 251, 235]],
  ];
  const out: string[] = [];
  for (let i = 0; i < 256; i++) {
    const v = i / 255;
    const k = stops.findIndex(([p]) => p >= v);
    const [p1, c1] = stops[Math.max(1, k)];
    const [p0, c0] = stops[Math.max(0, k - 1)];
    const f = p1 === p0 ? 0 : (v - p0) / (p1 - p0);
    out.push(`rgb(${c0.map((c, j) => Math.round(c + (c1[j] - c) * f)).join(",")})`);
  }
  return out;
})();

function colour(db: number): string {
  const v = Math.min(1, Math.max(0, (db - SPEC_FLOOR) / (SPEC_TOP - SPEC_FLOOR)));
  return LUT[Math.round(v * 255)];
}

function y(db: number, h: number): number {
  const v = Math.min(1, Math.max(0, (db - FLOOR_DB) / -FLOOR_DB));
  return h - v * h;
}

export function LevelTrace({ url, className }: { url: string | null; className?: string }) {
  const canvas = useRef<HTMLCanvasElement | null>(null);
  const spec = useRef<HTMLCanvasElement | null>(null);
  const specBuf = useRef<HTMLCanvasElement | null>(null);
  const range = useRef<{ lo: number; hi: number }>({ lo: 40, hi: 16000 });
  const samples = useRef<Sample[]>([]);
  // Clock alignment with show_service (ping/pong, the lowest round trip of the last 20 wins) and, per sample, how long
  // after show_service received it this page did: the delay readout, measured rather than claimed.
  const pongs = useRef<{ rtt: number; off: number }[]>([]);
  const lags = useRef<{ at: number; ms: number }[]>([]);
  const [now, setNow] = useState<{ db: number; live: boolean; lag: number | null }>({ db: FLOOR_DB, live: false, lag: null });

  // The feed: reconnects every second while it's down.
  useEffect(() => {
    if (!url) return;
    let sock: WebSocket | null = null;
    let retry: number | undefined;
    let closed = false;
    const ping = window.setInterval(() => {
      if (sock?.readyState === WebSocket.OPEN) sock.send(JSON.stringify({ ping: performance.now() }));
    }, 1000);
    const open = () => {
      sock = new WebSocket(url);
      pongs.current = [];
      sock.onopen = () => sock?.send(JSON.stringify({ ping: performance.now() }));
      sock.onmessage = (ev) => {
        const at = performance.now();
        try {
          const f = JSON.parse(ev.data as string) as {
            pk?: number;
            a?: number;
            pong?: number;
            srv?: number;
            f?: number[];
            fl?: number;
            fh?: number;
          };
          if (typeof f.pong === "number" && typeof f.srv === "number") {
            const rtt = at - f.pong;
            pongs.current = [...pongs.current, { rtt, off: f.srv * 1000 - (f.pong + rtt / 2) }].slice(-20);
            return;
          }
          if (typeof f.pk !== "number") return;
          const s = samples.current;
          s.push({ at, db: f.pk, f: Array.isArray(f.f) ? f.f : undefined });
          if (typeof f.fl === "number" && typeof f.fh === "number") range.current = { lo: f.fl, hi: f.fh };
          if (s.length > KEEP) s.splice(0, s.length - KEEP);
          const best = pongs.current.reduce<{ rtt: number; off: number } | null>((m, p) => (!m || p.rtt < m.rtt ? p : m), null);
          if (best && typeof f.a === "number") {
            lags.current.push({ at, ms: at - (f.a * 1000 - best.off) });
            if (lags.current.length > 400) lags.current.splice(0, lags.current.length - 400);
          }
        } catch {
          /* a frame we can't read: skipped */
        }
      };
      sock.onclose = () => {
        if (!closed) retry = window.setTimeout(open, 1000);
      };
    };
    open();
    return () => {
      closed = true;
      window.clearTimeout(retry);
      window.clearInterval(ping);
      sock?.close();
    };
  }, [url]);

  // Drawing: every display frame.
  useEffect(() => {
    let raf = 0;
    let lastReadout = 0;
    let lastT = 0;
    let acc = 0;
    const draw = (t: number) => {
      raf = requestAnimationFrame(draw);
      const c = canvas.current;
      if (!c) return;
      const dpr = window.devicePixelRatio || 1;
      const w = Math.round(c.clientWidth * dpr);
      const h = Math.round(c.clientHeight * dpr);
      if (c.width !== w || c.height !== h) {
        c.width = w;
        c.height = h;
      }
      const g = c.getContext("2d");
      if (!g) return;
      g.clearRect(0, 0, w, h);
      const night = isDark(); // dark mode (src/darkMode.ts)
      g.fillStyle = night ? "#231f1d" : "#f5f5f4";
      g.fillRect(0, 0, w, h);
      g.strokeStyle = night ? "#44403c" : "#d6d3d1";
      g.lineWidth = dpr;
      g.font = `${10 * dpr}px ui-sans-serif, system-ui`;
      g.fillStyle = night ? "#78716c" : "#a8a29e";
      for (const db of GRID_DB) {
        const gy = Math.round(y(db, h)) + 0.5;
        g.beginPath();
        g.moveTo(0, gy);
        g.lineTo(w, gy);
        g.stroke();
        g.fillText(`${db} dB`, 3 * dpr, gy - 2 * dpr);
      }
      const s = samples.current;
      const x = (at: number) => w - ((t - at) / WINDOW_MS) * w;
      // The trace: a filled step per sample, from its arrival to the next one's (a gap over 250 ms stays empty).
      g.fillStyle = "#0d9488";
      for (let i = s.length - 1; i >= 0; i--) {
        const x0 = x(s[i].at);
        if (x0 < 0) break;
        const next = i + 1 < s.length ? s[i + 1].at : t;
        const x1 = x(Math.min(next, s[i].at + 250));
        const top = y(s[i].db, h);
        g.fillRect(x0, top, Math.max(dpr, x1 - x0), h - top);
      }
      // The spectrogram: shift the buffer left by the time passed, paint the newest spectrum into the gap.
      const sc = spec.current;
      if (sc) {
        const sw = Math.round(sc.clientWidth * dpr);
        const sh = Math.round(sc.clientHeight * dpr);
        let buf = specBuf.current;
        if (!buf || buf.width !== sw || buf.height !== sh) {
          buf = document.createElement("canvas");
          buf.width = sw;
          buf.height = sh;
          specBuf.current = buf;
          const b0 = buf.getContext("2d");
          if (b0) {
            b0.fillStyle = SPEC_BG;
            b0.fillRect(0, 0, sw, sh);
          }
          acc = 0;
        }
        if (sc.width !== sw || sc.height !== sh) {
          sc.width = sw;
          sc.height = sh;
        }
        const bg = buf.getContext("2d");
        const sg = sc.getContext("2d");
        if (bg && sg) {
          acc += lastT ? ((t - lastT) / WINDOW_MS) * sw : 0;
          const dx = Math.min(sw, Math.floor(acc));
          acc -= dx;
          if (dx > 0) {
            bg.drawImage(buf, -dx, 0);
            let last: Sample | undefined;
            for (let i = s.length - 1; i >= 0 && !last; i--) if (s[i].f) last = s[i];
            if (last?.f && t - last.at < 250) {
              const n = last.f.length;
              for (let i = 0; i < n; i++) {
                const y0 = Math.round(sh - ((i + 1) * sh) / n);
                const y1 = Math.round(sh - (i * sh) / n);
                bg.fillStyle = colour(last.f[i]);
                bg.fillRect(sw - dx, y0, dx, y1 - y0);
              }
            } else {
              bg.fillStyle = SPEC_BG;
              bg.fillRect(sw - dx, 0, dx, sh);
            }
          }
          sg.drawImage(buf, 0, 0);
          const { lo, hi } = range.current;
          sg.font = `${10 * dpr}px ui-sans-serif, system-ui`;
          sg.fillStyle = "rgba(255,255,255,0.75)";
          for (const hz of SPEC_MARKS) {
            if (hz <= lo || hz >= hi) continue;
            const yy = sh - (Math.log(hz / lo) / Math.log(hi / lo)) * sh;
            sg.fillRect(0, Math.round(yy), 6 * dpr, dpr);
            sg.fillText(hz >= 1000 ? `${hz / 1000}k` : `${hz}`, 8 * dpr, yy + 4 * dpr);
          }
        }
      }
      lastT = t;
      // The readout, a few times a second.
      if (t - lastReadout > 150) {
        lastReadout = t;
        // The loudest of the last 300 ms, so the number is readable rather than flickering between sounds.
        let db = -Infinity;
        for (let i = s.length - 1; i >= 0 && t - s[i].at < 300; i--) db = Math.max(db, s[i].db);
        const live = s.length > 0 && t - s[s.length - 1].at < 500;
        const v = Number.isFinite(db) ? db : FLOOR_DB;
        const recent = lags.current.filter((l) => t - l.at < 2000).map((l) => l.ms).sort((a, b) => a - b);
        const lag = recent.length ? Math.max(0, Math.round(recent[Math.floor(recent.length / 2)])) : null;
        setNow((p) => (p.live === live && Math.round(p.db) === Math.round(v) && p.lag === lag ? p : { db: v, live, lag }));
      }
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, []);

  return (
    <div className={className}>
      <canvas ref={canvas} aria-hidden className="block h-20 w-full" />
      <canvas ref={spec} aria-hidden className="mt-px block h-24 w-full" />
      <div className="mt-1 flex justify-between gap-2 whitespace-nowrap text-xs text-muted-foreground">
        <span className="truncate">Level · spectrum · 5 s</span>
        <span className="font-mono tabular-nums text-stone-700" title="Delay: median over 2 s from show_service receiving the mic's audio to this page, by clock alignment">
          {now.live ? (now.db <= FLOOR_DB ? "silent" : `${Math.round(now.db)} dBFS`) : "no input"}
          {now.lag !== null && ` · delay ${now.lag} ms`}
        </span>
      </div>
    </div>
  );
}
