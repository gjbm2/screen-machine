// The Viewer: one component, three layouts (phone full-height sheet, tablet side sheet, desktop inspector).  The live
// picture leads and is never covered; under it: title, the one contextual primary, rating, variations, saved looks,
// Tune, Music (how it follows the music: browse/music.js), tags, About.  ⋯ holds the rest.
import { h, put, store as ls, ago, fmt } from "../kit.js";
import { S } from "../store.js";
import { lib } from "../data/library.js";
import { rate } from "../data/ratings.js";
import * as room from "../data/room.js";
import * as preview from "./preview.js";
import * as tune from "./tune.js";
import * as music from "./music.js";
import * as fx from "../onscreen/fx.js";
import { openShowOn } from "../onscreen/push.js";
import { cueBody, setVia, toggleFx, hasFx } from "../onscreen/compose.js";
import { button, chip, unroll, setLabel, busy } from "../ui/controls.js";
import { icon } from "../ui/icons.js";
import { ratingStrip } from "../ui/rating.js";
import { openMenu } from "../ui/sheet.js";
import { toast, toastErr } from "../ui/toast.js";
import { subtitle, cleanTitle, cleanDescription, originWords, sourceSite, slowWords, errorWords, ENERGY_WORDS, STAGE_WORDS, friendly } from "../words.js";
import { facetWord, variantTitle } from "../data/query.js";

export const V = { it: null, variant: null };
let nav = { step: () => {}, close: () => {}, review: () => {}, counter: () => "", filterBy: () => {}, isDesktop: () => false };
export function setNav(n) { nav = { ...nav, ...n }; }

// ---------------------------------------------------------------- the elements (built once, re-filled per item)
const capText = h("span", { class: "ctext" });
const capBtn = h("span", {});
const cap = h("span", { class: "cap", tid: "viewer-caption", "aria-live": "polite", dataset: { state: "idle" } }, h("span", { class: "dot" }), capText, capBtn);
const caption = h("div", { class: "caption" }, cap);
const backdrop = h("div", { class: "backdrop" });
const pvhost = h("div", { class: "pvhost" });
const fsBtn = h("button", { type: "button", class: "fs", "aria-label": "Full screen", title: "Full screen" }, icon("maximize"));
const shimmer = h("div", { class: "shimmer" });
const stage = h("div", { class: "stage", tid: "viewer-stage" }, backdrop, pvhost, shimmer, caption, fsBtn);
const closeBtn = h("button", { type: "button", class: "btn btn-ghost btn-icon vclose", "aria-label": "Close", tid: "viewer-close" }, icon("x"));
const counter = h("span", { class: "counter", tid: "viewer-counter" });
const vtitle = h("span", { class: "vtitle" });
const menuBtn = h("button", { type: "button", class: "btn btn-ghost btn-icon", "aria-label": "More actions", tid: "viewer-menu", "aria-haspopup": "menu" }, icon("more"));
const bar = h("div", { class: "viewer-bar" }, closeBtn, vtitle, counter, menuBtn);
const prevBtn = button("Previous", { kind: "ghost", icon: "left", tid: "viewer-prev" });
const nextBtn = button("Next", { kind: "ghost", tid: "viewer-next" });
nextBtn.append(icon("right"));
const titleEl = h("h2", { tid: "viewer-title" });
const subEl = h("div", { class: "sub", tid: "viewer-subtitle" });
const primary = button("", { kind: "primary", tid: "viewer-primary", big: true });
const showonSecondary = button("Show on a screen…", { kind: "outline", tid: "viewer-showon" });
const primaryNote = h("p", { class: "primary-note" });
const primaryWhy = h("p", { class: "why" });
const rateHost = h("div", {});
const varSec = h("div", { class: "vsec", tid: "variations" });
const lookSec = h("div", { class: "vsec", tid: "looks" });
const tuneU = unroll("Tune", { tid: "tune-unroll", hint: "Shuffle, nudge, save a look" });
const musicU = unroll("Music", { tid: "music-unroll", hint: "How it follows the music" });
const tagSec = h("div", { class: "vsec", tid: "tags" });
const aboutU = unroll("About this item", { tid: "about-unroll" });
const panel = h("div", { class: "viewer-panel" },
  h("div", { class: "stepper" }, prevBtn, nextBtn),
  h("div", { class: "vtitle-row" }, titleEl, subEl),
  h("div", {}, h("div", { class: "primary-row" }, h("div", { class: "pcol" }, primary, primaryNote), showonSecondary), primaryWhy),
  rateHost, varSec, lookSec, tuneU, musicU, tagSec, aboutU);
