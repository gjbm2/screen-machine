// Pure Library plumbing (design §6.4): normalise the index, the facet lists, matches / counts / facet values,
// sorting, the rating filter, and labels for specs.  DOM-free: tests/query.test.mjs runs it under node.
import { cleanTitle, authorOf, sourceLink, friendly, specName } from "../words.js";

export const KINDS = ["scene", "transition", "effect"];
export const NEW_MS = 3600e3;                       // "New": the plug-in file changed in the last hour

// The facets the Filter sheet shows, per kind (the client's list is authoritative; server facets only add values).
// group "main" shows in the sheet; "more" sits under "More filters".
export const FACET_DEFS = {
  look:      { label: "Look", group: "main" },
  energy:    { label: "Movement", group: "main", order: ["low", "mid", "high"], words: { low: "Calm", mid: "Medium", high: "Lively" } },
  dims:      { label: "Depth", group: "main", order: ["2d", "3d"], words: { "2d": "2D", "3d": "3D" } },
  stage:     { label: "Stage", group: "main", order: ["warp", "feedback", "glitch", "stylise", "colour", "light", "projection", "motion", "overlay"] },
  style:     { label: "Style", group: "main", order: ["gentle", "lively", "wild"] },
  technique: { label: "Technique", group: "more" },
  origin:    { label: "Source", group: "more", words: { core: "Built-in", imported: "Imported" } },
  licence:   { label: "Licence", group: "more", order: ["permissive", "attribution", "noncommercial", "unknown"] },
  mytags:    { label: "My tags", group: "more", empty: "No tags yet: add them in the Viewer (t)" },
  looks:     { label: "Saved looks", group: "more", words: { saved: "Has saved looks" }, empty: "No saved looks yet" },
  status:    { label: "Status", group: "more", order: ["accepted", "unreviewed", "needs_work", "building", "rejected"] },
  added:     { label: "Added", group: "more", words: { new: "New (last hour)" } },
};
export const FACETS = {
  scene:      ["look", "energy", "dims", "technique", "origin", "licence", "mytags", "looks", "status", "added"],
  transition: ["look", "energy", "style", "technique", "origin", "licence", "mytags", "looks", "status", "added"],
  effect:     ["look", "energy", "dims", "stage", "style", "technique", "origin", "licence", "mytags", "looks", "status", "added"],
};
export const facetWord = (facet, v) => FACET_DEFS[facet]?.words?.[v] ??
  (facet === "origin" && String(v).startsWith("lab") ? "Lab" : friendly(v));

const asList = (v) => (v == null ? [] : Array.isArray(v) ? v : [v]);
const uniq = (a) => [...new Set(a.filter((x) => x != null && x !== ""))];
function bands(e) {
  if (!Array.isArray(e) || e.length !== 2) return [];
  const [lo, hi] = e;
  return [["low", 0, 0.35], ["mid", 0.35, 0.65], ["high", 0.65, 1.0]].filter(([, a, b]) => lo < b && hi > a).map(([n]) => n);
}

/** /dev/library -> normalised items (raw paths; the caller puts the base on thumbs). */
export function normalise(idx, { now = Date.now(), withBase = (p) => p } = {}) {
  const out = [];
  for (const raw of idx?.items || []) {
    if (!raw || !KINDS.includes(raw.kind) || !raw.name) continue;
    const it = { ...raw };
    it.key = it.key || `${it.kind}/${it.name}`;
    it.thumb = it.thumb ? withBase(it.thumb) : null;
    it.preview = it.preview ? withBase(it.preview) : null;
    const f = it.facets && typeof it.facets === "object" ? it.facets : {};
    if (it.kind === "effect" && !it.stage) it.stage = asList(f.stage)[0] || null;
    if (it.spec == null) it.spec = it.preset || { [it.kind === "scene" ? "source" : it.kind]: it.plugin };
    it.status = it.status || asList(f.status)[0] || "accepted";
    it.origin = asList(f.origin)[0] || "core";
    it.presets = Array.isArray(it.presets) ? it.presets : [];
    it.title0 = it.label || it.name;                  // the original title
    it.title = cleanTitle(it.title0, it.plugin || it.name);
    it._author = authorOf(it.description);
    it._source = sourceLink(it.description);
    it._added = Date.parse(it.added_at) || 0;
    it._new = !!it._added && now - it._added < NEW_MS;
    it._e = Array.isArray(it.energy) ? (it.energy[0] + it.energy[1]) / 2 : null;
    it.ff = {                                         // the client's facet values
      look: uniq([...asList(f.family), ...asList(f.moods)].map((x) => String(x).toLowerCase())),
      energy: asList(f.energy).length ? asList(f.energy) : bands(it.energy),
      dims: asList(f.dims),
      stage: it.kind === "effect" ? uniq([it.stage, ...asList(f.stage)]) : [],
      style: asList(f.style ?? f.transition_style),
      technique: asList(f.technique),
      origin: [it.origin],
      licence: asList(f.licence).length ? asList(f.licence) : [],
      status: [it.status],
      added: it._new ? ["new"] : [],
      mytags: [], looks: [],
    };
    // server facets the client doesn't know: kept as extra values (never removes ours)
    for (const [k, v] of Object.entries(f)) if (!(k in it.ff) && k !== "family" && k !== "moods") it.ff[k] = asList(v);
    it._hay0 = [it.title, it.title0, it._author, it.name, it.plugin, it.preset, it.description, it.stage,
                ...it.presets.map((p) => `${p.name} ${p.label}`), ...Object.values(f).flat()]
      .filter(Boolean).join(" ").toLowerCase();
    it._hay = it._hay0;
    applyRating(it, null);
    out.push(it);
  }
  return out;
}

