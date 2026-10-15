// The live state of a room screen: Change scene (the Picker + transition + timing) and Back to art, plug-in problems,
// "What the director did", Next change, Auto + Next, the four Feel chips and "More about the feel" (incl. Wander),
// Colours, Effects, Scene settings (room PATCH /live {params}) and Watch here.  Everything goes through room's
// per-screen API, so room's look records it; controls show the server's values.
import { h, put, oneInFlight, http, fmt, mediaUrl } from "../kit.js";
import { S } from "../store.js";
import * as room from "../data/room.js";
import * as catalog from "../data/catalog.js";
import { onEvent } from "../data/renderer.js";
import { lib, pluginTitle } from "../data/library.js";
import { labelForSpec } from "../data/query.js";
import { LivePlayer } from "../../live-player.js";
import { button, chip, select, switchEl, unroll, slider, busy } from "../ui/controls.js";
import { paramForm } from "../ui/paramform.js";
import { toast, toastErr } from "../ui/toast.js";
import { errorWords, directorWords, pluginErrorWords, friendly, specName } from "../words.js";
import { L, onLook, patch as patchLook, refresh as refreshLook } from "./look.js";
import { renderStrip, cueBody } from "./compose.js";
import { mountEffects } from "./fx.js";
import { FEEL } from "./golive.js";
import { backToArt } from "./push.js";

const label = (s) => labelForSpec(s, lib.items, lib.pidx);
const sessionOf = (id) => room.screenOf(id)?.observed?.session || null;

// ---------------------------------------------------------------- what the director did / plug-in problems (kept per session)
export const did = new Map();        // session -> [{t, text}]
export const problems = new Map();   // session -> [ev]
const dismissed = new Map();         // session -> count dismissed
const listeners = new Set();
const notify = () => listeners.forEach((f) => { try { f(); } catch (_) {} });
function addDid(sid, text) {
  if (!sid || !text) return;
  const a = did.get(sid) || [];
  a.push({ t: Date.now(), text });
  while (a.length > 30) a.shift();
  did.set(sid, a);
  notify();
}
onEvent("*", (ev) => {
  if (!ev?.session || String(ev.screen || "").startsWith("dev-preview-")) return;
  if (ev.type === "session.plugin_error") {
    const a = problems.get(ev.session) || [];
    const prev = a.find((x) => x.name === ev.name && x.kind === ev.kind);
    if (prev) Object.assign(prev, ev); else a.push({ ...ev });
    problems.set(ev.session, a);
  }
  const w = directorWords(ev, (s) => label(s), (n) => pluginTitle(n) || friendly(n));
  if (w && (ev.type !== "session.transition" || ev.phase === "started")) addDid(ev.session, w);
  else if (ev.type === "session.plugin_error") notify();
});

