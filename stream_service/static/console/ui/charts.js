// Meters and sparklines (diagnostics, show inputs), coloured from the CSS tokens at draw time.
import { h, clamp01 } from "../kit.js";

const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";

export function meter(frac, { tone = "" } = {}) {
  const m = h("div", { class: `meter${tone ? " " + tone : ""}` }, h("div"));
  m.firstChild.style.width = `${Math.round(clamp01(frac) * 100)}%`;
  return m;
}
export function setMeter(m, frac, tone = null) {
  m.firstChild.style.width = `${Math.round(clamp01(frac) * 100)}%`;
  if (tone != null) m.className = `meter${tone ? " " + tone : ""}`;
}

/** series: [{data, color: "--series-1", dash, width, scale}]; hist: the number of points across. */
export function spark(canvas, series, { maxHint = 1, hist = 120 } = {}) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, hgt = canvas.clientHeight;
  if (!w || !hgt) return;
  if (canvas.width !== Math.round(w * dpr)) { canvas.width = Math.round(w * dpr); canvas.height = Math.round(hgt * dpr); }
  const g = canvas.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, w, hgt);
  const vals = [];
  for (const s of series) for (const v of s.data) if (v != null && isFinite(v)) vals.push(v * (s.scale || 1));
  vals.sort((a, b) => a - b);
  const max = Math.max(maxHint || 1, vals.length ? vals[Math.floor(0.95 * (vals.length - 1))] : 0) * 1.1;
  g.strokeStyle = css("--series-grid"); g.lineWidth = 1;
  for (const f of [0.25, 0.5, 0.75]) { g.beginPath(); g.moveTo(0, Math.round(hgt * f) + 0.5); g.lineTo(w, Math.round(hgt * f) + 0.5); g.stroke(); }
  g.font = "11px ui-monospace, monospace"; g.fillStyle = css("--ink-3"); g.fillText(max.toFixed(0), 4, 12);
  for (const s of series) {
    g.strokeStyle = s.color.startsWith("--") ? css(s.color) : s.color; g.lineWidth = s.width || 1.75; g.setLineDash(s.dash || []);
    g.beginPath();
    let started = false;
    s.data.forEach((v, i) => {
      if (v == null || !isFinite(v)) { started = false; return; }
      const x = w - (s.data.length - 1 - i) * (w / (hist - 1));
      const y = Math.max(1, hgt - 2 - ((v * (s.scale || 1)) / max) * (hgt - 4));
      started ? g.lineTo(x, y) : g.moveTo(x, y);
      started = true;
    });
    g.stroke();
  }
  g.setLineDash([]);
}