/** Fold the owner's rating entry {stars, reject, tags, looks} into an item. */
export function applyRating(it, e) {
  it._rating = e?.stars || 0;
  it._reject = !!e?.reject;
  it._mytags = Array.isArray(e?.tags) ? [...e.tags] : [];
  it._looks = Array.isArray(e?.looks) ? e.looks : [];
  it.ff.mytags = [...it._mytags];
  it.ff.looks = it._looks.length ? ["saved"] : [];
  it._hay = it._mytags.length ? `${it._hay0} ${it._mytags.join(" ").toLowerCase()}` : it._hay0;
  return it;
}

export const tokens = (q) => String(q || "").toLowerCase().split(/\s+/).filter(Boolean);

/** Stars {n: null|0..5, op: ge|eq|le} and rejects hide|show|only. Unrated counts as 0. */
export function ratingOk(it, st) {
  const rej = st.rejects || "hide";
  if (rej === "hide" && it._reject) return false;
  if (rej === "only" && !it._reject) return false;
  const n = st.stars?.n;
  if (n == null) return true;
  const r = it._rating || 0;
  if (n === 0) return r === 0;                    // "Unrated"
  const op = st.stars.op || "ge";
  return op === "eq" ? r === n : op === "le" ? r <= n : r >= n;
}

/** st: {q | qt, stars, rejects, facets: {kind: {facet: [values]}}} */
export function matches(it, st, kind = it.kind, except = null) {
  if (it.kind !== kind) return false;
  if (!ratingOk(it, st)) return false;
  const qt = st.qt || tokens(st.q);
  for (const t of qt) if (!it._hay.includes(t)) return false;
  const f = st.facets?.[kind] || {};
  for (const facet in f) {
    if (facet === except) continue;
    const vals = f[facet];
    if (!vals || !vals.length) continue;
    const have = it.ff[facet];
    if (!have || !have.some((v) => vals.includes(v))) return false;
  }
  return true;
}

export function counts(items, st) {
  const out = { scene: 0, transition: 0, effect: 0 };
  const s = { ...st, qt: st.qt || tokens(st.q) };
  for (const it of items) if (matches(it, s, it.kind)) out[it.kind]++;
  return out;
}

/** Values of one facet with counts after the other filters (the facet's own selection excluded). */
export function facetValues(items, st, kind, facet) {
  const s = { ...st, qt: st.qt || tokens(st.q) };
  const all = new Map();
  for (const it of items) if (it.kind === kind) for (const v of it.ff[facet] || []) all.set(v, 0);
  for (const v of st.facets?.[kind]?.[facet] || []) if (!all.has(v)) all.set(v, 0);
  for (const it of items) if (matches(it, s, kind, facet)) for (const v of it.ff[facet] || []) all.set(v, (all.get(v) || 0) + 1);
  const order = FACET_DEFS[facet]?.order;
  return [...all.entries()].sort((a, b) => order
    ? ((order.indexOf(a[0]) + 1 || 99) - (order.indexOf(b[0]) + 1 || 99)) || String(a[0]).localeCompare(String(b[0]))
    : b[1] - a[1] || String(a[0]).localeCompare(String(b[0])));
}

/** Which optional data exists (dormant features revive themselves when it does). */
export function hasData(items) {
  return {
    bench: items.some((i) => i.bench_ms_4k != null),
    heavy: items.some((i) => i.heavy === true),
    preview: items.some((i) => !!i.preview),
    auto: items.some((i) => i.auto != null),
  };
}

export const SORT_DEFS = [
  ["best", "Best first"], ["rating", "Highest rated"], ["az", "A–Z"], ["energy", "Calm to wild"], ["newest", "Newest"], ["fastest", "Fastest"],
];
export function sortOptions(items) {
  const d = hasData(items);
  return SORT_DEFS.filter(([k]) => k !== "fastest" || d.bench);
}