export const el = h("div", { class: "viewer", tid: "viewer", role: "region", "aria-label": "Viewer" }, bar, stage, panel);
let strip = null;
const tagList = h("span", { class: "chips", style: { display: "contents" } });
const sysList = h("span", { class: "chips", style: { display: "contents" } });
const tagIn = h("input", { tid: "tag-input", placeholder: "+ Tag", "aria-label": "Add tags (comma separated)", autocomplete: "off", list: "own-tags", maxlength: "64" });
const tagData = h("datalist", { id: "own-tags" });
put(tagSec, h("h3", {}, "Tags"), h("div", { class: "chips" }, sysList, tagList, h("span", { class: "tag-input" }, tagIn)), tagData);

// ---------------------------------------------------------------- wiring
closeBtn.addEventListener("click", () => nav.close());
prevBtn.addEventListener("click", () => nav.step(-1));
nextBtn.addEventListener("click", () => nav.step(1));
menuBtn.addEventListener("click", () => openItemMenu(menuBtn));
showonSecondary.addEventListener("click", () => showOn());
primary.addEventListener("click", () => primaryAction());
fsBtn.addEventListener("click", (e) => { e.stopPropagation(); const s = stage; (s.requestFullscreen || s.webkitRequestFullscreen)?.call(s); });
let sx = null, sy = null, moved = false;
stage.addEventListener("pointerdown", (e) => { sx = e.clientX; sy = e.clientY; moved = false; });
stage.addEventListener("pointerup", (e) => {
  if (sx == null) return;
  const dx = e.clientX - sx, dy = e.clientY - sy;
  sx = null;
  if (e.target.closest(".fs, .cap button")) return;
  if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy)) { moved = true; nav.step(dx < 0 ? 1 : -1); return; }
  if (nav.isDesktop()) nav.review();
  else stage.classList.toggle("capoff");
});
// touch swipes (some phones don't send pointerup for a horizontal pan)
let tx = null, ty = null;
stage.addEventListener("touchstart", (e) => { tx = e.touches[0].clientX; ty = e.touches[0].clientY; }, { passive: true });
stage.addEventListener("touchend", (e) => {
  if (tx == null) return;
  const t = e.changedTouches[0], dx = t.clientX - tx, dy = t.clientY - ty;
  tx = null;
  if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy) && !moved) { sx = null; nav.step(dx < 0 ? 1 : -1); }
}, { passive: true });
tagIn.addEventListener("keydown", (e) => {
  if (e.key !== "Enter") return;
  e.preventDefault();
  const it = V.it;
  const add = tagIn.value.split(",").map((x) => x.trim().toLowerCase()).filter(Boolean);
  tagIn.value = "";
  if (it && add.length) rate(it, { tags: [...new Set([...it._mytags, ...add])] });
});
tuneU.addEventListener("toggle", () => { if (tuneU.open) drawTune(); });
musicU.addEventListener("toggle", () => { if (musicU.open) drawMusic(); });
music.watchNow(() => musicU.open && !!V.it, () => drawMusic());
tune.onTune(() => { if (tuneU.open) drawTune(); drawPrimary(); });

