// Go live (the art state's primary): what to start with (Surprise me, or a scene from the Picker or the Viewer's
// "Start the live show with this"), the transitions in and out, and a "Start with this feel" draft sent to room's
// look once live.  room PUT {desired: {live: {scene, transitions: {in, out}}}}; progress in Room's words with seconds.
import { h, put, store as ls } from "../kit.js";
import { S } from "../store.js";
import * as room from "../data/room.js";
import * as catalog from "../data/catalog.js";
import { lib } from "../data/library.js";
import { labelForSpec } from "../data/query.js";
import { button, chip, select, switchEl, unroll, busy, statusRow, setLabel } from "../ui/controls.js";
import { toastErr } from "../ui/toast.js";
import { errorWords, stuckAdvice, roomErrorWords } from "../words.js";
import { patch as patchLook } from "./look.js";
import { transitionOptions } from "./picker.js";

export const FEEL = [["calm", "Calm", 0.2, "gentle"], ["steady", "Steady", 0.5, null], ["lively", "Lively", 0.75, "lively"], ["wild", "Wild", 0.95, "wild"]];
const G = () => S.get().golive || {};
function setG(patch) { const g = { ...G(), ...patch }; S.set({ golive: g }); ls.set("sm.console.golive", g); }

async function surprise() {
  const c = await catalog.ready();
  const names = Object.entries(c?.presets?.scenes || {}).filter(([n, p]) => {
    if (!p.tags || p.tags.heavy) return false;
    const it = lib.pidx.get(`scene:${n}`)?.item;
    return !(it && (it._reject || it.slow || it.load === "error"));
  }).map(([n]) => n);
  return names[Math.floor(Math.random() * names.length)] || null;
}

let stepState = null;     // {target, t0, text, tone, done}
export function renderArt(host, target, { openPicker }) {
  const g = G();
  const name = room.nameOf(target);
  const real = room.isReal(target);
  const entryLabel = g.entry ? g.entry.label : "Surprise me";
  const startBtn = button(entryLabel, { kind: "outline", tid: "startwith", icon: "sparkles", onClick: () => openPicker("scene", {
    title: `Start ${name.replace(/ screen$/i, "")} with…`, surprise: () => setG({ entry: null }),
    onPick: (it, preset) => setG({ entry: { key: it.key, spec: preset || it.spec, label: pickLabel(it, preset) } }) }) });
  // the button names the choice, whether it came from the Picker or the Viewer's "Start the live show with this"
  const off = S.on(["golive"], () => { if (!startBtn.isConnected) { off(); return; } setLabel(startBtn, G().entry ? G().entry.label : "Surprise me"); });
  const go = button(`Go live on ${name}${real ? " (the real TV)" : ""}`, { kind: "primary", big: true, tid: "golive-btn", cls: "btn-block" });
  const steps = h("div", { class: "steps", tid: "golive-steps", "aria-live": "polite" });
  const tOpts = transitionOptions().filter(([v]) => v);
  const tin = select(tOpts, g.in || (tOpts.some(([v]) => v === "melt-2s") ? "melt-2s" : "crossfade-3s"), (v) => setG({ in: v }), { tid: "tin-select", aria: "Going live" });
  const tout = select(tOpts, g.out || "crossfade-3s", (v) => setG({ out: v }), { tid: "tout-select", aria: "Going back to art" });
  const tU = unroll("Transitions", { tid: "transitions-unroll", hint: `${labelOf(tin.value)} in · ${labelOf(tout.value)} out` });
  put(tU.body, h("div", { class: "fields" }, h("label", { class: "field" }, h("span", { class: "lab" }, "Going live"), tin), h("label", { class: "field" }, h("span", { class: "lab" }, "Going back to art"), tout)));
  tin.addEventListener("change", () => tU.setHint(`${labelOf(tin.value)} in · ${labelOf(tout.value)} out`));
  tout.addEventListener("change", () => tU.setHint(`${labelOf(tin.value)} in · ${labelOf(tout.value)} out`));
  // the feel to start with (a local draft)
  const feel = g.feel || {};
  const fU = unroll("Start with this feel", { tid: "feel-start-unroll", hint: feelHint(feel) });
  const autoSw = switchEl("Auto picks scenes", feel.auto !== false, (on) => { setG({ feel: { ...G().feel, auto: on } }); fU.setHint(feelHint(G().feel)); });
  const chips = h("div", { class: "feel" }, FEEL.map(([k, l]) => chip(l, { pressed: feel.energy === k, onClick: () => { setG({ feel: { ...G().feel, energy: G().feel?.energy === k ? null : k } }); fU.setHint(feelHint(G().feel)); redrawChips(); } })));
  const redrawChips = () => chips.querySelectorAll(".chip").forEach((c, i) => { const on = G().feel?.energy === FEEL[i][0]; c.setAttribute("aria-pressed", String(on)); });
  const pal = select([["", "Each scene's own colours"], ...Object.entries(catalog.get()?.palettes || {}).map(([n, p]) => [n, p.label || n])], feel.palette || "",
                     (v) => { setG({ feel: { ...G().feel, palette: v || null } }); fU.setHint(feelHint(G().feel)); }, { aria: "Colours" });
  put(fU.body, h("div", { class: "stack" }, autoSw, h("div", {}, h("span", { class: "lab" }, "Feel"), chips), h("label", { class: "field" }, h("span", { class: "lab" }, "Colours"), pal)));
  go.addEventListener("click", () => goLive(target, go, steps, tin.value, tout.value));
  put(host, h("div", { class: "row", style: { marginTop: "16px" } }, h("span", { class: "lab", style: { fontWeight: 600 } }, "Start with"), startBtn),
    h("div", { style: { marginTop: "12px" } }, go), steps, tU, fU);
  drawSteps(steps, target);
}
const labelOf = (v) => labelForSpec(v, lib.items, lib.pidx, "transition") || v;
const pickLabel = (it, preset) => (preset && preset !== it.preset ? labelForSpec(preset, lib.items, lib.pidx, "scene") : null) || it.title;
function feelHint(f = {}) {
  const parts = [];
  if (f.auto === false) parts.push("auto off");
  if (f.energy) parts.push(FEEL.find(([k]) => k === f.energy)?.[1]);
  if (f.palette) parts.push(f.palette);
  return parts.length ? parts.join(" · ") : "the room's default";
}

