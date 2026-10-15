// The tab's live preview (design §6.3): one reusable 720p session per page load (POST /dev/preview, keyed by a random
// client id), re-cued with a hard cut when the item changes (after a 250 ms settle), a keepalive every 10 s while it
// is shown and the page visible, and a caption that tells the truth from the renderer's events: what is on the glass,
// and why not when the renderer refused or substituted.  The <video> lives in one box that moves between the
// inspector, the Viewer and Review (no re-cue).  Stops: 10 s after it is no longer shown, at once when the page is
// hidden, after 3 min with no activity, and on pagehide.
import { LivePlayer } from "../../live-player.js";
import { h, http, withBase, emitter } from "../kit.js";
import { S } from "../store.js";
import { onEvent } from "../data/renderer.js";
import { lib } from "../data/library.js";
import { errorWords, pluginErrorWords, specName } from "../words.js";
import { labelForSpec } from "../data/query.js";

export const LP_CLIENT = "t" + Math.random().toString(36).slice(2, 12);
const SETTLE_MS = 250, KEEP_MS = 10000, GRACE_MS = 10000, IDLE_MS = 180000, FALLBACK_MS = 1500, TRUTH_MS = 5000;

const ev = emitter();
export const onChange = (fn) => ev.on("change", fn);
const box = h("div", { class: "pvbox", style: { position: "absolute", inset: "0" } });
let player = null, url = null, sid = null, seq = 0;
let want = null;              // {it, spec, kind, label}
let posted = null;            // {seq, it, spec, label, at, prevGlass, confirmed, failed}
let onGlass = null;           // the label of what the preview shows now
let settleT = null, keepT = null, graceT = null, fallbackT = null, tickT = null, idleT = null;
let lastActivity = Date.now();
let tourOn = () => false;
export const setTourProbe = (fn) => { tourOn = fn; };

export const st = { state: "idle", text: "", action: null, key: null, sid: null, startedAt: 0 };
function set(state, text, action = null) {
  Object.assign(st, { state, text, action, key: want?.it?.key || posted?.it?.key || null, sid });
  S.set({ preview: { ...st } });
  ev.emit("change", st);
}
export const video = () => player?.video || null;
export const sessionId = () => sid;
export const shownKey = () => want?.it?.key || null;
export const glass = () => onGlass;

/** Put the playing picture in this host (a .pvhost element). */
export function host(el) {
  if (!el) return;
  if (box.parentNode !== el) el.append(box);
  const v = player?.video;
  if (v) v.play().catch(() => {});
}

function runningWords(w) {
  if (w.kind === "effect") return "On a moving scene (Warp stack)";
  if (w.kind === "transition") return "Looping Warp stack and Fractal zoom through it";
  return "Live preview";
}

/** Show an item (spec: the variation, look or tuned spec).  immediate: no settle (Tune restarts, Reset, Retry). */
export function show(it, spec = it.spec, { immediate = false, label = null } = {}) {
  clearTimeout(graceT); graceT = null;
  want = { it, spec, kind: it.kind, label: label || it.title };
  touch();
  clearTimeout(settleT);
  if (document.visibilityState !== "visible") return;
  if (!player) set("starting", "Starting… 0 s");
  else set("switching", `Loading ${want.label}… still showing ${onGlass || "the last picture"}`);
  settleT = setTimeout(post, immediate ? 0 : SETTLE_MS);
}

async function post() {
  const w = want;
  if (!w) return;
  const my = ++seq;
  const prevGlass = onGlass;
  posted = { seq: my, w, it: w.it, spec: w.spec, kind: w.kind, label: w.label, at: Date.now(), prevGlass, confirmed: false, failed: false };
  if (!player) { st.startedAt = Date.now(); startTick(); }
  const r = await http("POST", "/dev/preview", { client: LP_CLIENT, kind: w.kind, spec: w.spec }, { timeoutMs: 45000 });
  if (my !== seq || want !== w) return;
  stopTick();
  if (!r.ok) {
    if (r.status === 503 || r.error === "too_many_sessions") set("full", "The renderer is full right now. Try again in a moment.", "retry");
    else set("failed", `Preview didn't start: ${errorWords(r)}.`, "retry");
    return;
  }
  sid = r.data?.session || null;
  const att = r.data?.attach || {};
  if (!player || att.url !== url) {
    if (player) player.stop();
    url = att.url;
    player = new LivePlayer({ url: att.url, targetBufferMs: att.target_buffer_ms ?? 300, mount: box, sendStats: false });
    player.start();
    player.video.dataset.pv = "";
    player.video.setAttribute("aria-label", "Live preview");
    player.onFirstFrame(() => confirm(my));
    clearTimeout(fallbackT);
    fallbackT = setTimeout(() => { if (player?.stats().presented > 0) confirm(my); }, 4000);
  } else {
    host(box.parentNode);
    clearTimeout(fallbackT);
    fallbackT = setTimeout(() => confirm(my), FALLBACK_MS);
  }
  startKeep();
}

function confirm(my) {
  if (!posted || posted.seq !== my || posted.failed || posted.confirmed) return;
  posted.confirmed = true;
  onGlass = posted.label;
  // Another item was asked for meanwhile (its settle or POST is still pending): the glass shows this one, but the
  // caption keeps saying what is loading, so it never reads "Live preview" over the wrong item.
  if (want && want !== posted.w) { set("switching", `Loading ${want.label}… still showing ${onGlass}`); return; }
  set("playing", runningWords(posted));
}

