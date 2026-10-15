// Browse: kind tabs with honest counts, search, Filter, one row of quick chips, the Next-change and FX strips while the
// target screen is live, and the grid in the page's own scroll.  Selecting a tile plays it: in the inspector on a
// desktop, in the Viewer sheet on a phone or tablet.
import { h, put, debounce } from "../kit.js";
import { S } from "../store.js";
import { lib } from "../data/library.js";
import * as room from "../data/room.js";
import { matches, counts, sorted, tokens, activeFilters, badgeCount, facetValues, facetWord, itemForSpec, FACET_DEFS } from "../data/query.js";
import { createGrid } from "./grid.js";
import * as viewer from "./viewer.js";
import * as review from "./review.js";
import * as preview from "./preview.js";
import { openFilters, setFacet, clearFilters, KIND_WORDS, nWord } from "./filters.js";
import { renderStrip } from "../onscreen/compose.js";
import * as fx from "../onscreen/fx.js";
import { rate } from "../data/ratings.js";
import { button, chip, seg, emptyState } from "../ui/controls.js";
import { icon } from "../ui/icons.js";
import { openSheet, closeLayer, replaceHash } from "../ui/sheet.js";
import { desiredScene } from "../words.js";

export const isDesktop = () => window.matchMedia("(min-width: 1100px)").matches;
const isTablet = () => window.matchMedia("(min-width: 700px) and (min-height: 500px)").matches && !isDesktop();
const KINDS = [["scene", "Scenes"], ["transition", "Transitions"], ["effect", "Effects"]];

let list = [];
let grid, root, inspector, quick, nextStrip, fxStrip, emptyHost, kindSeg, countEls = {}, searchIn, filterBadge, filterBtn;
let viewerSheet = null;

export const currentList = () => list;
const counterFor = (it) => { const i = list.findIndex((x) => x.key === it.key); return i >= 0 ? `${i + 1} of ${list.length}` : ""; };

export function mount(host) {
  root = host;
  // ---- toolbar
  kindSeg = seg(KINDS.map(([k, l]) => { const n = h("span", { class: "n" }); countEls[k] = n; return [k, l, n]; }), S.get().kind,
                (k) => setKind(k), { aria: "Kind", itemTid: (k) => `kind-${k}` });
  kindSeg.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    const i = KINDS.findIndex(([k]) => k === S.get().kind);
    const j = (i + (e.key === "ArrowRight" ? 1 : KINDS.length - 1)) % KINDS.length;
    setKind(KINDS[j][0]);
    kindSeg.buttons[j].focus();
    e.preventDefault(); e.stopPropagation();
  });
  searchIn = h("input", { type: "search", tid: "search", placeholder: "Search scenes…", "aria-label": "Search", autocomplete: "off", spellcheck: "false" });
  const clearQ = h("button", { type: "button", class: "clear", "aria-label": "Clear the search", hidden: true }, icon("x"));
  const kbd = h("span", { class: "kbd", "aria-hidden": "true" }, "/");
  const search = h("div", { class: "search" }, icon("search"), searchIn, clearQ, kbd);
  const setQ = debounce(() => S.set({ q: searchIn.value }), 120);
  searchIn.addEventListener("input", () => { clearQ.hidden = !searchIn.value; kbd.hidden = !!searchIn.value || !isDesktop(); setQ(); });
  clearQ.addEventListener("click", () => { searchIn.value = ""; clearQ.hidden = true; S.set({ q: "" }); searchIn.focus(); });
  searchIn.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "Enter") { const k = list[0]?.key; if (k) { e.preventDefault(); setQ.flush?.(); grid.focusKey(k); } }
  });
  kbd.hidden = !isDesktop();
  filterBadge = h("span", { class: "fbadge", tid: "filter-badge", hidden: true });
  filterBtn = button("Filter", { kind: "outline", icon: "filter", tid: "filter-btn", cls: "filter-btn", onClick: () => openFilters() });
  filterBtn.append(filterBadge);
  const toolbar = h("div", { class: "toolbar" }, kindSeg, h("div", { class: "search-row" }, search, filterBtn));
  quick = h("div", { class: "chips-x quick", tid: "quick-chips", role: "toolbar", "aria-label": "Quick filters" });
  nextStrip = h("div", { class: "strip", tid: "next-strip", hidden: true });
  fxStrip = h("div", { class: "strip", tid: "fx-strip", hidden: true });
  emptyHost = h("div", {});
  grid = createGrid({ mode: "browse", onSelect: (it) => select(it.key, { open: true }), rate: (it, p) => rate(it, p), ribbons: () => ribbons, selected: () => S.get().sel });
  inspector = h("aside", { class: "inspector", "aria-label": "Selected item" });
  const main = h("div", { class: "browse-main" }, toolbar, quick, nextStrip, fxStrip, emptyHost, grid.el, grid.more);
  put(root, h("div", { class: "browse" }, main, inspector));
  viewer.setNav({ step, nextKey, select: goTo, close: closeViewer, review: openReview, counter: counterFor, filterBy, isDesktop });
  review.setNav({ step, list: () => list, select: (k) => select(k, { open: true, focus: false }), counter: counterFor, reveal });

  S.on(["kind", "q", "stars", "rejects", "facets", "sort", "libVersion", "large"], () => recompute());
  S.on(["roomStatus", "libVersion"], () => { computeRibbons(); grid.refresh(); });
  S.on(["roomStatus", "target", "compose", "kind"], () => drawStrips());
  S.on(["sel"], () => { grid.refresh(); drawInspector(); });
  S.on(["libVersion"], () => { viewer.refresh(); });
  fx.onBus(() => drawStrips());
  window.matchMedia("(min-width: 1100px)").addEventListener?.("change", () => relayout());
  recompute();
  drawInspector();
}

