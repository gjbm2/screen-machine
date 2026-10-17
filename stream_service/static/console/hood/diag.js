// Diagnostics for the "Looking at" session (/api/diag?session=…, 2 Hz only while open): a verdict in words (rose
// only for a TV session over budget for at least 10 s, otherwise amber "Brief slow frames"), host and GPU bars,
// three sparklines and a text summary.
import { h, put, visibleInterval, fmt, http } from "../kit.js";
import { diag as getDiag } from "../data/renderer.js";
import { meter, spark } from "../ui/charts.js";
import { switchEl } from "../ui/controls.js";
import { toastErr } from "../ui/toast.js";
import { diagWords } from "../words.js";

const HIST = 120;
export function mountDiag(host, ctx) {
  const verdict = h("div", { class: "status tone-ok", tid: "diag-verdict" }, "–");
  const which = h("p", { class: "meta" });
  const bars = h("div", { class: "meters" });
  const c1 = h("canvas", { class: "spark", tid: "diag-spark" }), c2 = h("canvas", { class: "spark", tid: "diag-spark" }), c3 = h("canvas", { class: "spark", tid: "diag-spark" });
  const lg = (items) => h("div", { class: "legend" }, items.map(([c, t]) => h("span", {}, h("i", { style: { background: `var(${c})` } }), t)));
  const text = h("pre", { class: "mono", style: { whiteSpace: "pre-wrap", margin: "8px 0 0" } });
  // the TV's details panel (docs/LIVE_DIAGNOSTICS.md): the room's diag.<screen> setting, the same one as the Room
  // page's switch, so the two never disagree.  Shown for a room screen only; never disabled.
  let hudScreen = null;
  const hudLine = h("span", {});
  const hudSw = switchEl("Show details on the TV", false, async (on) => {
    if (!hudScreen) return;
    const r = await http("PUT", "/api/v1/room/targets", { values: { [`diag.${hudScreen}`]: on }, source: "console" });
    if (!r.ok) { toastErr(`Couldn't switch the details: ${r.detail || r.error}`); hudSw.sw.set(!on); }
  }, { tid: "hud-switch", sub: hudLine });
  hudSw.hidden = true;
  put(host, h("div", { class: "stack" }, hudSw, verdict, which, bars,
    lg([["--series-2", "render p95"], ["--series-3", "write p95"], ["--series-1", "encode p50"], ["--series-4", "budget"]]), c1,
    lg([["--series-4", "late %"], ["--series-2", "dropped by the client /s (×10)"]]), c2,
    lg([["--series-3", "client buffer (ms)"], ["--series-1", "Mbit/s (×10)"]]), c3, text));
  const hist = { render: [], write: [], encode: [], budget: [], late: [], dropped: [], buffered: [], mbps: [] };
  let histSid = null, badSince = null;
  const push = (k, v) => { hist[k].push(v); if (hist[k].length > HIST) hist[k].shift(); };
  visibleInterval(async () => {
    const sid = ctx.lookingAt();
    const r = await getDiag(sid);
    if (!r.ok) { verdict.textContent = `No diagnostics: ${r.detail || r.error}`; return; }
    const d = r.data;
    const hst = d.host || {};
    const rows = [];
    const bar = (label, frac, txt) => { const tone = frac >= 0.95 ? "bad" : frac >= 0.8 ? "warn" : ""; rows.push(h("span", {}, label), meter(frac, { tone }), h("span", { class: "mono" }, txt)); };
    if (hst.load) bar("Host load (1 min)", hst.load[0] / (hst.cpus || 1), `${hst.load[0].toFixed(1)} / ${hst.cpus}`);
    for (const g of d.gpus || []) {
      bar(`GPU ${g.index} ${String(g.name).replace(/NVIDIA |GeForce /g, "")}`, (g.util ?? 0) / 100, `${g.util ?? "–"} %`);
      bar(`  Encoder ${g.index}`, (g.enc_util ?? 0) / 100, `${g.enc_util ?? "–"} %`);
      bar(`  Memory ${g.index}`, (g.mem_used_mb ?? 0) / (g.mem_total_mb || 1), `${((g.mem_used_mb ?? 0) / 1024).toFixed(1)} GB`);
    }
    put(bars, rows);
    const s = sid ? (d.sessions || []).find((x) => x.session === sid) : null;
    if (!s || !s.window) {
      verdict.className = "status tone-mute";
      verdict.textContent = sid ? "No frames from that session yet." : "Nothing is running that's yours: pick a session under Looking at.";
      which.textContent = ""; text.textContent = "";
      return;
    }
    if (histSid !== s.session) { for (const k in hist) hist[k] = []; histSid = s.session; badSince = null; }
    const tv = !!ctx.roomScreenOf(s.session);
    hudScreen = tv ? s.screen : null;
    hudSw.hidden = !tv;
    if (tv) {
      const hs = await http("GET", `/api/sessions/${s.session}`);
      const hd = hs.ok ? hs.data?.hud : null;
      if (hd) {
        hudSw.sw.set(!!hd.on);
        hudLine.textContent = hd.failed ? `Details stopped (${hd.failed})` : hd.on ? "On the TV now" : "";
      }
    }
    const lvl = d.verdict?.level || "ok";
    if (lvl === "bad") badSince = badSince ?? Date.now(); else badSince = null;
    const w0 = diagWords(lvl, d.verdict?.text || "", tv && badSince && Date.now() - badSince >= 10000);
    verdict.className = `status tone-${w0.tone}`;
    verdict.textContent = w0.text;
    which.textContent = `${s.screen} · ${s.session} · ${s.profile} · GPU ${s.gpu}`;
    const w = s.window, c = s.client || {};
    push("render", w.render_ms?.p95); push("write", w.write_ms?.p95); push("encode", w.encode_ms?.p50); push("budget", w.budget_ms);
    push("late", w.late_pct); push("dropped", c.dropped_per_s); push("buffered", c.buffered_ms); push("mbps", w.mbps);
    spark(c1, [{ data: hist.budget, color: "--series-4", dash: [4, 3], width: 1 }, { data: hist.render, color: "--series-2" }, { data: hist.write, color: "--series-3" }, { data: hist.encode, color: "--series-1" }], { maxHint: (w.budget_ms || 33) * 1.5 });
    spark(c2, [{ data: hist.late, color: "--series-4" }, { data: hist.dropped, color: "--series-2", scale: 10 }], { maxHint: 10 });
    spark(c3, [{ data: hist.buffered, color: "--series-3" }, { data: hist.mbps, color: "--series-1", scale: 10 }], { maxHint: 400 });
    const f = (x) => `${fmt(x?.p50, 1)}/${fmt(x?.p95, 1)}/${fmt(x?.max, 1)}`;
    text.textContent = `last ${w.window_s} s: ${w.frames} frames, late ${fmt(w.late_pct, 1)} %, repeats ${w.repeats}, resyncs ${w.resyncs}, queue ${w.encode_queue}, ${fmt(w.mbps, 1)} Mbit/s\n` +
      `ms p50/p95/max  render ${f(w.render_ms)}  write ${f(w.write_ms)}  encode ${f(w.encode_ms)}  slip ${f(w.slip_ms)}  host ${f(w.host_ms)}  (budget ${w.budget_ms})\n` +
      `client: presented ${c.presented ?? "–"}  dropped ${c.dropped ?? "–"} (${fmt(c.dropped_per_s, 1)}/s)  buffered ${c.buffered_ms ?? "–"} ms`;
  }, 500, ctx.isOpen("diag"));
}