// ---------------------------------------------------------------- show an item
export function show(it, { variant = null, keepVariant = false } = {}) {
  const same = V.it?.key === it.key;
  V.it = it;
  if (!same || !keepVariant) V.variant = variant || (typeof it.spec === "string" ? it.spec : it.preset) || null;
  if (!same || !keepVariant) { tune.reset(it, V.variant); music.reset(it, V.variant); }
  drawAll();
  preview.host(pvhost);
  preview.show(it, V.variant || it.spec, { label: variantLabel(it, V.variant) });
  if (it.kind === "effect" && room.isReady(room.screenOf(S.get().target))) fx.refresh(S.get().target);
}
export function hostPreview() { preview.host(pvhost); }
export function clear() { V.it = null; V.variant = null; tune.reset(null, null); }
export const stageEl = () => stage;

function variantLabel(it, v) {
  const p = (it.presets || []).find((x) => x.name === v);
  if (!p || it.presets.length < 2) return it.title;
  return variantTitle(it.title, p.label);
}

export function setVariant(name) {
  const it = V.it;
  if (!it || V.variant === name) return;
  V.variant = name;
  tune.reset(it, name);
  music.reset(it, name);
  drawVariants(); drawPrimary(); drawAbout();
  if (tuneU.open) drawTune();
  if (musicU.open) drawMusic();
  preview.show(it, name, { label: variantLabel(it, name) });
}
export function stepVariant(d) {
  const it = V.it;
  const ps = it?.presets || [];
  if (ps.length < 2) return;
  const i = Math.max(0, ps.findIndex((p) => p.name === V.variant));
  setVariant(ps[(i + d + ps.length) % ps.length].name);
}

function drawAll() {
  const it = V.it;
  if (!it) return;
  backdrop.style.backgroundImage = it.thumb ? `url("${it.thumb}")` : "none";
  vtitle.textContent = it.title;
  titleEl.textContent = it.title;
  subEl.textContent = [friendly(it.kind), subtitle(it), originWords(it.origin)].filter(Boolean).join(" · ");
  counter.textContent = nav.counter(it);
  if (!strip) { strip = ratingStrip(it, (x, p) => rateAndMaybeAdvance(x, p)); rateHost.append(strip); }
  strip.update(it);
  drawVariants(); drawLooks(); drawTags(); drawAbout(); drawPrimary(); drawCaption();
  if (tuneU.open) drawTune();
  if (musicU.open) drawMusic();
}
export function refresh() {
  const it = V.it;
  if (!it) return;
  const fresh = lib.byKey.get(it.key);
  if (fresh && fresh !== it) V.it = fresh;
  counter.textContent = nav.counter(V.it);
  strip?.update(V.it);
  drawLooks(); drawOwnTags(); drawPrimary();
}
export function refreshCounter() { if (V.it) counter.textContent = nav.counter(V.it); }

export function rateAndMaybeAdvance(it, patch) {
  const adv = S.get().advance && V.it?.key === it.key && (patch.stars || patch.reject);
  const next = adv ? nav.nextKey?.(1) : null;          // chosen before the rating re-sorts the list
  const p = rate(it, patch);
  if (adv) setTimeout(() => (next ? nav.select(next) : nav.step(1)), 120);
  return p;
}

// ---------------------------------------------------------------- caption (the truth about the picture)
function drawCaption() {
  const p = S.get().preview;
  const it = V.it;
  const st = p.state === "idle" && it ? "starting" : p.state;
  cap.dataset.state = st;
  const tone = { starting: "info", switching: "info", playing: "live", failed: "warn", full: "warn", paused: "mute" }[st] || "mute";
  cap.className = `cap tone-${tone}`;
  capText.textContent = p.text || (it ? "Starting…" : "");
  put(capBtn,
    p.action === "retry" ? button("Retry", { kind: "quiet", tid: "preview-retry", onClick: (e) => { e.stopPropagation(); preview.retry(); } }) :
    p.action === "resume" ? button("Resume", { kind: "quiet", tid: "preview-resume", onClick: (e) => { e.stopPropagation(); preview.resume(); } }) : null);
  stage.classList.toggle("playing", st === "playing" || (st === "switching" && !!preview.video()) || (st === "failed" && !!preview.video()));
  const fk = st === "failed" ? `${p.key}|${p.text}` : "";
  if (fk !== lastFailed) { lastFailed = fk; if (tuneU.open && it) drawTune(); }
}
let lastFailed = "";
preview.onChange(() => drawCaption());