const STATUS_RANK = { accepted: 0, unreviewed: 1, needs_work: 2, building: 3, rejected: 4 };
const byTitle = (a, b) => a.title.localeCompare(b.title, undefined, { sensitivity: "base", numeric: true });
const score = (x) => (x._reject ? -1 : x._rating || 0);
export function sorted(list, sort) {
  const cmp = {
    az: byTitle,
    energy: (a, b) => (a._e ?? 9) - (b._e ?? 9) || byTitle(a, b),
    newest: (a, b) => b._added - a._added || byTitle(a, b),
    rating: (a, b) => score(b) - score(a) || byTitle(a, b),
    fastest: (a, b) => (a.bench_ms_4k ?? 1e9) - (b.bench_ms_4k ?? 1e9) || byTitle(a, b),
    best: (a, b) => score(b) - score(a) || ((a.load === "error") - (b.load === "error")) || (!!a.slow - !!b.slow) ||
      ((STATUS_RANK[a.status] ?? 1) - (STATUS_RANK[b.status] ?? 1)) || ((a.origin !== "core") - (b.origin !== "core")) ||
      ((b.weight ?? 1) - (a.weight ?? 1)) || byTitle(a, b),
  }[sort] || byTitle;
  return list.sort(cmp);
}

/** preset name -> {item, preset} */
export function presetIndex(items) {
  const m = new Map();
  for (const it of items) for (const p of it.presets || []) if (p?.name) m.set(`${it.kind}:${p.name}`, { item: it, preset: p });
  return m;
}

/** A label for a scene spec (what a screen shows): "Fluid ink · Neon swirl", "Live art", friendly(id). */
/** An item's title with one of its variations: "Fluid ink" + "Deep sea" -> "Fluid ink · Deep sea", but a label that
 *  already starts with the title stands alone ("Crossfade" + "Crossfade 3 s" -> "Crossfade 3 s", not both). */
export function variantTitle(title, presetLabel) {
  const pl = cleanTitle(presetLabel || "");
  const t = String(title || "");
  if (!pl || pl === t) return t;
  const lt = t.toLowerCase(), lp = pl.toLowerCase();
  if (lt && lp.startsWith(lt) && (lp.length === lt.length || /[\s·:,(\-]/.test(lp[lt.length]))) return pl;
  return `${t} · ${pl}`;
}

export function labelForSpec(spec, items, pidx = null, kind = "scene") {
  if (spec == null) return "";
  const idx = pidx || presetIndex(items);
  const name = specName(spec);
  if (!name) return "";
  if (name === "live_art" || name === "live-art") return "Live art";
  const hit = idx.get(`${kind}:${name}`);
  if (hit) {
    const it = hit.item;
    return it.presets.length > 1 && hit.preset.label ? variantTitle(it.title, hit.preset.label) : it.title;
  }
  const src = typeof spec === "object" ? (spec.source || spec.effect || spec.transition) : name;
  const it = items.find((x) => x.kind === kind && (x.plugin === src || x.name === src));
  if (it) return it.title;
  return friendly(name);
}

/** The item a spec belongs to (for ribbons: "On North screen"). */
export function itemForSpec(spec, items, pidx = null, kind = "scene") {
  if (spec == null) return null;
  const idx = pidx || presetIndex(items);
  const name = specName(spec);
  const hit = idx.get(`${kind}:${name}`);
  if (hit) return hit.item;
  const src = typeof spec === "object" ? (spec.source || spec.effect || spec.transition) : name;
  return items.find((x) => x.kind === kind && (x.plugin === src || x.name === src)) || null;
}

/** Active filter constraints, as chips: [{facet, value, label}] incl. stars / rejects. */
export function activeFilters(st, kind) {
  const out = [];
  const n = st.stars?.n;
  if (n != null) {
    const op = st.stars.op || "ge";
    out.push({ facet: "stars", value: `${op}${n}`, label: n === 0 ? "Unrated" : op === "eq" ? `Exactly ${n} star${n > 1 ? "s" : ""}` : op === "le" ? `${n} star${n > 1 ? "s" : ""} or less` : n === 5 ? "5 stars" : `${n}+ stars` });
  }
  if ((st.rejects || "hide") !== "hide") out.push({ facet: "rejects", value: st.rejects, label: st.rejects === "only" ? "Rejected only" : "Showing rejected" });
  for (const [facet, vals] of Object.entries(st.facets?.[kind] || {})) {
    for (const v of vals || []) out.push({ facet, value: v, label: facetWord(facet, v) });
  }
  return out;
}
export const badgeCount = (st, kind) => activeFilters(st, kind).length;
