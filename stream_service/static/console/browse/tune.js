// Tune: the item's parameters, live on the tab's preview session only (design §4 #29-31).  A scene PATCHes the
// preview session, an effect PUTs its preview FX slot, a transition re-POSTs the preview.  Shuffle (around the
// preset), Nudge (near the current values), Back (60 steps), Tour (shuffle every 5 s, 8 s for a transition), Reset,
// Save look.  To put the tuned result on a screen, Play or Show on carry it as the spec ("With your changes").
import { h, put, http } from "../kit.js";

import * as catalog from "../data/catalog.js";
import * as preview from "./preview.js";
import { rate } from "../data/ratings.js";
import { paramForm, isColor, restarts } from "../ui/paramform.js";
import { button } from "../ui/controls.js";
import { toast, toastErr } from "../ui/toast.js";
import { errorWords } from "../words.js";

const VARY_SKIP = new Set(["amount", "render_scale", "duration_s"]);
const TOUR_MS = { scene: 5000, effect: 5000, transition: 8000 };
const FX_IDS = ["pv", "pv1"];
const rnd = Math.random;
const gauss = () => (rnd() + rnd() + rnd() - 1.5) * 2;

export const T = { key: null, it: null, variant: null, props: null, bound: new Set(), cur: {}, base: {}, hist: [], touched: new Set(),
                   tour: null, activeLook: null, fxId: FX_IDS[0], saving: false, loading: null };
const listeners = new Set();
export const onTune = (fn) => { listeners.add(fn); return () => listeners.delete(fn); };
const changed = () => listeners.forEach((f) => { try { f(T); } catch (e) { console.error(e); } });
preview.setTourProbe(() => !!T.tour);

const variantOf = (it, v) => v || (typeof it.spec === "string" ? it.spec : it.preset) || null;
const specObj = (it, variant) => (variant ? { preset: variant } : Object.fromEntries(Object.entries(typeof it.spec === "object" ? it.spec : {}).filter(([k]) => k !== "params")));
const isBind = (v) => v && typeof v === "object" && ("bind" in v || "signal" in v);

/** Reset Tune for the item + variation on show (no network until load()). */
export function reset(it, variant) {
  tourStop();
  Object.assign(T, { key: it?.key || null, it, variant: variantOf(it || {}, variant), props: null, bound: new Set(), cur: {}, base: {}, hist: [],
                     touched: new Set(), activeLook: null, fxId: FX_IDS[0], saving: false, loading: null });
  changed();
}

/** Load the schema (lazy catalog) and the variation's values. */
export function load() {
  if (T.props || !T.it) return Promise.resolve(T.props);
  if (T.loading) return T.loading;
  const it = T.it, key = T.key, variant = T.variant;
  T.loading = (async () => {
    const c = await catalog.ready();
    if (T.key !== key || T.variant !== variant) return null;
    if (!c) { toastErr("Couldn't load the parameters (the renderer's catalog)"); T.loading = null; return null; }
    const props = catalog.propsFor(it.kind, it.plugin, c);
    const pre = variant ? (it.kind === "scene" ? c.presets?.scenes : it.kind === "transition" ? c.presets?.transitions : c.presets?.effects)?.[variant] : null;
    const pv = pre?.params || {};
    const cur = {}, bound = new Set();
    for (const [k, p] of Object.entries(props)) {
      const v = pv[k];
      if (isBind(v)) { bound.add(k); cur[k] = v.rest ?? p.default; }
      else cur[k] = v ?? p.default;
    }
    if (it.kind === "effect" && "amount" in props) cur.amount = 1;
    Object.assign(T, { props, bound, cur, base: { ...cur }, loading: null });
    changed();
    return props;
  })();
  return T.loading;
}

export function ownParams() {
  if (!T.props) return {};
  return Object.fromEntries(Object.keys(T.props)
    .filter((k) => T.cur[k] !== undefined && (T.touched.has(k) || (!T.bound.has(k) && T.props[k]["x-tier"] !== "quality")))
    .map((k) => [k, T.cur[k]]));
}
/** The spec for Play / Show on / Add: the tuned values, else the chosen look, else the variation. */
export function currentSpec(it, variant) {
  const v = variantOf(it, variant);
  if (T.key === it.key && T.variant === v && T.touched.size) {
    const own = Object.fromEntries([...T.touched].map((k) => [k, T.cur[k]]));
    return { spec: { ...specObj(it, v), params: own }, note: T.activeLook && !T.dirtySinceLook ? `With look: ${T.activeLook}` : `With your changes: ${T.touched.size} setting${T.touched.size > 1 ? "s" : ""}`, tuned: true };
  }
  return { spec: v || it.spec, note: null, tuned: false };
}

