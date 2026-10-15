// The target screen's FX bus, through room (GET / PUT /api/v1/screens/{id}/fx, if_rev with one retry on 409):
// add / remove from Browse, the On screen Effects chips (level, on / off, remove, the slot's error in words),
// Strength, and Effect options (Auto effects from the server's look, density, rate, stages, pulse, follows, fade,
// Clear).  Updated on session.fx events for the screen's session, plus a 5 s safety read while visible.
import { h, put, oneInFlight, emitter, fmt } from "../kit.js";
import { S } from "../store.js";
import * as room from "../data/room.js";
import { onEvent } from "../data/renderer.js";
import { lib } from "../data/library.js";
import { labelForSpec } from "../data/query.js";
import { chip, slider, switchEl, select, unroll, button } from "../ui/controls.js";
import { toast, toastErr } from "../ui/toast.js";
import { errorWords, friendly, STAGE_WORDS } from "../words.js";
import { L, onLook, patch as patchLook, refresh as refreshLook } from "./look.js";

const ev = emitter();
export const bus = { target: null, data: null, at: 0, err: null };
export const onBus = (fn) => ev.on("bus", fn);
let fade = 2;
let fadeOwn = false;        // the person typed a fade here: it stays theirs (the server's fade_s is only the starting value)
const STAGES = ["warp", "feedback", "glitch", "stylise", "colour", "light", "projection", "motion", "overlay"];

export async function refresh(target = S.get().target) {
  if (!room.isReady(room.screenOf(target))) return { ok: false, status: 409, error: "not_live", skipped: true };   // no 409 mid-cut
  const r = await room.fx(target);
  bus.target = target; bus.at = Date.now();
  if (r.ok) { bus.data = r.data; bus.err = null; } else { bus.data = null; bus.err = r; }
  ev.emit("bus", bus);
  return r;
}
const sessionOf = (id) => room.screenOf(id)?.observed?.session || null;
onEvent("session.fx", (e) => {
  const t = S.get().target;
  if (!t || e.session !== sessionOf(t)) return;
  if (bus.data && bus.target === t && e.rev >= (bus.data.rev || 0)) {
    const prev = new Map((bus.data.stack || []).map((s) => [s.id, s]));
    bus.data = { ...bus.data, rev: e.rev, open: e.open ?? bus.data.open, fade_s: e.fade_s ?? bus.data.fade_s,
                 stack: (e.stack || []).map((s) => ({ ...(prev.get(s.id) || {}), ...s })) };
    ev.emit("bus", bus);
  }
  setTimeout(() => refresh(t), 300);
});

export const stack = () => (bus.target === S.get().target ? bus.data?.stack || [] : []);
export const has = (spec) => stack().some((s) => room.sameFx(s, spec));
const slotLabel = (s) => (s.preset ? labelForSpec(s.preset, lib.items, lib.pidx, "effect") : null) || s.label || friendly(s.effect || s.id);

async function mutate(target, fn, what) {
  const r = await room.putFx(target, (cur) => {
    const body = fn(cur);
    if (!body) return null;
    if (body.fade_s == null) body.fade_s = fade;
    if (body.master === undefined) body.master = room.masterOut(cur.master);
    return body;
  });
  if (r.skipped) return r;
  if (!r.ok) { toastErr(errorWords(r, { screen: room.nameOf(target), what, target: "the room service" })); refresh(target); return r; }
  bus.target = target; bus.data = r.data; bus.at = Date.now(); ev.emit("bus", bus);
  for (const rj of r.data?.rejected || []) toastErr(`${friendly(rj.effect || "effect")} was refused: ${rj.detail || rj.error}`);
  return r;
}
const specItem = (spec) => (typeof spec === "string" ? { preset: spec } : { ...spec });

export async function add(target, it, spec, label) {
  const r = await mutate(target, (cur) => {
    if (cur.stack.some((s) => room.sameFx(s, spec))) { toast(`${label || it.title} is already on ${room.nameOf(target)}`); return null; }
    if (cur.stack.length >= 6) { toastErr("The bus holds at most 6 effects: take one off first"); return null; }
    return { stack: [...cur.stack.map(room.fxItem), specItem(spec)] };
  }, "add");
  if (r?.ok && !r.skipped) toast(`Added ${label || it.title} to ${room.nameOf(target)}`);
  return r;
}
export async function removeSpec(target, spec, label = "") {
  const r = await mutate(target, (cur) => ({ stack: cur.stack.filter((s) => !room.sameFx(s, spec)).map(room.fxItem) }), "remove");
  if (r?.ok && !r.skipped) toast(`Took ${label || "it"} off ${room.nameOf(target)}`);
  return r;
}
const removeId = (target, id) => mutate(target, (cur) => ({ stack: cur.stack.filter((s) => s.id !== id).map(room.fxItem) }), "remove");
const setBypass = (target, id, on) => mutate(target, (cur) => ({ stack: cur.stack.map((s) => (s.id === id ? { ...room.fxItem(s), bypass: on || undefined } : room.fxItem(s))) }), "on / off");
const levelSend = oneInFlight(({ target, id, v }) => mutate(target, (cur) => ({ stack: cur.stack.map((s) => (s.id === id ? { ...room.fxItem(s), level: v } : room.fxItem(s))) }), "level"), 200);
const clearAll = (target) => mutate(target, () => ({ stack: [] }), "clear");
const masterSend = oneInFlight(({ target, master }) => mutate(target, (cur) => ({ stack: cur.stack.map(room.fxItem), master }), "strength"), 200);

