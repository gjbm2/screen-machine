// The Lighting tab: the lights' scenes, effects and transitions, as the Library shows the screens' (owner, 27 Nov:
// "the scene, effect & transition library should also allow review of the lighting scenes as well as render scenes
// (with a suitable screen shot of an indication of what that scene looks like for each)").
//
// From show_service, through room's /show pass-through: GET /show/api/v1/lights/library (config/live/light_*.yaml as
// Library items, each with a picture taken of the real look engine's output drawn by the 3D room:
// tools/room/light_shots.py), GET /show/api/v1/lights/ratings and POST /show/api/v1/lights/rate. The same tile, grid
// and stars as the Library. A tile opens what the item is: its picture, what it does in words, the light groups here
// that can show it, and how it sits with the others: how much light it puts in the room, and what it comes down to
// when a low-power light (the laser, the derbies) is featured (docs/LIGHTS.md §7.5).
import { h, put, ROOM_BASE, debounce } from "../kit.js";
import { seg, button, chip, emptyState } from "../ui/controls.js";
import { createGrid } from "../browse/grid.js";
import { ratingStrip } from "../ui/rating.js";
import { openSheet } from "../ui/sheet.js";
import { toastErr } from "../ui/toast.js";
import { icon } from "../ui/icons.js";
import { friendly } from "../words.js";

const API = `${ROOM_BASE}/show/api/v1/lights`;
const KINDS = [["light", "Scenes"], ["light_transition", "Transitions"], ["light_effect", "Effects"]];
const L = { items: [], byKey: new Map(), groups: {}, lineups: {}, room: {}, stars: {}, loaded: false, error: null };
const ratings = new Map();
const st = { kind: "light", q: "", group: null, unrated: false, role: null, sel: null };
let root, grid, kindSeg, countEls = {}, quick, emptyHost, searchIn, sheet = null, loadedAt = 0;