// ---------------------------------------------------------------- primary
const targetLive = () => room.isLive(room.screenOf(S.get().target));
export function actionFor(it) {
  if (!it) return "showon";
  if (!targetLive()) return "showon";
  if (it.kind === "scene") return "play";
  if (it.kind === "effect") return fx.has(tune.currentSpec(it, V.variant).spec) || fx.has(V.variant || it.spec) ? "remove" : "add";
  return "use";
}
function drawPrimary() {
  const it = V.it;
  if (!it) return;
  const a = actionFor(it);
  const name = room.nameOf(S.get().target);
  const cur = tune.currentSpec(it, V.variant);
  const vl = variantLabel(it, V.variant);
  const via = S.get().compose.via;
  const label = a === "showon" ? "Show on a screen…" : a === "play" ? `Play on ${name}` : a === "add" ? `Add ${vl} to ${name}` : a === "remove" ? `Take off ${name}`
    : via?.key === it.key && (via.spec === (cur.spec)) ? "Used for the next change" : "Use for the next change";
  setLabel(primary, label);
  primary.dataset.action = a;
  showonSecondary.hidden = a === "showon";
  const missing = S.get().roomLink === "missing";
  primary.disabled = a === "showon" && missing;
  primaryWhy.textContent = a === "showon" && missing ? "This page was opened outside the Screen Machine app, so it can't reach the screens: use the Library link on the Room page." : "";
  primaryWhy.hidden = !primaryWhy.textContent;
  primaryNote.textContent = cur.note || (a === "play" ? `Changes ${S.get().compose.at === "now" ? "now" : "on the beat"}${S.get().compose.via ? ` via ${S.get().compose.via.label}` : ""}` : "");
  primaryNote.hidden = !primaryNote.textContent;
}
S.on(["roomStatus", "target", "compose", "roomLink"], () => drawPrimary());
fx.onBus(() => drawPrimary());

export async function primaryAction(it = V.it) {
  if (!it) return;
  const a = actionFor(it);
  if (a === "showon") return showOn(it);
  return liveAction(it, a);
}
/** `c`: the live action (Play / Add / Take off / Use), only while the target is live. */
export async function liveAction(it = V.it, a = actionFor(it)) {
  if (!it) return;
  const t = S.get().target, name = room.nameOf(t);
  if (!targetLive()) { toast(`${name} isn't live. Go live from On screen (g o).`); return; }
  const cur = it === V.it ? tune.currentSpec(it, V.variant) : { spec: it.spec };
  const label = it === V.it ? variantLabel(it, V.variant) : it.title;
  if (a === "play") {
    busy(primary, true, "Playing…");
    const r = await room.cue(t, cueBody(cur.spec));
    busy(primary, false); drawPrimary();
    if (r.ok) toast(`${label} → ${name}`); else toastErr(errorWords(r, { screen: name, target: "the room service" }));
  } else if (a === "add" || a === "remove") {
    busy(primary, true, a === "add" ? "Adding…" : "Taking off…");
    if (a === "add") await fx.add(t, it, cur.spec, label);
    else await fx.removeSpec(t, fx.has(cur.spec) ? cur.spec : V.variant || it.spec, label);
    busy(primary, false); drawPrimary();
  } else {
    const via = S.get().compose.via;
    if (via && via.key === it.key && via.spec === cur.spec) { setVia(null); toast("The next change uses the room's choice"); }
    else { setVia({ key: it.key, spec: cur.spec, label }); toast(`The next change goes via ${label}`); }
  }
}