function fail(ev) {
  if (!posted || posted.failed) return;
  posted.failed = true;
  clearTimeout(fallbackT);
  const still = posted.prevGlass || "the last picture";
  onGlass = posted.prevGlass;
  if (want && want !== posted.w) { set("switching", `Loading ${want.label}… still showing ${still}`); return; }   // not the one asked for now
  const w = pluginErrorWords(ev);
  if (/too slow/.test(w)) set("failed", `${w[0].toUpperCase()}${w.slice(1)} — still showing ${still}`);
  else set("failed", `This one failed to start — still showing ${still}. About shows the error.`);
}

onEvent("session.transition", (e) => {
  if (!sid || e.session !== sid || e.phase !== "started" || !posted || posted.confirmed || posted.failed) return;
  if (posted.kind === "scene") {
    const want = specName(posted.spec);
    const names = new Set([want, posted.it.plugin, posted.it.name, ...(posted.it.presets || []).map((p) => p.name)]);
    if (e.scene && !names.has(e.scene)) return;               // a recovery cue to the previous scene is not ours
  }
  const my = posted.seq;                                         // this post, not whichever is current in 150 ms
  setTimeout(() => confirm(my), 150);
});
onEvent("session.fx", (e) => {
  if (!sid || e.session !== sid || !posted || posted.kind !== "effect" || posted.confirmed) return;
  confirm(posted.seq);
});
onEvent("session.plugin_error", (e) => {
  if (!sid || e.session !== sid || !posted) return;
  if (Date.now() - posted.at > TRUTH_MS + 30000) return;
  const plugin = posted.it.plugin;
  if (e.name !== plugin) return;
  fail(e);
});
onEvent("session.state", (e) => {
  if (!sid || e.session !== sid) return;
  if (["ended", "failed"].includes(e.state)) { sid = null; url = null; if (player) { player.stop(); player = null; } }
});

function startTick() {
  stopTick();
  tickT = setInterval(() => {
    if (st.state !== "starting") return stopTick();
    const s = Math.round((Date.now() - st.startedAt) / 1000);
    set("starting", s >= 10 ? "Still starting, the renderer is busy" : `Starting… ${s} s`);
  }, 1000);
}
function stopTick() { clearInterval(tickT); tickT = null; }

function startKeep() {
  clearInterval(keepT);
  keepT = setInterval(async () => {
    if (!want || document.visibilityState !== "visible") return;
    const r = await http("POST", "/dev/preview", { client: LP_CLIENT, keepalive: true });
    if (r.ok && r.data && r.data.session == null && want) {   // it ended under us (TTL): start again
      sid = null; url = null; if (player) { player.stop(); player = null; }
      show(want.it, want.spec, { immediate: true, label: want.label });
    }
  }, KEEP_MS);
}

function del({ keepalive = false } = {}) {
  clearTimeout(graceT); graceT = null;
  clearInterval(keepT); keepT = null;
  clearTimeout(fallbackT);
  if (player) { player.stop(); player = null; url = null; }
  const had = sid;
  sid = null; onGlass = null; posted = null; seq++;
  try { fetch(withBase(`/dev/preview?client=${LP_CLIENT}`), { method: "DELETE", keepalive, cache: "no-store" }).catch(() => {}); } catch (_) {}
  return had;
}

/** The item is no longer shown: stop the player now, end the session after a 10 s grace (cancelled by show()). */
export function release() {
  clearTimeout(settleT); stopTick();
  want = null;
  if (player) { player.stop(); player = null; url = null; }
  clearInterval(keepT); keepT = null;
  seq++; posted = null; onGlass = null;
  set("idle", "");
  clearTimeout(graceT);
  graceT = setTimeout(() => del(), GRACE_MS);
}
export function retry() { if (want) show(want.it, want.spec, { immediate: true, label: want.label }); }
export function resume() { touch(); if (want) show(want.it, want.spec, { immediate: true, label: want.label }); }

// ---------------------------------------------------------------- idle, visibility, page end
function touch() { lastActivity = Date.now(); }
for (const t of ["pointerdown", "keydown", "wheel", "touchstart"]) window.addEventListener(t, () => { lastActivity = Date.now(); }, { passive: true, capture: true });
window.addEventListener("pointermove", () => { lastActivity = Date.now(); }, { passive: true });
idleT = setInterval(() => {
  if (!want || st.state === "paused" || tourOn()) return;
  if (Date.now() - lastActivity >= IDLE_MS) {
    del();
    set("paused", "Preview paused to free the GPU.", "resume");
  }
}, 1000);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState !== "visible") {
    if (sid || player) { del(); if (want) set("paused", "Preview paused while this page is hidden.", "resume"); }
  } else if (want && (st.state === "paused" || !player)) {
    lastActivity = Date.now();
    show(want.it, want.spec, { immediate: true, label: want.label });
  }
});
window.addEventListener("pagehide", () => { if (sid || graceT) del({ keepalive: true }); });

/** A label for a spec on this preview (variation names). */
export function specLabel(it, spec) {
  if (typeof spec === "string" && spec !== it.preset) return labelForSpec(spec, lib.items, lib.pidx, it.kind);
  return it.title;
}