// ---------------------------------------------------------------- list, counts, chips
function stateForQuery() { const s = S.get(); return { ...s, qt: tokens(s.q) }; }
function recompute() {
  const s = S.get();
  if (!lib.loaded) { put(grid.el, Array.from({ length: 12 }, () => h("div", { class: "tile skel", style: { aspectRatio: "16 / 13" } }))); return; }
  document.body.classList.toggle("large", !!s.large);
  const st = stateForQuery();
  list = sorted(lib.items.filter((it) => matches(it, st, s.kind)), s.sort);
  const c = counts(lib.items, st);
  for (const [k] of KINDS) { countEls[k].textContent = String(c[k]); kindSeg.buttons.find((b) => b.dataset.v === k).dataset.count = String(c[k]); }
  kindSeg.setValue(s.kind);
  searchIn.placeholder = `Search ${KIND_WORDS[s.kind][1]}…`;
  if (searchIn.value !== s.q && document.activeElement !== searchIn) searchIn.value = s.q;
  const n = badgeCount(s, s.kind);
  filterBadge.textContent = String(n); filterBadge.hidden = !n;
  filterBtn.setAttribute("aria-label", n ? `Filter (${n} on)` : "Filter");
  grid.replaceItems(lib.byKey);
  grid.setList(list, { keepLimit: true });
  drawQuick(c);
  drawEmpty(c);
  viewer.refreshCounter();
}
function drawQuick() {
  const s = S.get(), kind = s.kind;
  const act = activeFilters(s, kind);
  const kids = act.map((f) => chip(f.label, { cls: "on", tid: "active-chip", data: { facet: f.facet, value: f.value }, removable: true, removeLabel: `Remove the filter ${f.label}`,
    onRemove: () => removeActive(f), onClick: () => removeActive(f) }));
  if (act.length) kids.push(chip("Clear all", { tid: "clear-all", onClick: () => clearFilters(kind) }));
  for (const t of tagSuggestions(s, kind)) kids.push(t);
  const unrated = lib.items.filter((it) => it.kind === kind && !it._rating && !it._reject && matches(it, { ...stateForQuery(), stars: { n: null } }, kind)).length;
  if (unrated) kids.push(chip(`Review unrated ${unrated}`, { tid: "review-unrated", onClick: () => reviewUnrated() }));
  const sel = (f) => new Set(s.facets[kind]?.[f] || []);
  const mine = new Map();
  for (const it of lib.items) for (const t of it._mytags || []) mine.set(t, (mine.get(t) || 0) + 1);
  for (const [t] of [...mine].sort((a, b) => b[1] - a[1]).slice(0, 3)) if (!sel("mytags").has(t)) kids.push(chip(t, { onClick: () => setFacet(kind, "mytags", t, true), data: { facet: "mytags", value: t } }));
  const fresh = lib.items.filter((it) => it.kind === kind && it._new).length;
  if (fresh && !sel("added").has("new")) kids.push(chip(`New ${fresh}`, { onClick: () => setFacet(kind, "added", "new", true) }));
  const looks = facetValues(lib.items, stateForQuery(), kind, "look").filter(([v, n]) => n && !sel("look").has(v)).slice(0, 4);
  for (const [v] of looks) kids.push(chip(facetWord("look", v), { onClick: () => setFacet(kind, "look", v, true), data: { facet: "look", value: v } }));
  if (!sel("energy").has("low")) kids.push(chip("Calm", { onClick: () => setFacet(kind, "energy", "low", true) }));
  // one chip per word: a Look "calm" and Movement "Calm" (or a tag of the same name) would read "Calm · … · Calm"
  const seen = new Set();
  put(quick, kids.filter((c) => { const w = c.textContent.trim().toLowerCase(); if (seen.has(w)) return false; seen.add(w); return true; }));
}
// Searching "piano" offers the tag piano (any facet: sets, your tags, look, technique, style, depth) with how many it
// would show; tapping it filters by the tag and clears the words.
const SUGGEST_FACETS = ["modes", "mytags", "look", "technique", "style", "dims"];
function tagSuggestions(s, kind) {
  const words = String(s.q || "").toLowerCase().split(/\s+/).filter((w) => w.length >= 2);
  if (!words.length) return [];
  const base = { ...stateForQuery(), q: "", qt: [] };
  const out = [];
  for (const facet of SUGGEST_FACETS) {
    const chosen = new Set(s.facets[kind]?.[facet] || []);
    for (const [v, n] of facetValues(lib.items, base, kind, facet)) {
      const w = String(v).toLowerCase();
      if (!n || chosen.has(v) || !words.some((q) => w.includes(q))) continue;
      out.push(chip(facetWord(facet, v), { count: n, cls: "suggest", tid: "tag-suggest", data: { facet, value: v },
        title: `Show only ${FACET_DEFS[facet]?.label?.toLowerCase() || "tag"} "${facetWord(facet, v)}"`,
        onClick: () => { setFacet(kind, facet, v, true); searchIn.value = ""; S.set({ q: "" }); } }));
    }
  }
  return out.slice(0, 6);
}
function removeActive(f) {
  const s = S.get();
  if (f.facet === "stars") S.set({ stars: { ...s.stars, n: null } });
  else if (f.facet === "rejects") S.set({ rejects: "hide" });
  else setFacet(s.kind, f.facet, f.value, false);
}
function drawEmpty(c) {
  const s = S.get();
  if (list.length || !lib.loaded) { put(emptyHost); return; }
  const other = KINDS.filter(([k]) => k !== s.kind && c[k] > 0);
  const acts = [];
  if (s.q) acts.push(button("Clear search", { kind: "outline", onClick: () => { searchIn.value = ""; S.set({ q: "" }); } }));
  if (badgeCount(s, s.kind)) acts.push(button("Clear filters", { kind: "outline", onClick: () => clearFilters(s.kind) }));
  for (const [k, l] of other) acts.push(button(`${nWord(c[k], k).replace(/^\d+ /, `${c[k]} `)} in ${l} →`, { kind: "quiet", onClick: () => setKind(k) }));
  const msg = s.rejects === "only" ? "Nothing is rejected here." : s.stars.n != null ? "Nothing here with that many stars yet: rate with 1–5 in the Viewer." : "Nothing matches.";
  put(emptyHost, emptyState(msg, acts));
}
export function setKind(k) {
  if (S.get().kind === k) return;
  S.set({ kind: k });
  window.scrollTo({ top: 0 });
}
export function filterBy(kind, facet, value) {
  closeViewer();
  if (S.get().kind !== kind) S.set({ kind });
  setFacet(kind, facet, value, true);
}