export function showOn(it = V.it) {
  if (!it) return;
  const opts = [];
  const isCur = it === V.it;
  const v = isCur ? V.variant : (typeof it.spec === "string" ? it.spec : it.preset);
  for (const p of it.presets || []) opts.push([p.name, it.presets.length > 1 ? cleanTitle(p.label) : it.title, p.name, variantLabel(it, p.name)]);
  if (!opts.length) opts.push(["plugin", it.title, it.spec, it.title]);
  for (const l of it._looks || []) opts.push([`look:${l.name}`, `Look: ${l.name}`, { preset: v || it.preset, params: l.params }, `${it.title} · ${l.name}`]);
  let value = v || opts[0][0];
  if (isCur) {
    const cur = tune.currentSpec(it, V.variant);
    if (cur.tuned) {
      if (tune.T.activeLook && !tune.T.dirtySinceLook && opts.some(([k]) => k === `look:${tune.T.activeLook}`)) value = `look:${tune.T.activeLook}`;
      else { opts.unshift(["tuned", `With your changes (${tune.T.touched.size})`, cur.spec, `${it.title} (tuned)`]); value = "tuned"; }
    }
  }
  openShowOn(it, { options: opts, value });
}

// ---------------------------------------------------------------- variations, looks, tags, about
function drawVariants() {
  const it = V.it;
  const ps = it.presets || [];
  varSec.hidden = ps.length < 2;
  if (ps.length < 2) { put(varSec); return; }
  put(varSec, h("h3", {}, "Variations"), h("div", { class: "chips" }, ps.map((p) => chip(cleanTitle(p.label) || p.name, {
    pressed: p.name === V.variant, tid: "variation", data: { preset: p.name }, onClick: () => setVariant(p.name) }))));
}
let confirmDel = null;
function drawLooks() {
  const it = V.it;
  const looks = it?._looks || [];
  lookSec.hidden = !looks.length;
  if (!looks.length) { put(lookSec); return; }
  const active = tune.T.key === it.key && tune.T.activeLook && !tune.T.dirtySinceLook ? tune.T.activeLook : null;
  put(lookSec, h("h3", {}, "Saved looks"), h("div", { class: "chips" }, looks.map((l) => {
    const c = chip(l.name, { pressed: l.name === active, tid: "look", data: { name: l.name }, onClick: () => tune.applyLook(it, l) });
    let lp = null;
    c.addEventListener("touchstart", () => { lp = setTimeout(() => { confirmDel = l.name; drawLooks(); }, 500); }, { passive: true });
    c.addEventListener("touchend", () => clearTimeout(lp));
    c.addEventListener("touchmove", () => clearTimeout(lp), { passive: true });
    const del = h("button", { type: "button", class: "chip x del", tid: "look-delete", "aria-label": `Delete the look ${l.name}`, title: "Delete this look",
                              style: { minWidth: "36px", padding: "0", justifyContent: "center" } }, icon("x"));
    del.addEventListener("click", () => deleteLook(it, l.name));
    return h("span", { class: "look-chip" }, c, del);
  })), confirmDel ? h("div", { class: "confirm-inline" }, `Delete ${confirmDel}?`,
      button("Delete", { kind: "danger", cls: "btn-sm", tid: "look-delete", onClick: () => deleteLook(it, confirmDel) }),
      button("Keep", { kind: "quiet", cls: "btn-sm", onClick: () => { confirmDel = null; drawLooks(); } })) : null);
}
async function deleteLook(it, name) {
  confirmDel = null;
  await rate(it, { looks: (it._looks || []).filter((x) => x.name !== name) });
  if (tune.T.activeLook === name) tune.T.activeLook = null;
  drawLooks(); drawPrimary();
}
function drawTags() {
  const it = V.it;
  const sys = [];
  for (const f of ["look", "dims", "stage", "style"]) for (const v of it.ff?.[f] || []) sys.push([f, v]);
  put(sysList, sys.slice(0, 6).map(([f, v]) => chip(facetWord(f, v), { cls: "tag-sys", title: `Show only ${facetWord(f, v)}`, onClick: () => nav.filterBy(it.kind, f, v) })));
  drawOwnTags();
}
function drawOwnTags() {
  const it = V.it;
  if (!it) return;
  put(tagList, (it._mytags || []).map((t) => chip(t, { tid: "tag-chip", data: { tag: t }, removable: true, removeTid: "tag-remove", removeLabel: `Remove the tag ${t}`,
    onRemove: () => rate(it, { tags: it._mytags.filter((x) => x !== t) }) })));
  const all = new Set();
  for (const x of lib.items) for (const t of x._mytags || []) all.add(t);
  put(tagData, [...all].sort().map((t) => h("option", { value: t })));
}
export function focusTag() { if (!V.it) return; tagIn.focus(); tagIn.scrollIntoView({ block: "nearest" }); }