async function getJson(path) {
  const r = await fetch(`${API}${path}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
}
const pct = (x) => `${Math.round(x * 100)}%`;
const lux = (x) => (x < 1 ? `${x.toFixed(1)}` : `${Math.round(x)}`);

function applyRating(it) {
  const e = ratings.get(it.key);
  it._rating = e?.stars || 0;
  it._reject = !!e?.reject;
  it._mytags = Array.isArray(e?.tags) ? [...e.tags] : [];
  it._looks = [];
}
function normalise(raw) {
  const it = { ...raw };
  it.title = it.label || it.name;
  it.thumb = it.thumb ? `${ROOM_BASE}/show${it.thumb}` : null;
  const li = it.light || {};
  const on = (li.groups || []).map((g) => g.label);
  const bits = [on.length ? on.join(", ") : "No light here can show it"];
  if (li.rest) bits.push("resting");
  else if (li.moments?.length) bits.push("while the room waits");
  it.sub = bits.join(" · ");
  it._hay = [it.title, it.name, it.description, ...on, ...(it.facets?.moods || []), ...(it.facets?.sections || []),
             ...(it.facets?.when || [])].filter(Boolean).join(" ").toLowerCase();
  applyRating(it);
  return it;
}

export async function load() {
  try {
    const [lib, rt] = await Promise.all([getJson("/library"), getJson("/ratings").catch(() => ({ ratings: {} }))]);
    ratings.clear();
    for (const [k, v] of Object.entries(rt.ratings || {})) ratings.set(k, v);
    L.items = (lib.items || []).map(normalise);
    L.byKey = new Map(L.items.map((i) => [i.key, i]));
    L.groups = lib.groups || {}; L.lineups = lib.lineups || {}; L.room = lib.room_light || {}; L.stars = lib.star_weights || {};
    L.loaded = true; L.error = null;
  } catch (e) {
    L.error = "The lighting catalogue couldn't be read (is the show service running?)";
  }
  loadedAt = Date.now();
  draw();
}

async function rate(it, patch) {
  const before = ratings.get(it.key);
  const next = { ...(before || {}), ...patch };
  if (!next.stars) delete next.stars;
  if (!next.reject) delete next.reject;
  if (Object.keys(next).filter((k) => k !== "at").length) ratings.set(it.key, next); else ratings.delete(it.key);
  applyRating(it); refresh();
  try {
    const r = await fetch(`${API}/rate`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ key: it.key, ...patch }) });
    if (!r.ok) throw new Error(String(r.status));
    const d = await r.json();
    if (d.entry) ratings.set(it.key, d.entry); else ratings.delete(it.key);
  } catch (_) {
    if (before) ratings.set(it.key, before); else ratings.delete(it.key);
    toastErr("Couldn't save the rating");
  }
  applyRating(it); refresh();
}

function matches(it) {
  if (it.kind !== st.kind) return false;
  if (st.group && !(it.light?.groups || []).some((g) => g.id === st.group)) return false;
  if (st.unrated && (it._rating || it._reject)) return false;
  if (st.role === "resting" && !it.light?.rest) return false;
  if (st.role === "waiting" && !(it.light?.moments?.length)) return false;
  if (st.role === "playing" && (it.light?.rest || it.light?.moments?.length)) return false;
  return st.q.toLowerCase().split(/\s+/).filter(Boolean).every((t) => it._hay.includes(t));
}
function refresh() { grid?.refresh(); if (sheet?.isOpen() && st.sel) drawView(L.byKey.get(st.sel)); }

function draw() {
  if (!root) return;
  for (const [k] of KINDS) countEls[k].textContent = String(L.items.filter((i) => i.kind === k).length);
  // best rated first; what no light here can show goes last
  const can = (i) => ((i.light?.groups || []).length ? 1 : 0);
  const list = L.items.filter(matches).sort((a, b) => can(b) - can(a) || (b._rating || 0) - (a._rating || 0) || a.title.localeCompare(b.title));
  // quick chips: the light groups, the roles, unrated
  const chips = [];
  for (const [gid, g] of Object.entries(L.groups)) {
    const n = L.items.filter((i) => i.kind === st.kind && (i.light?.groups || []).some((x) => x.id === gid)).length;
    if (n) chips.push(chip(g.label, { pressed: st.group === gid, count: n, onClick: () => { st.group = st.group === gid ? null : gid; draw(); } }));
  }
  if (st.kind === "light") {
    for (const [r, word] of [["playing", "Playing"], ["resting", "Resting"], ["waiting", "While the room waits"]]) {
      chips.push(chip(word, { pressed: st.role === r, onClick: () => { st.role = st.role === r ? null : r; draw(); } }));
    }
  }
  const un = L.items.filter((i) => i.kind === st.kind && !i._rating && !i._reject).length;
  if (un) chips.push(chip("Unrated", { pressed: st.unrated, count: un, onClick: () => { st.unrated = !st.unrated; draw(); } }));
  put(quick, ...chips);
  if (L.error) put(emptyHost, emptyState(L.error, [button("Try again", { kind: "outline", onClick: () => load() })]));
  else if (L.loaded && !list.length) put(emptyHost, emptyState("Nothing matches.", [button("Clear", { kind: "outline", onClick: () => { st.q = ""; st.group = null; st.unrated = false; st.role = null; searchIn.value = ""; draw(); } })]));
  else put(emptyHost);
  grid.setList(list);
}

// ---------------------------------------------------------------- one item, opened
const fact = (label, ...kids) => h("div", { class: "lfact" }, h("div", { class: "lab" }, label), h("div", { class: "val" }, ...kids));
function makesRoom(it) {
  // what a featured low-power light does to this item's groups (docs/LIGHTS.md §7.5): the same sum the director does
  const out = [];
  const other = Number(L.room.other_lux ?? 1), min = 0.12;
  for (const [fid, f] of Object.entries(L.groups)) {
    if (!f.reads_under_lux) continue;
    const rows = (it.light?.groups || []).filter((g) => g.id !== fid && g.lux > 0.05).map((g) => {
      const lv = Math.max(min, Math.min(1, (f.reads_under_lux - other - f.lux) / g.lux));
      return lv < 0.995 ? `${g.label} to ${pct(lv)}` : `${g.label} as they are`;
    });
    if (rows.length) out.push(fact(`With the ${f.label.toLowerCase()} featured (they read under ${f.reads_under_lux} lux)`, rows.join("; ")));
  }
  return out;
}
// What the stars do: the art director draws a rated item more or less often (show_service light_scenes.LightRatings).
function starWords(it) {
  if (it._reject) return "Rejected: the lights never draw it while anything else fits.";
  const x = L.stars[String(it._rating || 0)];
  if (!it._rating || !x) return "Unrated: the lights draw it by how well it fits the music. Your stars change how often.";
  return x >= 1 ? `Your ${it._rating} stars: the lights draw it about ${x >= 10 ? Math.round(x) : x.toFixed(1).replace(/\.0$/, "")} times as often as an unrated one.`
    : `Your ${it._rating} star${it._rating > 1 ? "s" : ""}: the lights draw it about ${x >= 0.5 ? "as often as" : `a ${Math.round(1 / x) === 7 ? "seventh" : `${Math.round(1 / x)}th`} as often as`} an unrated one.`;
}
let viewEl = null;
function drawView(it) {
  if (!it || !viewEl) return;
  const li = it.light || {};
  const pic = it.thumb ? h("img", { class: "lpic", src: it.thumb, alt: `A picture of ${it.title}` })
    : h("div", { class: "lpic none" }, "No picture yet: run tools/room/light_shots.py");
  const facts = [];
  facts.push(fact("Plays on", (li.groups || []).length ? li.groups.map((g) => g.label).join(", ") : "No light group here can show it"));
  if (it.kind === "light" && li.share != null) {
    const g = (li.groups || []).filter((x) => x.lux > 0.05);
    facts.push(fact("Light in the room", li.rest && li.share < 0.01 ? "None: it rests dark"
      : `${pct(li.share)} of the group's full light on average` + (g.length ? ` (${g.map((x) => `${x.label}: about ${lux(x.lux)} lux`).join("; ")}, in a plain look)` : "")));
  }
  if (li.lands?.length) facts.push(fact("Lands", li.lands.map((s) => friendly(s)).join(", ")));
  if (it.facets?.when?.length) facts.push(fact("Fires when", it.facets.when.join(", ")));
  if (li.transition_kind) facts.push(fact("How the scenes swap", friendly(li.transition_kind)));
  if (it.facets?.sections?.length) facts.push(fact("Written for", it.facets.sections.map((s) => friendly(s)).join(", ")));
  if (li.moments?.length) facts.push(fact("While the room", li.moments.map((s) => friendly(s)).join(", ")));
  if (it.facets?.moods?.length) facts.push(fact("Feel", it.facets.moods.map((s) => friendly(s)).join(", ")));
  if (Array.isArray(it.energy)) facts.push(fact("Energy", `${pct(it.energy[0])} to ${pct(it.energy[1])}`));
  if (li.needs?.length) facts.push(fact("Needs a light that can", li.needs.map((s) => friendly(s)).join(", ")));
  facts.push(...makesRoom(it));
  const file = it.kind === "light" ? "light_scenes.yaml" : it.kind === "light_effect" ? "light_effects.yaml" : "light_transitions.yaml";
  put(viewEl, pic, h("h2", { class: "ltitle" }, it.title), h("p", { class: "ldesc" }, it.description || ""),
      ratingStrip(it, rate, { size: "big" }), h("p", { class: "lsrc", tid: "light-star-words" }, starWords(it)), h("div", { class: "lfacts" }, ...facts),
      h("p", { class: "lsrc" }, `config/live/${file} · ${it.name}`));
}
function open(it) {
  st.sel = it.key;
  if (!viewEl) viewEl = h("div", { class: "lview" });
  drawView(it);
  const wide = window.matchMedia("(min-width: 700px)").matches;
  if (!sheet?.isOpen()) sheet = openSheet({ id: "light-view", title: "", body: viewEl, side: wide, scrim: true, focus: false, tid: "light-view",
    onClose: () => { sheet = null; st.sel = null; grid.refresh(); } });
  grid.refresh();
}