function drawSteps(el, target) {
  if (!stepState || stepState.target !== target) { put(el); return; }
  put(el, statusRow(stepState.tone, stepLines(stepState), { icon: stepState.tone === "bad" ? "alert" : null }));
}
/** A step's text plus, when there is one, room's reason and what to try, on a second line. */
export function stepLines(st) {
  return st.sub ? [h("div", {}, st.text), h("div", { class: "meta", tid: "golive-why" }, st.sub)] : st.text;
}
/** "Last try: the TV page couldn't play the live stream · trying again in 2 s" (empty until a try has failed). */
export function whyLine(sum) {
  if (!sum?.why) return "";
  const again = Number.isFinite(sum.retryIn) && sum.retryIn > 0 ? ` · trying again in ${Math.round(sum.retryIn)} s` : "";
  return `${sum.tries > 1 ? `Tried ${sum.tries} times; last` : "Last"} try: ${sum.why}${again}`;
}

export async function goLive(target, btn, stepsEl, tin, tout) {
  const g = G();
  let entry = g.entry?.spec;
  if (!entry) entry = await surprise();
  if (!entry) { toastErr("Couldn't pick a scene to start with"); return; }
  busy(btn, true, "Going live…");
  const body = { desired: { live: { scene: entry, transitions: { in: tin, out: tout } } } };
  const r = await room.putDesired(target, body.desired);
  if (!r.ok) {
    busy(btn, false);
    stepState = { target, tone: "bad", text: errorWords(r, { screen: room.nameOf(target), target: "the room service" }) };
    drawSteps(stepsEl, target);
    return;
  }
  const t0 = Date.now();
  stepState = { target, t0, tone: "warn", text: "Switching to live… 0 s" };
  drawSteps(stepsEl, target);
  const label = (s) => labelForSpec(s, lib.items, lib.pidx);
  await new Promise((resolve) => {
    const tick = setInterval(() => {
      const sc = room.screenOf(target);
      const sum = room.summary(target, label);
      const s = Math.round((Date.now() - t0) / 1000);
      if (sum.live) { stepState = { target, tone: "live", text: sum.text, done: true }; clearInterval(tick); resolve(); }
      else if (sum.problem) { stepState = { target, tone: "bad", text: `Didn't go live: ${roomWhy(sum.detail) || "the room reported a problem"}`, sub: stuckAdvice(sc, room.nameOf(target)) }; clearInterval(tick); resolve(); }
      else if (sc?.desired === "art") { stepState = { target, tone: "mute", text: "Went back to art" }; clearInterval(tick); resolve(); }
      else if (s > 60) {
        // say room's reason and what to try, not just "Waiting to go live…"; room keeps retrying meanwhile
        const why = sum.why || (sum.hint ? sum.hint.replace(/^Can't go live yet: /, "") : "");
        stepState = { target, t0, stuck: true, tone: "bad", text: `Still not live after a minute${why ? `: ${why}` : ""}`, sub: stuckAdvice(sc, room.nameOf(target)) };
        clearInterval(tick); resolve();
      }
      else stepState = { target, t0, tone: "warn", text: `${sum.text === "Showing art" ? "Switching to live…" : sum.text}${sum.hint ? ` ${sum.hint}` : ""} ${s} s`, sub: whyLine(sum) };
      if (stepsEl.isConnected) drawSteps(stepsEl, target);
      else { const e = document.querySelector("[data-testid=golive-steps]"); if (e) drawSteps(e, target); }
    }, 500);
  });
  busy(btn, false);
  if (stepState?.done) {
    const f = g.feel || {};
    const out = {};
    if (f.auto === false) out.auto = { enabled: false };
    const fe = FEEL.find(([k]) => k === f.energy);
    if (fe) out.direction = { energy: fe[2], transition_style: fe[3], source: "panel" };
    if (f.palette) out.palette = f.palette;
    if (Object.keys(out).length) await patchLook(target, out, { silent: true });
  }
}
export const steps = () => stepState;
const roomWhy = (d) => roomErrorWords(d) || d;