function drawAbout() {
  const it = V.it;
  const rows = [];
  const row = (k, v) => { if (v != null && v !== "") rows.push(h("dt", {}, k), h("dd", {}, v)); };
  const desc = cleanDescription(it.description);
  if (it.title0 && it.title0 !== it.title) row("Original title", it.title0);
  if (it._author) row("By", it._author);
  if (it._source) row("Source", h("a", { href: it._source, target: "_blank", rel: "noopener" }, sourceSite(it._source), icon("arrow", { cls: "tiny" })));
  row("Plug-in", it.plugin);
  const vp = (it.presets || []).find((p) => p.name === (V.variant || it.preset));
  row("Preset", vp && it.presets.length > 1 && cleanTitle(vp.label) ? `${cleanTitle(vp.label)} (${vp.name})` : V.variant || it.preset || "— (the plug-in's defaults)");
  row("Origin", originWords(it.origin));
  row("Licence", it.ff?.licence?.[0] ? friendly(it.ff.licence[0]) : null);
  if (it.kind === "effect") row("Stage", STAGE_WORDS[it.stage] || it.stage);
  if (it.kind === "transition" && it.duration_s != null) row("Duration", `${fmt(it.duration_s, 1).replace(/\.0$/, "")} s`);
  if (Array.isArray(it.energy)) row("Energy", `${(it.ff?.energy || []).map((e) => ENERGY_WORDS[e] || e).join(", ")} (${fmt(it.energy[0])}–${fmt(it.energy[1])})`);
  if (it.bench_ms_4k != null) row("Cost", `${fmt(it.bench_ms_4k)} ms a frame at 4K${it.heavy ? " (heavy)" : ""}`);
  if (it.auto != null) row("In auto pools", it.auto ? "Yes" : "No");
  row("Added", it.added_at ? ago(it.added_at) : null);
  if (it.preview) row("Recorded clip", h("a", { href: it.preview, target: "_blank", rel: "noopener" }, "Open", icon("arrow", { cls: "tiny" })));
  if (it.slow) row("GPU", h("span", { class: "warn-ink" }, slowWords(it.slow)));
  if (it.load === "error" || it._broken) row("Load error", h("span", { class: "bad-ink" }, it.error || it._broken || "failed to load"));
  if (it.runtime_errors > 0) row("Runtime errors", h("span", { class: "bad-ink" }, `${it.runtime_errors}${it.runtime_error ? `: ${String(it.runtime_error).split("\n")[0]}` : ""}`));
  const pr = [];
  const P = it.params && typeof it.params === "object" ? it.params : null;
  if (P) for (const [k, x] of Object.entries(P)) pr.push(`${k}: ${typeof x === "object" ? JSON.stringify(x) : x}`);
  const B = it.bindings && typeof it.bindings === "object" ? it.bindings : null;
  if (B) for (const [k, x] of Object.entries(B)) pr.push(`${k} ← ${typeof x === "object" ? JSON.stringify(x) : x}`);
  put(aboutU.body, h("div", { class: "about" }, desc ? h("p", { class: "desc" }, desc) : null, h("dl", {}, rows), pr.length ? h("pre", {}, pr.join("\n")) : null));
}