const CSS = `
.lview { display: flex; flex-direction: column; gap: 12px; padding: 0 16px 24px; min-width: 0; }
.lview .lpic { width: 100%; aspect-ratio: 16 / 9; object-fit: cover; border-radius: 12px; background: #06070a; display: block; }
.lview .lpic.none { display: flex; align-items: center; justify-content: center; color: #8d93a5; font-size: 13px; padding: 12px; text-align: center; }
.lview .ltitle { font-size: 20px; margin: 4px 0 0; }
.lview .ldesc { margin: 0; color: var(--ink-2); }
.lview .lfacts { display: flex; flex-direction: column; gap: 10px; border-top: 1px solid var(--line, #e5e7eb); padding-top: 12px; }
.lview .lfact .lab { font-size: 12px; color: var(--ink-2); }
.lview .lfact .val { font-size: 14px; }
.lview .lsrc { font-size: 12px; color: var(--ink-2); margin: 4px 0 0; }
`;

export function mount(host) {
  root = host;
  document.head.append(h("style", {}, CSS));
  kindSeg = seg(KINDS.map(([k, l]) => { const n = h("span", { class: "n" }); countEls[k] = n; return [k, l, n]; }), st.kind,
                (k) => { st.kind = k; st.group = null; st.role = null; draw(); window.scrollTo({ top: 0 }); }, { aria: "Kind", itemTid: (k) => `lkind-${k}` });
  searchIn = h("input", { type: "search", tid: "light-search", placeholder: "Search lighting…", "aria-label": "Search", autocomplete: "off", spellcheck: "false" });
  const setQ = debounce(() => { st.q = searchIn.value; draw(); }, 120);
  searchIn.addEventListener("input", setQ);
  const toolbar = h("div", { class: "toolbar" }, kindSeg, h("div", { class: "search-row" }, h("div", { class: "search" }, icon("search"), searchIn)));
  quick = h("div", { class: "chips-x quick", role: "toolbar", "aria-label": "Quick filters" });
  emptyHost = h("div", {});
  grid = createGrid({ mode: "browse", onSelect: (it) => open(it), rate: (it, p) => rate(it, p), selected: () => st.sel, tid: "light-grid" });
  put(root, h("div", { class: "browse" }, h("div", { class: "browse-main" }, toolbar, quick, emptyHost, grid.el, grid.more)));
  load();
}
export function shown() { if (Date.now() - loadedAt > 60000) load(); }