// ---------------------------------------------------------------- senders
let pend = {}, sendT = null;
function flushSoon(ms) { clearTimeout(sendT); sendT = setTimeout(flush, ms); }
async function flush() {
  const it = T.it, p = pend; pend = {};
  if (!it || !T.props || !Object.keys(p).length) return;
  const sid = preview.sessionId();
  const restart = Object.keys(p).some((k) => restarts(T.props[k]));
  const own = Object.fromEntries([...T.touched].filter((k) => T.cur[k] !== undefined).map((k) => [k, T.cur[k]]));
  let r = { ok: true };
  if (it.kind === "scene") {
    if (restart || !sid) { preview.show(it, { ...specObj(it, T.variant), params: own }, { immediate: true }); return; }
    r = await http("PATCH", `/api/sessions/${sid}`, { params: p });
  } else if (it.kind === "effect") {
    if (!sid) return;
    if (restart) T.fxId = T.fxId === FX_IDS[0] ? FX_IDS[1] : FX_IDS[0];
    if ("amount" in T.cur) own.amount = T.cur.amount;
    r = await http("PUT", `/api/sessions/${sid}/fx`, { stack: [{ id: T.fxId, ...specObj(it, T.variant), params: own }], fade_s: 0 });
  } else {
    preview.show(it, { ...specObj(it, T.variant), params: own }, { immediate: true });
    return;
  }
  if (!r.ok) toastErr(`Tune: ${errorWords(r)}`);
}
function send(k, v) { pend[k] = v; flushSoon(T.it?.kind === "transition" ? 400 : 60); }
function sendMany(o) { Object.assign(pend, o); flushSoon(T.it?.kind === "transition" ? 50 : 0); }