// ---------------------------------------------------------------- Browse: the one-line FX strip
export function renderStrip(el, target) {
  const st = stack();
  const amt = bus.data?.master?.amount;
  put(el, h("span", { class: "lab" }, `Effects on ${room.nameOf(target).replace(/ screen$/i, "")}:`),
    st.length ? st.map((s) => chip(`${slotLabel(s)}${s.origin === "auto" ? " · auto" : ""}`, { cls: s.origin === "auto" ? "dashed" : "", removable: true,
      removeLabel: `Take ${slotLabel(s)} off`, onRemove: () => removeId(target, s.id), data: { slot: s.id } }))
      : h("span", { class: "muted" }, bus.err ? errorWords(bus.err, { screen: room.nameOf(target) }) : "none yet: add one from the cards"),
    amt != null && typeof amt === "number" ? h("span", { class: "muted" }, `· Strength ${fmt(amt)}`) : null);
}

// ---------------------------------------------------------------- On screen: Effects
export function mountEffects(host, { openPicker } = {}) {
  const target = () => S.get().target;
  let open = null;            // the expanded slot id
  const chipsEl = h("div", { class: "chips" });
  const slotEl = h("div", {});
  const strength = slider("Strength", { tid: "fx-strength", value: 1, onInput: (v) => masterSend({ target: target(), master: masterBody({ amount: v }) }) });
  const status = h("p", { class: "hint" });
  // options
  const autoSw = switchEl("Auto effects", false, (on) => patchLook(target(), { auto: { fx: { enabled: on, max: Number(maxSel.value), pool: "all" } } }),
                          { tid: "fx-auto", sub: "The director adds, swaps and removes effects" });
  const maxSel = select([0, 1, 2, 3, 4].map((n) => [String(n), `At most ${n}`]), "2", (v) => patchLook(target(), { auto: { fx: { enabled: autoSw.sw.get(), max: Number(v), pool: "all" } } }),
                        { tid: "fx-auto-max", aria: "At most this many auto effects" });
  const dirFx = (patch) => patchLook(target(), { direction: { fx: patch } });
  const density = slider("Density", { tid: "fx-density", value: 0.5, onCommit: (v) => dirFx({ density: v }) });
  const rate = slider("Rate", { tid: "fx-rate", value: 0.5, onCommit: (v) => dirFx({ rate: v }) });
  let stages = [];
  const stageChips = h("div", { class: "chips" });
  const drawStages = () => put(stageChips, STAGES.map((s) => chip(STAGE_WORDS[s] || s, { pressed: stages.includes(s), tid: `fx-stage-${s}`,
    onClick: () => { stages = stages.includes(s) ? stages.filter((x) => x !== s) : [...stages, s]; drawStages(); dirFx({ stages: stages.length ? stages : null }); } })));
  drawStages();
  const pulse = slider("Pulse", { tid: "fx-pulse", value: 1, onInput: (v) => masterSend({ target: target(), master: masterBody({ pulse: v }) }) });
  const amtF = select([["", "Fixed"], ["level_slow", "The music's level (slow)"], ["activity_slow", "The music's activity (slow)"]], "", () => masterSend({ target: target(), master: masterBody({}) }), { tid: "fx-amount-follows", aria: "Strength follows" });
  const pulF = select([["", "Fixed"], ["level", "The music's level"], ["beat", "The beat"], ["level_slow", "The music's level (slow)"]], "", () => masterSend({ target: target(), master: masterBody({}) }), { tid: "fx-pulse-follows", aria: "Pulse follows" });
  const fadeIn = h("input", { type: "number", class: "input", min: "0", max: "30", step: "0.5", value: "2", tid: "fx-fade", "aria-label": "Fade seconds" });
  fadeIn.addEventListener("change", () => {
    const v = parseFloat(fadeIn.value);
    fade = isFinite(v) ? Math.max(0, Math.min(30, v)) : 2;
    fadeIn.value = String(fade);
    fadeOwn = true;
  });
  fadeIn.addEventListener("input", () => { const v = parseFloat(fadeIn.value); if (isFinite(v)) { fade = Math.max(0, Math.min(30, v)); fadeOwn = true; } });
  if (fadeOwn) fadeIn.value = String(fade);
  const clearBtn = button("Clear all effects", { kind: "danger", tid: "fx-clear", onClick: () => clearAll(target()) });
  const options = unroll("Effect options", { tid: "fx-options" });
  put(options.body, h("div", { class: "stack" }, autoSw, h("div", { class: "row" }, h("span", { class: "grow" }, "How many"), h("div", { style: { width: "160px" } }, maxSel)),
    density, rate, h("div", { class: "vsec" }, h("h3", {}, "Stages the director may use (none = all)"), stageChips), status,
    pulse, h("label", { class: "field" }, h("span", { class: "lab" }, "Strength follows"), amtF), h("label", { class: "field" }, h("span", { class: "lab" }, "Pulse follows"), pulF),
    h("div", { class: "row" }, h("span", { class: "grow" }, "Fade (seconds) for changes made here"), fadeIn), h("div", {}, clearBtn)));

  function masterBody(over) {
    const val = (k, sel) => {
      const v = over[k] ?? (k === "amount" ? Number(strength.input.value) : Number(pulse.input.value));
      return sel.value ? { bind: sel.value, range: [0, v] } : v;
    };
    return { amount: val("amount", amtF), pulse: val("pulse", pulF) };
  }

  function drawChips() {
    const st = stack();
    put(chipsEl, st.map((s) => {
      const c = chip(`${slotLabel(s)}${s.origin === "auto" ? " · auto" : ""}${s.bypass ? " (off)" : ""}`, { cls: s.origin === "auto" ? "dashed" : "", tid: "fx-chip",
        pressed: open === s.id, data: { slot: s.id }, onClick: () => { open = open === s.id ? null : s.id; drawChips(); } });
      if (s.state === "failed") c.classList.add("tone-bad");
      return c;
    }), chip("Effect", { tid: "fx-add", cls: "dashed", onClick: () => openPicker?.("effect") }));
    chipsEl.lastElementChild.prepend(plusIcon());
    const s = st.find((x) => x.id === open);
    if (!s) { put(slotEl); return; }
    const err = (bus.data?.errors || []).find((x) => x.slot === s.id);
    const lvl = slider("Level", { tid: "fx-level", value: s.level ?? 1, onInput: (v) => levelSend({ target: target(), id: s.id, v }) });
    const byp = switchEl("On", !s.bypass, (on) => setBypass(target(), s.id, !on), { tid: "fx-bypass", sub: "Off fades it out and keeps its place" });
    put(slotEl, h("div", { class: "fx-slot" }, h("div", { class: "row" }, h("strong", { class: "grow" }, slotLabel(s)),
      h("span", { class: "hint" }, `${STAGE_WORDS[s.stage] || s.stage || ""}${s.origin === "auto" ? " · added by Auto" : ""}`)),
      lvl, byp, s.state === "failed" || err ? h("p", { class: "bad-ink" }, `This effect failed: ${err?.detail || "render error"}`) : null,
      button("Remove", { kind: "danger", tid: "fx-remove", onClick: () => { open = null; removeId(target(), s.id); } })));
  }
  function drawMaster() {
    const m = bus.data?.master;
    if (!m) return;
    const sync = (v, sl, sel) => { if (typeof v === "object" && v) { if (document.activeElement !== sel) sel.value = v.bind || ""; sl.set(v.range?.[1] ?? 1); } else { if (document.activeElement !== sel) sel.value = ""; sl.set(v ?? 1); } };
    sync(m.amount, strength, amtF); sync(m.pulse, pulse, pulF);
    // seed from the server until the person sets their own; never overwrite what they typed (a refresh after
    // every PUT, each session.fx event and the 5 s safety read all land here)
    if (!fadeOwn && bus.data?.fade_s != null && document.activeElement !== fadeIn) { fadeIn.value = String(bus.data.fade_s); fade = bus.data.fade_s; }
  }
  function drawAuto() {
    const a = L.data?.auto?.fx;
    if (a) { autoSw.sw.set(!!a.enabled); if (document.activeElement !== maxSel) maxSel.value = String(a.max ?? 2); }
    const d = L.data?.direction?.fx;
    if (d) { density.set(d.density ?? 0.5); rate.set(d.rate ?? 0.5); stages = Array.isArray(d.stages) ? [...d.stages] : []; drawStages(); }
    const ba = bus.data?.auto;
    status.textContent = ba ? `${ba.enabled ? "Auto effects on" : "Auto effects off"}${ba.enabled && ba.next_in_s != null ? ` · next change in ${Math.round(ba.next_in_s)} s` : ""}${ba.last ? ` · last: ${ba.last.op === "add" ? "added" : ba.last.op === "remove" ? "removed" : "swapped in"} ${labelForSpec(ba.last.preset, lib.items, lib.pidx, "effect")}` : ""}` : "";
  }
  put(host, h("div", { class: "os-section" }, h("span", { class: "lab" }, "Effects"), chipsEl, slotEl, strength, options));
  const offs = [onBus(() => { drawChips(); drawMaster(); drawAuto(); }), onLook(() => drawAuto())];
  drawChips(); drawMaster(); drawAuto();
  return { refresh: () => refresh(target()), destroy: () => offs.forEach((f) => f()) };
}
function plusIcon() { const s = document.createElement("span"); s.className = "icon"; s.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" aria-hidden="true"><path d="M5 12h14"/><path d="M12 5v14"/></svg>'; return s; }
export { refreshLook };