// ---------------------------------------------------------------- ribbons: "On North screen" for any live screen
let ribbons = new Map();
function computeRibbons() {
  ribbons = new Map();
  const st = S.get().roomStatus;
  for (const [id, sc] of Object.entries(st?.screens || {})) {
    if (!room.isLive(sc)) continue;
    const it = itemForSpec(desiredScene(sc.desired), lib.items, lib.pidx);
    if (!it) continue;
    const name = (sc.name || id).replace(/ screen$/i, "");
    ribbons.set(it.key, [...(ribbons.get(it.key) || []), name]);
  }
}

// ---------------------------------------------------------------- strips (target live)
function drawStrips() {
  const t = S.get().target;
  const live = room.isLive(room.screenOf(t));
  nextStrip.hidden = !live;
  if (live) renderStrip(nextStrip, { target: t });
  const showFx = live && S.get().kind === "effect";
  fxStrip.hidden = !showFx;
  if (showFx) {
    if ((fx.bus.target !== t || Date.now() - fx.bus.at > 5000) && room.isReady(room.screenOf(t))) fx.refresh(t);
    fx.renderStrip(fxStrip, t);
  }
}
setInterval(() => { if (document.visibilityState === "visible" && fxStrip && !fxStrip.hidden && S.get().mode === "browse" && room.isReady(room.screenOf(S.get().target))) fx.refresh(S.get().target); }, 5000);