// ---------------------------------------------------------------- variations
function varyKeys() {
  const { props, bound } = T;
  return Object.keys(props || {}).filter((k) => {
    const p = props[k];
    if (VARY_SKIP.has(k) || p["x-tier"] === "quality" || bound.has(k)) return false;
    return p.type === "boolean" || Array.isArray(p.enum) || p.type === "number" || p.type === "integer" || isColor(p);
  });
}
function varyNum(p, from, base, near) {
  const lo = p.minimum ?? 0, hi = p.maximum ?? 1;
  if (!(hi > lo)) return from;
  const log = lo > 0 && hi / lo >= 20;
  const f = log ? Math.log : (x) => x, g = log ? Math.exp : (x) => x;
  const L = f(lo), H = f(hi), span = H - L;
  const at = (v) => f(Math.min(hi, Math.max(lo, Number.isFinite(Number(v)) ? Number(v) : (p.default ?? lo))));
  const x = near ? at(from) + gauss() * 0.08 * span : at(base) + (L + rnd() * span - at(base)) * (0.35 + 0.65 * rnd());
  let v = g(Math.min(H, Math.max(L, x)));
  const step = p["x-ui"]?.step;
  if (p.type === "integer") v = Math.round(v);
  else { if (step) v = Math.round(v / step) * step; v = Number(v.toPrecision(6)); }
  return Math.min(hi, Math.max(lo, v));
}
function varyColor(hex, near) {
  const n = parseInt(String(hex || "#ffffff").slice(1, 7), 16) || 0;
  const [r, gr, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((c) => c / 255);
  const mx = Math.max(r, gr, b), mn = Math.min(r, gr, b), d = mx - mn;
  let hh = d === 0 ? 0 : mx === r ? ((gr - b) / d) % 6 : mx === gr ? (b - r) / d + 2 : (r - gr) / d + 4;
  hh = (hh / 6 + 1) % 1;
  let sat = mx ? d / mx : 0, val = mx;
  if (near) { hh = (hh + gauss() * 0.04 + 1) % 1; sat = Math.min(1, Math.max(0, sat + gauss() * 0.06)); val = Math.min(1, Math.max(0.1, val + gauss() * 0.06)); }
  else { hh = rnd(); sat = 0.45 + 0.55 * rnd(); val = 0.55 + 0.45 * rnd(); }
  const k = (m) => (m + hh * 6) % 6, c = (m) => val - val * sat * Math.max(0, Math.min(k(m), 4 - k(m), 1));
  return "#" + [c(5), c(3), c(1)].map((x) => Math.round(x * 255).toString(16).padStart(2, "0")).join("");
}
function variation(near) {
  const { props, cur, base } = T;
  const keys = varyKeys();
  const next = {};
  const odds = near ? { num: 1, pick: 0.12, flag: 0.08, color: 0.5 } : { num: 0.7, pick: 0.5, flag: 0.35, color: 0.7 };
  for (const k of keys) {
    const p = props[k], from = cur[k], b = base[k] ?? p.default;
    if (/(^|_)seed$/.test(k)) {
      if (!near) { const lo = p.minimum ?? 0, hi = Math.min(p.maximum ?? 1e6, lo + 2 ** 31 - 1); next[k] = lo + Math.floor(rnd() * (hi - lo + 1)); }
    } else if (Array.isArray(p.enum)) { if (rnd() < odds.pick && p.enum.length > 1) next[k] = p.enum.filter((x) => x !== from)[Math.floor(rnd() * (p.enum.length - 1))]; }
    else if (p.type === "boolean") { if (rnd() < odds.flag) next[k] = !from; }
    else if (isColor(p)) { if (rnd() < odds.color) next[k] = varyColor(near ? from : b, near); }
    else if (rnd() < odds.num) next[k] = varyNum(p, from, b, near);
  }
  const pickable = keys.filter((k) => !(near && /(^|_)seed$/.test(k)));
  if (!Object.keys(next).length && pickable.length) {
    const k = pickable[Math.floor(rnd() * pickable.length)], p = props[k];
    next[k] = Array.isArray(p.enum) ? p.enum.find((x) => x !== cur[k]) ?? cur[k] : p.type === "boolean" ? !cur[k]
      : isColor(p) ? varyColor(cur[k], near) : varyNum(p, cur[k], base[k] ?? p.default, near);
  }
  return next;
}
export function canVary() { return !!T.props && varyKeys().length > 0; }

export function applyVals(next, { look = null } = {}) {
  if (!T.props) return false;
  const diff = {};
  for (const [k, v] of Object.entries(next || {})) if (k in T.props && T.cur[k] !== v) diff[k] = v;
  if (!Object.keys(diff).length) { if (look) { T.activeLook = look; T.dirtySinceLook = false; changed(); } return false; }
  T.hist.push({ ...T.cur });
  if (T.hist.length > 60) T.hist.shift();
  Object.assign(T.cur, diff);
  for (const k of Object.keys(diff)) { T.touched.add(k); T.bound.delete(k); }
  T.activeLook = look; T.dirtySinceLook = false;
  sendMany(diff);
  changed();
  return true;
}
export async function vary(mode) {
  await load();
  if (!T.props) return;
  if (!canVary()) { toast("Nothing here to vary"); return; }
  if (!applyVals(variation(mode === "near"))) toast("No change: try again");
}
export function back() {
  if (!T.hist.length) return;
  tourStop();
  const prev = T.hist.pop(), diff = {};
  for (const [k, v] of Object.entries(prev)) if (T.cur[k] !== v) diff[k] = v;
  Object.assign(T.cur, diff);
  if (Object.keys(diff).length) sendMany(diff);
  changed();
}
export async function tourToggle() {
  if (T.tour) { tourStop(); changed(); return; }
  await load();
  if (!canVary()) return;
  await vary("wide");
  const kind = T.it?.kind;
  T.tour = setInterval(() => { if (document.visibilityState === "visible" && T.props) vary("wide"); }, TOUR_MS[kind] || 5000);
  changed();
}
export function tourStop() { if (T.tour) { clearInterval(T.tour); T.tour = null; } }
export function resetToPreset() {
  if (!T.it) return;
  tourStop();
  const it = T.it, variant = T.variant;
  Object.assign(T, { cur: { ...T.base }, hist: [], touched: new Set(), activeLook: null });
  preview.show(it, variant || it.spec, { immediate: true });
  changed();
}
export function nextLookName(it) {
  const used = new Set((it._looks || []).map((l) => l.name));
  let n = (it._looks?.length || 0) + 1;
  while (used.has(`look ${n}`)) n++;
  return `look ${n}`;
}
export function startSave() { load().then(() => { tourStop(); T.saving = true; changed(); }); }
export function cancelSave() { T.saving = false; changed(); }
export async function saveLook(it, name) {
  name = String(name || "").trim().slice(0, 40) || nextLookName(it);
  await load();
  const params = ownParams();
  if (it.kind === "effect") delete params.amount;
  const others = (it._looks || []).filter((l) => l.name !== name);
  if (others.length >= 24) { toastErr("24 looks is the limit: delete one first"); return false; }
  T.saving = false;
  const ok = await rate(it, { looks: [...others, { name, params }] });
  if (ok) { T.activeLook = name; T.dirtySinceLook = false; toast(`Saved the look "${name}"`); }
  changed();
  return ok;
}
export async function applyLook(it, look) {
  await load();
  if (T.key !== it.key) return;
  tourStop();
  if (!applyVals(look.params || {}, { look: look.name })) { T.activeLook = look.name; changed(); }
}

// ---------------------------------------------------------------- the panel
const keepOpen = { more: false, engine: false };
export function renderPanel(host, it, { failed = null } = {}) {
  const box = h("div", { class: "tune", tid: "tune-panel" });
  if (failed) { put(host, h("div", { class: "status tone-warn" }, h("span", { class: "dot", style: { marginTop: "7px" } }), h("div", { class: "grow" }, `Tune is off: ${failed}`))); return; }
  if (!T.props) {
    put(host, h("div", { class: "tune", tid: "tune-panel" }, h("p", { class: "hint" }, "Loading the settings…")));
    load();
    return;
  }
  const canV = canVary();
  const tool = (label, ic, tid, fn, { off = false, pressed = null, title } = {}) => {
    const b = button(label, { kind: "outline", icon: ic, tid, onClick: fn, disabled: off, title });
    if (pressed != null) b.setAttribute("aria-pressed", String(pressed));
    return b;
  };
  const tools = h("div", { class: "tools" },
    tool("Shuffle", "shuffle", "tune-shuffle", () => { tourStop(); vary("wide"); }, { off: !canV, title: "Try a random variation around the preset (r)" }),
    tool("Nudge", "nudge", "tune-nudge", () => { tourStop(); vary("near"); }, { off: !canV, title: "A small change from here (n)" }),
    tool("Back", "undo", "tune-back", () => back(), { off: !T.hist.length, title: "Back to the values before the last change (b)" }),
    tool(T.tour ? "Stop tour" : "Tour", T.tour ? "pause" : "play", "tune-tour", () => tourToggle(), { off: !canV, pressed: !!T.tour, title: "Keep shuffling every few seconds (p)" }),
    tool("Save look", "save", "tune-save", () => startSave(), { title: "Save these values as a look (s)" }));
  const head = h("div", { class: "row", style: { justifyContent: "space-between", marginBottom: "6px" } },
    h("span", { class: "hint" }, it.kind === "transition" ? "Changes show on the next run" : "Changes show live in the preview"),
    button("Reset", { kind: "ghost", tid: "tune-reset", onClick: () => resetToPreset(), title: "Back to the variation's values" }));
  let nameIn = null;
  if (T.saving) {
    nameIn = h("input", { class: "input sm", tid: "look-name-input", "aria-label": "Name for this look", maxlength: "40", autocomplete: "off" });
    nameIn.value = nextLookName(it);
    nameIn.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); saveLook(it, nameIn.value); }
      else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); cancelSave(); }
    });
  }
  const rows = h("div", { class: "rows" });
  paramForm(rows, T.props, T.cur, (k, v) => {
    tourStop();
    if (T.cur[k] === v) return;
    T.hist.push({ ...T.cur });
    if (T.hist.length > 60) T.hist.shift();
    T.cur[k] = v; T.touched.add(k); T.bound.delete(k);
    if (T.activeLook) T.dirtySinceLook = true;
    send(k, v);
    changesLine();
  }, { bound: T.bound, keepOpen });
  const changes = h("div", { class: "changes" });
  const changesLine = () => {
    const n = T.touched.size;
    put(changes, n ? [`With your changes: ${n} setting${n > 1 ? "s" : ""}`, button("Save as a look", { kind: "quiet", cls: "btn-sm", onClick: () => startSave() })] : null);
  };
  changesLine();
  put(box, head, tools, nameIn ? h("div", { class: "row" }, h("label", { class: "field grow" }, h("span", { class: "lab" }, "Name this look (Enter saves, Esc cancels)"), nameIn)) : null, rows, changes);
  put(host, box);
  if (nameIn) requestAnimationFrame(() => { nameIn.focus(); nameIn.select(); });
}