// ---------------------------------------------------------------- the live section
let wanderT = null, wanderOn = false;
export function renderLive(host, target, { openPicker }) {
  const name = room.nameOf(target);
  const sid = sessionOf(target);
  // primary
  const change = button("Change scene", { kind: "primary", big: true, tid: "change-scene-btn", onClick: () => openPicker("scene", {
    title: `Change the scene on ${name.replace(/ screen$/i, "")}`, footer: "change",
    onPick: async (it, preset) => {
      const r = await room.cue(target, cueBody(preset || it.spec));
      if (r.ok) toast(`${it.title} → ${name}`); else toastErr(errorWords(r, { screen: name, target: "the room service" }));
    } }) });
  const stop = button("Back to art", { kind: "danger", big: true, tid: "backtoart-btn", onClick: async () => {
    busy(stop, true, "Going back…");
    await backToArt(target);
    busy(stop, false);
  } });
  const acts = h("div", { class: "acts" }, change, stop);
  // plug-in problems
  const probEl = h("div", {});
  const drawProblems = () => {
    const a = problems.get(sessionOf(target)) || [];
    const n = a.length - (dismissed.get(sessionOf(target)) || 0);
    if (n <= 0) { put(probEl); return; }
    let open = false;
    const list = h("ul", { hidden: true, style: { margin: "6px 0 0", paddingLeft: "18px" } }, a.slice(-8).map((e) => h("li", {}, `${pluginTitle(e.name) || friendly(e.name)}: ${pluginErrorWords(e)}${e.count > 1 ? ` (×${e.count})` : ""}`)));
    const show = button("Show", { kind: "quiet", cls: "btn-sm", onClick: () => { open = !open; list.hidden = !open; show.querySelector(".label").textContent = open ? "Hide" : "Show"; } });
    const dis = button("Dismiss", { kind: "quiet", cls: "btn-sm", onClick: () => { dismissed.set(sessionOf(target), a.length); drawProblems(); } });
    put(probEl, h("div", { class: "status tone-warn", tid: "plugin-problem", style: { marginTop: "12px" } }, h("span", { class: "dot", style: { marginTop: "7px" } }),
      h("div", { class: "grow" }, `${n} plug-in problem${n > 1 ? "s" : ""}, the show kept going`, list), show, dis));
  };
  // what the director did
  const didU = unroll("What the director did", { tid: "director-did" });
  const drawDid = () => {
    const a = (did.get(sessionOf(target)) || []).slice(-10).reverse();
    didU.setHint(a[0]?.text || "nothing yet");
    put(didU.body, a.length ? h("ul", { class: "dirlog" }, a.map((x) => h("li", {}, h("span", { class: "t" }, new Date(x.t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })), h("span", {}, x.text))))
      : h("p", { class: "hint" }, "Scene changes show here, in words."));
  };
  // next change
  const next = h("div", { class: "strip" });
  const drawNext = () => renderStrip(next, { target, tid: "next-change", showName: false, label: false });
  // auto + next
  const autoSw = switchEl("Auto picks scenes", true, (on) => patchLook(target, { auto: { enabled: on } }), { tid: "auto-switch", sub: "" });
  const autoNext = button("Next", { kind: "outline", icon: "skip", tid: "auto-next", onClick: async () => {
    const r = await room.cue(target, { to: "auto", at: L.data?.auto?.snap || "beat" });
    if (!r.ok) toastErr(errorWords(r, { screen: name, target: "the room service" })); else toast("Changing to the director's next pick");
  } });
  // feel
  const feelChips = FEEL.map(([k, l, e, st]) => chip(l, { tid: `feel-${k}`, onClick: () => patchLook(target, { direction: { energy: e, transition_style: st, source: "panel" } }) }));
  const feel = h("div", { class: "feel" }, feelChips);
  // more about the feel
  const more = unroll("More about the feel", { tid: "feel-more" });
  const dir = (patch) => patchLook(target, { direction: { ...patch, source: "panel" } });
  const sl = {
    energy: slider("Energy", { tid: "dir-energy", onCommit: (v) => dir({ energy: v }) }),
    pace: slider("Pace", { tid: "dir-pace", onCommit: (v) => dir({ pace: v }) }),
    intensity: slider("Music response", { tid: "dir-intensity", title: "How far the music moves the picture", onCommit: (v) => dir({ intensity: v }) }),
    change_rate: slider("Change rate", { tid: "dir-change_rate", onCommit: (v) => dir({ change_rate: v }) }),
  };
  const v = catalog.get()?.vocab || {};
  const style = select([["", "Any"], ...(v.transition_style || []).map((s) => [s, friendly(s)])], "", (x) => dir({ transition_style: x || null }), { tid: "dir-style", aria: "Transition style" });
  const genre = select([["", "Any"], ...(v.genre || []).map((s) => [s, friendly(s)])], "", (x) => dir({ genre: x || null }), { tid: "dir-genre", aria: "Genre" });
  let moods = [];
  const moodBox = h("div", { class: "chips" });
  const drawMoods = () => put(moodBox, (v.moods || []).map((m) => chip(friendly(m), { tid: `dir-mood-${m}`, pressed: moods.includes(m),
    onClick: () => { moods = moods.includes(m) ? moods.filter((x) => x !== m) : [...moods, m]; drawMoods(); dir({ moods }); } })));
  drawMoods();
  const dmin = h("input", { type: "number", class: "input", min: "2", max: "3600", tid: "dwell-min", "aria-label": "Shortest stay (s)" });
  const dmax = h("input", { type: "number", class: "input", min: "2", max: "3600", tid: "dwell-max", "aria-label": "Longest stay (s)" });
  const sendDwell = () => {
    const lo = parseFloat(dmin.value), hi = parseFloat(dmax.value);
    patchLook(target, { auto: { dwell_s: Number.isFinite(lo) && Number.isFinite(hi) && hi >= lo ? [lo, hi] : null } });
  };
  dmin.addEventListener("change", sendDwell); dmax.addEventListener("change", sendDwell);
  const followFx = switchEl("Follows the lighting desk's effect", true, () => sendFollow(), { tid: "follow-effect" });
  const followVar = switchEl("Follows the lighting desk's variant", true, () => sendFollow(), { tid: "follow-variant" });
  const sendFollow = () => patchLook(target, { auto: { follow_dmx: [followFx.sw.get() ? "effect" : null, followVar.sw.get() ? "variant" : null].filter(Boolean) } });
  const heavyNote = lib.items.some((i) => i.heavy === true) ? "" : "no scene is marked heavy yet";
  const skipHeavy = switchEl("Skip heavy scenes", true, (on) => patchLook(target, { auto: { pool: on ? { tags: { heavy: false } } : "all" } }), { tid: "skip-heavy", sub: heavyNote });
  const snap = h("div", { class: "seg", role: "group", "aria-label": "Changes happen" },
    ["beat", "now"].map((s) => { const b = h("button", { type: "button", tid: `snap-${s}`, "aria-pressed": "false" }, s === "beat" ? "On the beat" : "At once"); b.addEventListener("click", () => patchLook(target, { auto: { snap: s } })); return b; }));
  const wander = switchEl("Wander", wanderOn, (on) => { wanderOn = on; scheduleWander(target); }, { tid: "wander-switch", sub: "Nudges the feel every 10–30 s while this page is open" });
  const dirStatus = h("p", { class: "hint" });
  // the music steers the feel (music_direction.py): the sliders are your trim around it; ♪ marks what it is moving
  const musicSw = switchEl("Music steers the feel", true, (on) => patchLook(target, { auto: { music: { enabled: on } } }),
    { tid: "music-steers", sub: "From the lighting desk: busier music, busier pictures" });
  const musicW = slider("How much", { tid: "music-weight", value: 1, fmt: (v) => `${Math.round(v * 100)}%`, title: "How far the music moves the feel",
    onCommit: (v) => patchLook(target, { auto: { music: { weight: +v.toFixed(2) } } }) });
  const musicLine = h("p", { class: "hint", tid: "music-line" });
  const notes = {};
  for (const k of Object.keys(sl)) {
    notes[k] = h("span", { class: "gl", tid: `music-note-${k}`, title: "The music moves this: the slider is your trim around it" });
    sl[k].querySelector(".name").append(notes[k]);
  }
  put(more.body, h("div", { class: "stack" }, musicSw, musicW, musicLine, sl.energy, sl.pace, sl.intensity, sl.change_rate,
    h("div", { class: "fields" }, h("label", { class: "field" }, h("span", { class: "lab" }, "Transition style"), style), h("label", { class: "field" }, h("span", { class: "lab" }, "Genre"), genre)),
    h("div", { class: "vsec" }, h("h3", {}, "Moods"), moodBox),
    h("div", {}, h("span", { class: "lab" }, "How long each scene stays (seconds)"), h("div", { class: "row" }, dmin, h("span", {}, "to"), dmax)),
    followFx, followVar, skipHeavy, h("div", { class: "row" }, h("span", { class: "grow" }, "Changes happen"), snap), wander, dirStatus));
  // colours
  const pals = h("div", { class: "palettes", role: "group", "aria-label": "Colours" });
  const drawPals = () => {
    const cur = L.data?.palette ?? null;
    const P = catalog.get()?.palettes || {};
    put(pals, [[null, "Scene's own", ["#d6d3d1", "#a8a29e", "#78716c", "#a8a29e", "#d6d3d1"]], ...Object.entries(P).map(([n, p]) => [n, p.label || n, p.swatch || []])].map(([n, l, sw]) => {
      const b = h("button", { type: "button", class: "pal", tid: `palette-${n ?? "own"}`, "aria-pressed": String(cur === n) }, h("span", { class: "sw" }, sw.map((c) => h("span", { style: { background: c } }))), l);
      b.addEventListener("click", () => patchLook(target, { palette: n }));
      return b;
    }));
  };
  // effects
  const fxHost = h("div", {});
  const fxCtl = mountEffects(fxHost, { openPicker: (kind) => openPicker("effect", { title: `Add an effect to ${name.replace(/ screen$/i, "")}`, onPick: async (it, preset) => {
    const { add } = await import("./fx.js");
    add(target, it, preset || it.spec, it.title);
  } }) });
  // scene settings
  const ssU = unroll("Scene settings", { tid: "scene-settings" });
  let ssScene = null;
  const liveSend = oneInFlight((params) => room.patchLive(target, { params }).then((r) => { if (!r.ok) toastErr(errorWords(r, { screen: name, target: "the room service" })); }), 200);
  let pendParams = {};
  const drawScene = async () => {
    if (!ssU.open) return;
    const cur = L.data?.current_scene || specName(L.data?.scene);
    if (!cur) { put(ssU.body, h("p", { class: "hint" }, "Waiting for the screen's scene…")); return; }
    if (ssScene === cur && ssU.body.querySelector("[data-param]")) return;
    ssScene = cur;
    const c = await catalog.ready();
    const pre = c?.presets?.scenes?.[cur];
    const src = pre?.source || cur;
    const props = catalog.propsFor("scene", src, c);
    const bound = new Set(Object.keys(L.data?.scene_bindings || {}).concat(Object.entries(pre?.params || {}).filter(([, x]) => x && typeof x === "object" && "bind" in x).map(([k]) => k)));
    const host2 = h("div", { class: "pform" });
    paramForm(host2, props, L.data?.scene_params || {}, (k, val) => { pendParams = { ...pendParams, [k]: val }; liveSend(pendParams); setTimeout(() => { pendParams = {}; }, 400); },
      { bound, onReset: (k) => { const d = pre?.params?.[k]; const val = d && typeof d === "object" ? d.rest ?? props[k]?.default : d ?? props[k]?.default; liveSend({ [k]: val }); } });
    put(ssU.body, h("p", { class: "hint" }, `${label(cur)}: changes go to ${name} at once.`), host2);
  };
  ssU.addEventListener("toggle", drawScene);
  // watch here
  const wU = unroll("Watch here", { tid: "watch-here", hint: "Off: the TV shows it" });
  let wPlayer = null, wStats = null;
  const wStage = h("div", { class: "stage player-stage" });
  const wLine = h("div", { class: "statsline" });
  wU.addEventListener("toggle", async () => {
    if (!wU.open) { wPlayer?.stop(); wPlayer = null; clearInterval(wStats); return; }
    const s = sessionOf(target);
    put(wU.body, h("p", { class: "hint" }, "Connecting…"));
    const r = await http("GET", `/api/sessions/${s}`);
    const att = r.ok ? r.data?.attach : null;
    if (!att?.url) { put(wU.body, h("p", { class: "hint", tid: "watch-msg" }, "Can't watch this one here: the renderer didn't give its stream.")); return; }
    const type = att.codec_string ? `video/mp4; codecs="${att.codec_string}"` : codecType(att.codec);
    if (!window.MediaSource || !MediaSource.isTypeSupported(type)) {
      put(wU.body, h("p", { class: "hint", tid: "watch-msg" }, `This browser can't play the ${att.h >= 2000 ? "4K " : ""}stream; the TV shows it.`), h("p", { class: "meta" }, `${att.codec || ""} ${att.w || ""}×${att.h || ""} @ ${att.fps || ""} fps, GPU ${r.data?.gpu ?? "–"}, set by the screen's config`));
      return;
    }
    put(wU.body, wStage, wLine, h("p", { class: "meta" }, `${att.codec || ""} ${att.w}×${att.h} @ ${att.fps} fps · GPU ${r.data?.gpu ?? "–"} · set by the screen's config`));
    wPlayer = new LivePlayer({ url: mediaUrl(att.url), targetBufferMs: att.target_buffer_ms ?? 300, mount: wStage, sendStats: false });
    wPlayer.start();
    clearInterval(wStats);
    wStats = setInterval(() => { if (!wPlayer || !wU.isConnected) { clearInterval(wStats); wPlayer?.stop(); return; } const st = wPlayer.stats(); wLine.textContent = `${st.state} · ${st.codec ?? ""} · ${st.presented} frames · buffered ${st.buffered_ms ?? "–"} ms`; }, 250);
  });

  put(host, acts, probEl, didU,
    h("div", { class: "os-section", style: { marginTop: "16px" } }, h("span", { class: "lab" }, "Next change"), next),
    h("div", { class: "row", style: { marginTop: "8px" } }, h("div", { class: "grow" }, autoSw), autoNext),
    h("div", { class: "os-section", style: { marginTop: "16px" } }, h("span", { class: "lab" }, "Feel"), feel), more,
    h("div", { class: "os-section", style: { marginTop: "12px" } }, h("span", { class: "lab" }, "Colours"), pals),
    h("div", { style: { marginTop: "12px" } }, fxHost), ssU, wU);

  // ---- server values
  const fromLook = () => {
    const d = L.data;
    if (!d || L.target !== target) return;
    const a = d.auto || {}, di = d.direction || {};
    autoSw.sw.set(a.enabled !== false);
    autoSw.querySelector(".sub").textContent = a.enabled === false ? "Paused: the scene stays until you change it" : "";
    const e = typeof di.energy === "number" ? di.energy : 0.5;
    const near = FEEL.reduce((x, y) => (Math.abs(y[2] - e) < Math.abs(x[2] - e) ? y : x));
    feelChips.forEach((c, i) => c.setAttribute("aria-pressed", String(FEEL[i] === near && Math.abs(near[2] - e) < 0.08)));
    for (const k of Object.keys(sl)) if (typeof di[k] === "number") sl[k].set(di[k]);
    const mf = new Set(d.music_fields || []), eff = d.direction_effective || {}, mu = d.music || {};
    for (const k of Object.keys(sl)) notes[k].textContent = mf.has(k) && typeof eff[k] === "number" ? ` ♪ ${fmt(eff[k])}` : "";
    const am = a.music || {};
    musicSw.sw.set(am.enabled !== false);
    musicW.set(typeof am.weight === "number" ? am.weight : 1);
    musicW.hidden = am.enabled === false;
    musicLine.textContent = am.enabled === false ? "The feel stays where you set it." : !mu.online ? "Waiting for the lighting desk…"
      : `Hearing ${mu.band || "music"}${typeof eff.energy === "number" ? ` · energy ${fmt(eff.energy)}` : ""}${mf.has("moods") ? ` · moods ${(eff.moods || []).slice(0, 2).join(", ")}` : ""}${mf.has("transition_style") ? ` · ${eff.transition_style} changes` : ""}`;
    if (document.activeElement !== style) style.value = di.transition_style || "";
    if (document.activeElement !== genre) genre.value = di.genre || "";
    moods = Array.isArray(di.moods) ? [...di.moods] : []; drawMoods();
    if (document.activeElement !== dmin && document.activeElement !== dmax) {
      const dw = Array.isArray(a.dwell_s) ? a.dwell_s : a.dwell_s_effective || null;
      dmin.value = dw ? String(Math.round(dw[0])) : ""; dmax.value = dw ? String(Math.round(dw[1])) : "";
      dmin.placeholder = a.dwell_s_effective ? String(Math.round(a.dwell_s_effective[0])) : "min"; dmax.placeholder = a.dwell_s_effective ? String(Math.round(a.dwell_s_effective[1])) : "max";
    }
    const fol = Array.isArray(a.follow_dmx) ? a.follow_dmx : [];
    followFx.sw.set(fol.includes("effect")); followVar.sw.set(fol.includes("variant"));
    skipHeavy.sw.set(!!(a.pool && typeof a.pool === "object" && a.pool.tags?.heavy === false));
    for (const b of snap.children) b.setAttribute("aria-pressed", String(b.dataset.testid === `snap-${a.snap || "beat"}`));
    drawPals();
    drawScene();
  };
  const offs = [onLook(fromLook), S.on(["compose", "libVersion"], drawNext)];
  const lst = () => { drawDid(); drawProblems(); };
  listeners.add(lst);
  offs.push(() => listeners.delete(lst));
  drawNext(); drawDid(); drawProblems(); drawPals();
  if (L.target === target && L.data) fromLook();
  refreshLook(target);
  fxCtl.refresh();
  return {
    destroy() { offs.forEach((f) => f()); fxCtl.destroy(); wPlayer?.stop(); clearInterval(wStats); },
    dirStatus: (inp) => {
      const a = inp?.auto;
      if (!a) return;
      dirStatus.textContent = a.enabled === false ? "Auto is paused." : `${a.armed ? "Next change" : "Waiting"}${a.eligible_in_s != null ? ` in ${Math.max(0, Math.round(a.eligible_in_s))}–${Math.max(0, Math.round(a.max_in_s ?? a.eligible_in_s))} s` : ""}${a.pending_change ? ` · the lighting desk asked for a change (${a.pending_change})` : ""}`;
      autoSw.querySelector(".sub").textContent = dirStatus.textContent;
    },
  };
}
function codecType(c) {
  const k = String(c || "").toLowerCase();
  if (k.startsWith("hevc") || k.startsWith("h265") || k.startsWith("hvc")) return 'video/mp4; codecs="hvc1.1.6.L153.B0"';
  if (k.startsWith("av1") || k.startsWith("av01")) return 'video/mp4; codecs="av01.0.08M.08"';
  return 'video/mp4; codecs="avc1.640028"';
}
// ---------------------------------------------------------------- Wander: the random art director (client side)
function scheduleWander(target) {
  clearTimeout(wanderT);
  if (!wanderOn) return;
  wanderT = setTimeout(async () => {
    if (!wanderOn) return;
    const t = S.get().target;
    if (room.isLive(room.screenOf(t)) && document.visibilityState === "visible") {
      const d = L.target === t && L.data?.direction ? L.data.direction : {};
      const spread = 0.15, c01 = (x) => Math.min(1, Math.max(0, x));
      const next = {};
      for (const k of ["energy", "pace", "intensity", "change_rate"]) {
        const lo = k === "intensity" ? 0.4 : 0;
        next[k] = Number(Math.max(lo, c01((typeof d[k] === "number" ? d[k] : 0.5) + (Math.random() * 2 - 1) * spread)).toFixed(2));
      }
      const v = catalog.get()?.vocab || {};
      if ((v.transition_style || []).length && Math.random() < 0.3) next.transition_style = v.transition_style[Math.floor(Math.random() * v.transition_style.length)];
      if ((v.moods || []).length && Math.random() < 0.3) next.moods = [v.moods[Math.floor(Math.random() * v.moods.length)]];
      const r = await patchLook(t, { direction: { ...next, source: "panel" } }, { silent: true });
      if (r?.ok) addDid(sessionOf(t), `Wander: energy ${fmt(d.energy)}→${fmt(next.energy)}, pace ${fmt(d.pace)}→${fmt(next.pace)}${next.transition_style ? `, style ${next.transition_style}` : ""}${next.moods ? `, mood ${next.moods[0]}` : ""}`);
    }
    scheduleWander(target);
  }, 10000 + Math.random() * 20000);
}
export const wandering = () => wanderOn;