// ---------------------------------------------------------------- selection, Viewer, Review
export function select(key, { open = true, focus = false } = {}) {
  const it = lib.byKey.get(key);
  if (!it) return;
  const showing = S.get().sel === key && viewer.V.it?.key === key && (isDesktop() || viewerSheet?.isOpen());
  S.set({ sel: key });
  if (showing) { if (focus) grid.focusKey(key); return; }
  if (isDesktop()) {
    viewer.show(it);
    drawInspector();
    const t = grid.ensureShown(key);
    if (focus && t) grid.focusKey(key);
    else if (t && !review.isOpen()) t.scrollIntoView({ block: "nearest" });
  } else if (open || viewerSheet) {
    openViewerSheet(it);
    viewer.show(it);
  }
}
function openViewerSheet(it) {
  const hash = `#browse/${it.kind}/${encodeURIComponent(it.name)}`;
  if (viewerSheet?.isOpen()) { replaceHash(hash); return; }
  viewerSheet = openSheet({ id: "viewer", body: viewer.el, cls: `viewer-sheet${isTablet() ? " side" : ""}`, side: isTablet(), hash, scrim: isTablet(), focus: false,
    onClose: () => { viewerSheet = null; preview.release(); S.set({ sel: null, viewer: false }); } });
  S.set({ viewer: true });
}
export function closeViewer() {
  if (viewerSheet?.isOpen()) { viewerSheet.close(); return true; }
  if (S.get().sel) { const k = S.get().sel; S.set({ sel: null }); preview.release(); viewer.clear(); drawInspector(); grid.tileOf(k)?.focus({ preventScroll: true }); return true; }
  return false;
}
export function nextKey(d) {
  const cur = S.get().sel;
  if (!list.length) return null;
  let i = list.findIndex((x) => x.key === cur);
  const only = S.get().onlyUnrated && (review.isOpen() || !isDesktop() || S.get().viewer);
  for (let n = 0; n < list.length; n++) {
    i = i < 0 ? (d > 0 ? 0 : list.length - 1) : (i + d + list.length) % list.length;
    const it = list[i];
    if (it.key !== cur && (!only || (!it._rating && !it._reject))) return it.key;
  }
  return null;
}
function goTo(key) {
  const it = lib.byKey.get(key);
  if (!it) return;
  select(key, { open: true });
  if (viewerSheet) replaceHash(`#browse/${it.kind}/${encodeURIComponent(it.name)}`);
}
export function step(d) { const k = nextKey(d); if (k) goTo(k); }
export function openReview() {
  if (!isDesktop()) { if (S.get().sel && !viewerSheet) select(S.get().sel, { open: true }); return; }
  review.open();
}
function reviewUnrated() {
  S.set({ onlyUnrated: true });
  const first = list.find((it) => !it._rating && !it._reject);
  if (!first) return;
  select(first.key, { open: true });
  if (isDesktop()) setTimeout(() => review.open(), 0);
}
function reveal(key) {
  const t = grid.ensureShown(key);
  if (!t) return;
  grid.focusKey(key, { scroll: false });
  const go = () => { if (t.isConnected) t.scrollIntoView({ block: "center" }); };
  go(); requestAnimationFrame(go); setTimeout(go, 150);      // tiles above may resize as they render
}
function relayout() {
  const sel = S.get().sel;
  if (isDesktop()) {
    if (viewerSheet?.isOpen()) { const s = viewerSheet; viewerSheet = null; closeLayer(s.layer); if (sel) S.set({ sel }); }
    drawInspector();
    if (sel) viewer.hostPreview();
  }
}