function drawTune() {
  const it = V.it;
  if (!it) return;
  if (tune.T.key !== it.key) tune.reset(it, V.variant);
  const p = S.get().preview;
  const failed = p.state === "failed" && p.key === it.key && /too slow|failed to start/i.test(p.text) ? p.text.split(" — ")[0].replace(/\.$/, "") : null;
  const ae = document.activeElement;
  if (ae && tuneU.body.contains(ae) && ae.type === "range") return;     // never redraw under a dragging finger
  tune.renderPanel(tuneU.body, it, { failed });
}
function drawMusic() {
  const it = V.it;
  if (!it) return;
  const ae = document.activeElement;
  if (ae && musicU.body.contains(ae) && (ae.type === "range" || ae.tagName === "SELECT")) return;   // never under a finger
  music.renderPanel(musicU.body, it, V.variant);
}
export function toggleTune(open = !tuneU.open) { tuneU.open = open; if (open) { drawTune(); tuneU.scrollIntoView({ block: "nearest" }); } }
export const tuneOpen = () => tuneU.open;

// ---------------------------------------------------------------- ⋯
function openItemMenu(anchor) {
  const it = V.it;
  if (!it) return;
  const items = [];
  const name = room.nameOf(S.get().target);
  if (it.kind === "scene") items.push({ label: "Start the live show with this", tid: "menu-start-with", onClick: () => startWith(it) });
  if (it.kind === "transition") {
    items.push({ label: "Use when going live", tid: "menu-use-in", onClick: () => setGolive({ in: tune.currentSpec(it, V.variant).spec, inLabel: variantLabel(it, V.variant) }, `${name} goes live via ${variantLabel(it, V.variant)}`) });
    items.push({ label: "Use when going back to art", tid: "menu-use-out", onClick: () => setGolive({ out: tune.currentSpec(it, V.variant).spec, outLabel: variantLabel(it, V.variant) }, `${name} goes back to art via ${variantLabel(it, V.variant)}`) });
  }
  if (it.kind === "effect") {
    const on = hasFx(it.key);
    items.push({ label: on ? "Detach from the next change" : "Attach to the next change", tid: "menu-attach-next",
                 onClick: () => { if (toggleFx({ key: it.key, spec: tune.currentSpec(it, V.variant).spec, label: variantLabel(it, V.variant) })) toast(on ? `${it.title} detached` : `${it.title} rides on the next change`); } });
  }
  items.push({ label: "Show on a screen…", sub: "o", onClick: () => showOn(it) });
  if (nav.isDesktop()) items.push({ label: "Review", sub: "f", onClick: () => nav.review() });
  items.push({ sep: true });
  items.push({ label: "Only unrated", checked: S.get().onlyUnrated, tid: "menu-only-unrated", onClick: () => S.set({ onlyUnrated: !S.get().onlyUnrated }) });
  items.push({ label: "Next after rating", checked: S.get().advance, tid: "menu-next-after-rating", onClick: () => S.set({ advance: !S.get().advance }) });
  openMenu(anchor, items, { tid: "viewer-menu-list" });
}
export function setGolive(patch, msg) {
  const g = { ...(S.get().golive || {}), ...patch };
  S.set({ golive: g });
  ls.set("sm.console.golive", g);
  if (msg) toast(msg);
}
function startWith(it) {
  const spec = tune.currentSpec(it, V.variant).spec;
  setGolive({ entry: { key: it.key, spec, label: variantLabel(it, V.variant) } }, `${room.nameOf(S.get().target)} will start with ${variantLabel(it, V.variant)}`);
}
export { stage };
