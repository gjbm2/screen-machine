// Show inputs: the show-bus monitor for the session under "Looking at" (room's GET …/inputs for a room screen, the
// renderer's /api/sessions/{sid}/inputs otherwise), 4 Hz while open: the link in words, BPM, the follower, colours,
// effect / variant, lead, masters, six meters, bindings and a 24 px beat lamp.
import { h, put, http, visibleInterval, fmt, clamp01 } from "../kit.js";
import * as room from "../data/room.js";
import { inputsWords, beatWords } from "../words.js";
import { meter, setMeter } from "../ui/charts.js";
import { paintLamp } from "../onscreen/music.js";

const hexOf = (rgb) => (Array.isArray(rgb) ? "#" + rgb.map((c) => Math.round(clamp01(c) * 255).toString(16).padStart(2, "0")).join("") : "#000");

export function mountInputs(host, ctx) {
  const link = h("p", { style: { margin: 0 } }, "–");
  const lamp = h("span", { class: "lamp big", tid: "inputs-lamp", "aria-hidden": "true" });
  const bpm = h("div", { style: { fontSize: "22px", fontWeight: 600, fontVariantNumeric: "tabular-nums" } }, "– bpm");
  const lock = h("div", { class: "meta" }, "–");
  const rgb = h("span", { style: { display: "inline-block", width: "34px", height: "22px", borderRadius: "4px", border: "1px solid var(--line)" } });
  const rgbSlow = h("span", { style: { display: "inline-block", width: "34px", height: "22px", borderRadius: "4px", border: "1px solid var(--line)" } });
  const kv = h("div", { class: "kv" });
  const names = [["confidence", "Beat confidence", "ok"], ["activity_slow", "Activity (slow)", ""], ["activity", "Activity", ""], ["pulse", "Pulse", "info"], ["beat", "Beat", "info"], ["level", "Level", "ok"]];
  const meters = {}, vals = {};
  const mBox = h("div", { class: "meters", tid: "inputs-meters" }, names.flatMap(([k, l, tone]) => { meters[k] = meter(0, { tone }); vals[k] = h("span", { class: "mono" }, "–"); return [h("span", {}, l), meters[k], vals[k]]; }));
  const binds = h("pre", { class: "log", style: { maxHeight: "200px", marginTop: "8px" } });
  put(host, h("div", { class: "stack", tid: "inputs-monitor" }, link,
    h("div", { class: "row", style: { gap: "16px", alignItems: "center" } }, lamp, h("div", {}, bpm, lock), h("div", {}, h("div", { class: "meta" }, "Colour / slow"), h("div", { class: "row" }, rgb, rgbSlow))),
    kv, mBox, h("div", {}, h("div", { class: "meta" }, "What follows the music"), binds)));
  let I = null, at = 0;
  visibleInterval(async () => {
    const sid = ctx.lookingAt();
    if (!sid) { link.textContent = "Nothing to look at: pick a session under Looking at."; I = null; return; }
    const scr = ctx.roomScreenOf(sid);
    // room's view while the screen is settled live; mid-cut (room answers 409) or for other sessions, the renderer's
    const r = scr && room.isReady(room.screenOf(scr)) ? await room.inputs(scr) : await http("GET", `/api/sessions/${sid}/inputs`);
    if (!r.ok) { link.textContent = `No reading: ${r.detail || r.error}`; I = null; return; }
    I = r.data; at = performance.now();
    const d = I.derived || {}, v = I.values || {};
    link.textContent = `${inputsWords(I)}${I.fixture ? ` · fixture ${I.fixture}` : ""}`;
    bpm.textContent = d.bpm ? `${d.bpm.toFixed(1)} bpm` : "– bpm";
    lock.textContent = beatWords(d);
    rgb.style.background = hexOf(v.rgb); rgbSlow.style.background = hexOf(d.rgb_slow);
    const L = I.lead || {};
    put(kv, h("span", {}, "Effect / variant"), h("span", {}, `${v.effect ?? "–"} / ${v.variant ?? "–"}`),
      h("span", {}, "Lead"), h("span", {}, `${fmt(L.total_ms, 0)} ms (server ${fmt(L.server_ms, 0)} + buffer ${fmt(L.buffered_ms, 0)} + frame ${fmt(L.frame_ms, 0)} + photon ${fmt(L.photon_ms, 0)})${L.measured ? "" : " estimated"}`),
      h("span", {}, "Masters"), h("span", {}, `speed ×${fmt(I.masters?.speed)} · depth ${fmt(I.masters?.depth)}`));
    const src = { confidence: d.beat_confidence, activity_slow: d.activity_slow, activity: v.activity, pulse: v.pulse, beat: d.beat, level: v.level };
    for (const [k] of names) { setMeter(meters[k], src[k] ?? 0); vals[k].textContent = fmt(src[k]); }
    binds.textContent = (I.bindings || []).map((b) => `${String(b.param).padEnd(14)} ← ${String(b.bind).padEnd(13)} [${(b.range || []).map((x) => fmt(x)).join(", ")}]  ${fmt(b.value, 3)}`).join("\n") || "Nothing is bound to the music.";
  }, 250, ctx.isOpen("inputs"));
  const frame = () => { requestAnimationFrame(frame); if (lamp.isConnected && ctx.isOpen("inputs")()) paintLamp(lamp, I, at); };
  requestAnimationFrame(frame);
}