// ---------------------------------------------------------------- the inspector (desktop)
function drawInspector() {
  if (!inspector) return;
  const sel = S.get().sel;
  if (sel && isDesktop()) {
    if (viewer.el.parentNode !== inspector) put(inspector, viewer.el);
    viewer.hostPreview();
    return;
  }
  if (isDesktop() && viewer.el.parentNode === inspector) viewer.el.remove();
  if (sel && !isDesktop()) return;
  const t = S.get().target;
  const sum = room.summary(t, (s) => lib.items.length ? (itemForSpec(s, lib.items, lib.pidx)?.title || "") : "");
  const unrated = list.filter((it) => !it._rating && !it._reject).length;
  const keys = [["↑ ↓ ← →", "move and play"], ["Enter / f", "Review, full screen"], ["0–5", "stars"], ["x", "reject"], ["t", "tag"], ["o", "show on a screen"], ["e", "tune"], ["/", "search"]];
  put(inspector, h("div", { class: "inspector-empty" },
    h("h2", {}, "Pick something to see it move"),
    h("p", { class: "muted" }, "Select a picture: it plays live here. The arrow keys move on."),
    unrated ? button(`Review unrated (${unrated})`, { kind: "outline", onClick: () => reviewUnrated() }) : null,
    h("div", { class: "keys-list" }, keys.map(([k, w]) => [h("span", { class: "kbd" }, k), h("span", { class: "muted" }, w)])),
    h("div", { class: `screen-row tone-${sum.tone}`, style: { cursor: "default" } }, h("span", { class: "stxt" }, h("span", { class: "sname" }, room.nameOf(t)),
      h("span", { class: "sstate" }, h("span", { class: "dot" }), h("span", {}, sum.text))))));
}
S.on(["roomStatus", "target"], () => { if (!S.get().sel) drawInspector(); });

export const gridApi = () => grid;
export const focusSearch = () => { searchIn.focus(); searchIn.select(); };
export const hasViewerSheet = () => !!viewerSheet?.isOpen();
