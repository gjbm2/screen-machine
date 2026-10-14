// Library gallery for the dev console (docs/EFFECTS_LAYER_DESIGN.md §I.1-I.2).
// export mount({library, fxbus}): a tagged, filterable gallery of every scene, transition and effect (core, lab,
// imported) from GET /dev/library, with thumbnails, hover / tap previews, favourites, a next-cue composer and the
// FX bus panel (fxbus.js).  Defensive: /dev/library missing -> the catalog without thumbnails; ?mock=1 -> the
// library-mock.json fixture and an in-memory bus (add &mockx=4 to multiply the items for a 300+ card test).
import { h, put, add, http, ctx, debounce, hashHue, ago, store, toast, explain, fmt, LIVE_STATES, STAGES } from "/static/libkit.js";
import { mountFxBus } from "/static/fxbus.js";
import { LivePlayer } from "/static/live-player.js";

const KINDS = [["scene", "Scenes"], ["transition", "Transitions"], ["effect", "Effects"]];
const FAV_KEY = "sm.library.favs";
const UI_KEY = "sm.library.ui";
const BATCH = 60;                                    // cards rendered per page of the infinite scroll
const STATUS_RANK = { accepted: 0, unreviewed: 1, needs_work: 2, building: 3, rejected: 4 };
const VALUE_ORDER = { energy: ["low", "mid", "high"], stage: STAGES, status: Object.keys(STATUS_RANK), origin: ["core", "imported"],
                      licence: ["permissive", "attribution", "noncommercial", "unknown"], dims: ["2d", "3d"], style: ["gentle", "lively", "wild"] };
const DEFAULT_FACETS = {
  scene: ["dims", "family", "origin", "moods", "technique", "energy", "licence"],
  transition: ["family", "origin", "style", "technique", "energy", "licence"],
  effect: ["stage", "dims", "origin", "moods", "style", "technique", "energy", "licence"],
};
const NEW_MS = 3600e3;                               // "new": the plug-in file changed in the last hour
const REFRESH_MS = 20000;                            // the server rescans plug-ins every 20 s
const SORTS = [["recommended", "Recommended"], ["name", "Name"], ["energy", "Energy: calm → wild"], ["newest", "Newest"], ["fastest", "Fastest"]];
const ACTION = { scene: "Cue", transition: "Use next", effect: "+ FX" };

const asList = (v) => (v == null ? [] : Array.isArray(v) ? v : [v]);
function bands(e) {
  if (!Array.isArray(e) || e.length !== 2) return [];
  const [lo, hi] = e;
  return [["low", 0, 0.35], ["mid", 0.35, 0.65], ["high", 0.65, 1.0]].filter(([, a, b]) => lo < b && hi > a).map(([n]) => n);
}
// a plug-in the GPU guard tripped (or calibration found too slow / broken): greyed, never auto-picked
const slowText = (sl) => !sl ? null : sl.source === "calibration"
  ? `${sl.status === "broken" ? "broken" : "too slow"} on GPU ${sl.gpu} (calibration: ${sl.calibration}${sl.ms != null ? `, ${fmt(sl.ms)} ms` : ""}${sl.budget_ms != null ? ` / ${fmt(sl.budget_ms)} ms budget` : ""}) · not auto-picked`
  : `slow: ${fmt(sl.ms)} ms of GPU per frame (budget ${fmt(sl.budget_ms)} ms, ${sl.profile || "?"}) · not auto-picked`;
const shortOrigin = (o) => (!o || o === "core" ? "core" : String(o).startsWith("lab") ? "lab" : String(o).split(":")[0]);

// ---------------------------------------------------------------- index helpers
function facetsFromTags(kind, tags = {}, extra = {}) {
  const t = tags || {};
  const f = {
    family: asList(t.family), stage: asList(extra.stage || t.stage), dims: asList(t.dims), technique: asList(t.technique),
    moods: asList(t.moods), style: asList(t.style ?? t.transition_style), energy: bands(t.energy),
    origin: [extra.origin || t.origin || "core"], status: [extra.status || t.status || "accepted"], licence: [extra.licence || t.licence || "permissive"],
  };
  return Object.fromEntries((DEFAULT_FACETS[kind] || []).map((k) => [k, f[k] || []]));
}

// The /api/catalog as a library index (no thumbnails): used until the server has GET /dev/library.
function indexFromCatalog(c) {
  const items = [];
  const push = (kind, name, o) => items.push({
    key: `${kind}/${name}`, kind, name, preset: o.preset ?? null, plugin: o.plugin, label: o.label || name, description: o.description || null,
    spec: o.spec, stage: o.stage, duration_s: o.duration_s, energy: o.tags?.energy || null, auto: !!o.tags && o.preset != null,
    facets: facetsFromTags(kind, o.tags, { stage: o.stage, origin: o.origin, licence: o.license_class }),
    params: o.params, thumb: null, preview: null, bench_ms_4k: null, heavy: !!o.tags?.heavy, load: "ok", runtime_errors: 0, added_at: null,
    weight: o.tags?.weight,
  });
  const P = c.presets || {};
  for (const [n, p] of Object.entries(P.scenes || {})) push("scene", n, { ...p, preset: n, plugin: p.source, spec: n });
  for (const s of c.sources || []) if (!s.internal) push("scene", `@${s.name}`, { ...s, plugin: s.name, spec: { source: s.name } });
  for (const [n, p] of Object.entries(P.transitions || {})) push("transition", n, { ...p, preset: n, plugin: p.transition, spec: n, duration_s: p.params?.duration_s });
  for (const t of c.transitions || []) if (!t.internal) push("transition", `@${t.name}`, { ...t, plugin: t.name, spec: { transition: t.name } });
  const stageOf = (name) => (c.effects || []).find((e) => e.name === name)?.stage;
  for (const [n, p] of Object.entries(P.effects || {})) push("effect", n, { ...p, preset: n, plugin: p.effect, spec: n, stage: p.stage || stageOf(p.effect) });
  for (const e of c.effects || []) if (!e.internal) push("effect", `@${e.name}`, { ...e, plugin: e.name, spec: { effect: e.name } });
  const slow = new Map((c.slow || []).map((x) => [`${x.kind === "source" ? "scene" : x.kind}/${x.name}`, x]));
  for (const it of items) it.slow = slow.get(`${it.kind}/${it.plugin}`) || null;
  return { rev: 0, generated_at: null, stages: c.stages || STAGES, facets: DEFAULT_FACETS, items };
}

function normalise(idx, multiply = 1) {
  const out = [];
  for (const raw of idx.items || []) {
    if (!raw || !DEFAULT_FACETS[raw.kind] || !raw.name) continue;
    const it = { ...raw };
    it.key = it.key || `${it.kind}/${it.name}`;
    it.label = it.label || it.name;
    it.facets = it.facets && typeof it.facets === "object" ? it.facets : {};
    if (it.kind === "effect" && !it.stage) it.stage = it.facets.stage?.[0] || null;
    if (it.spec == null) it.spec = it.preset || { [it.kind === "scene" ? "source" : it.kind]: it.plugin };
    it.status = it.facets.status?.[0] || "accepted";
    it.origin = it.facets.origin?.[0] || "core";
    it._added = Date.parse(it.added_at) || 0;
    it._e = Array.isArray(it.energy) ? (it.energy[0] + it.energy[1]) / 2 : null;
    it._hay = [it.label, it.name, it.plugin, it.preset, it.description, it.stage, ...Object.values(it.facets).flat()].filter(Boolean).join(" ").toLowerCase();
    out.push(it);
  }
  for (let m = 1; m < multiply; m++) {          // mock stress test only
    for (const it of out.slice(0, out.length / m)) out.push({ ...it, key: `${it.key}~${m}`, name: `${it.name}~${m}`, label: `${it.label} #${m + 1}` });
  }
  return out;
}

// ---------------------------------------------------------------- mount
export function mount({ library, fxbus } = {}) {
  if (!library) return null;
  const q = new URLSearchParams(location.search);
  ctx.mock = q.has("mock") && q.get("mock") !== "0";
  const multiply = Math.max(1, Math.min(10, parseInt(q.get("mockx") || "1", 10) || 1));

  const ui = { kind: "scene", sort: "recommended", favOnly: false, filters: {}, nextVia: null, at: null, ...store.get(UI_KEY, {}) };
  const S = {
    items: [], byKey: new Map(), idx: null, source: null, note: null, loadedAt: 0,
    kind: DEFAULT_FACETS[ui.kind] ? ui.kind : "scene", sort: ui.sort, favOnly: !!ui.favOnly, q: "", qt: [],
    filters: {}, favs: new Set(asList(store.get(FAV_KEY, []))), nextVia: null, nextFx: [], at: ui.at || null,
    limit: BATCH, detail: null, filtersOpen: false, list: [], current: null, errors: [], errorsDismissed: 0, lab: null, labAvail: "unknown",
  };
  for (const k of Object.keys(DEFAULT_FACETS)) {
    S.filters[k] = {};
    for (const [f, vals] of Object.entries(ui.filters?.[k] || {})) if (Array.isArray(vals) && vals.length) S.filters[k][f] = new Set(vals);
  }
  const saveUi = () => store.set(UI_KEY, {
    kind: S.kind, sort: S.sort, favOnly: S.favOnly, at: S.at, nextVia: S.nextVia?.key || null,
    filters: Object.fromEntries(Object.entries(S.filters).map(([k, f]) => [k, Object.fromEntries(Object.entries(f).filter(([, s]) => s.size).map(([n, s]) => [n, [...s]]))])),
  });

  // ---- skeleton
  const srcNote = h("span", { class: "small lib-sub" });
  const labBadge = h("span", { class: "lib-pill", hidden: true });
  const tabs = h("div", { class: "lib-tabs", role: "tablist", "aria-label": "library kind" });
  const tabEls = {};
  for (const [k, label] of KINDS) {
    const n = h("span", { class: "n" });
    const b = h("button", { class: "lib-tab", role: "tab", id: `lib-tab-${k}`, "aria-selected": "false", "aria-controls": "lib-grid", tabindex: "-1", onclick: () => setKind(k) }, label, n);
    b.addEventListener("keydown", (e) => {
      const i = KINDS.findIndex(([x]) => x === k);
      if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
        const j = (i + (e.key === "ArrowRight" ? 1 : KINDS.length - 1)) % KINDS.length;
        setKind(KINDS[j][0]); tabEls[KINDS[j][0]].b.focus(); e.preventDefault();
      }
    });
    tabEls[k] = { b, n };
    tabs.appendChild(b);
  }
  const search = h("input", { type: "search", class: "lib-search", placeholder: "Search names, tags, plug-ins   ( / )", "aria-label": "search the library", autocomplete: "off", spellcheck: "false" });
  const filtN = h("span", { class: "n", hidden: true });
  const filtBtn = h("button", { class: "lib-filtbtn", "aria-expanded": "false", "aria-controls": "lib-filters" }, "Filters", filtN);
  const sortSel = h("select", { "aria-label": "sort" }, SORTS.map(([v, t]) => h("option", { value: v }, t)));
  sortSel.value = SORTS.some(([v]) => v === S.sort) ? S.sort : "recommended";
  const favBtn = h("button", { class: "lib-iconbtn", title: "favourites only (★ on a card to add)", "aria-pressed": String(S.favOnly), "aria-label": "favourites only" }, "★");
  const active = h("div", { class: "lib-active", "aria-label": "active filters" });
  const filters = h("div", { class: "lib-filters", id: "lib-filters", hidden: true });
  const next = h("div", { class: "lib-next", "aria-label": "next cue" });
  const banner = h("div", { class: "lib-banner", hidden: true, role: "alert" });
  const noteEl = h("div", { class: "lib-warnbox", hidden: true, style: { marginTop: "8px" } });
  const grid = h("div", { class: "lib-grid", id: "lib-grid", role: "list", "aria-label": "library items" });
  const scroller = h("div", { class: "lib-scroll" }, grid);
  const status = h("div", { class: "lib-note", style: { marginTop: "6px" } });
  const labBody = h("div", { class: "body" }, "–");
  const labSum = h("summary", {}, h("span", {}, "Plug-ins"), h("span", { class: "small" }, "–"));
  const lab = h("details", { class: "lib-lab" }, labSum, labBody);
  const detailEl = h("div", { class: "lib-detail", role: "region", "aria-label": "details" });

  put(library, 
    h("h2", { class: "lib-h2" }, "Library", srcNote, h("span", { style: { flex: 1 } }), labBadge),
    h("div", { class: "lib-bar" }, tabs, search, filtBtn, sortSel, favBtn),
    active, filters, next, banner, noteEl, scroller, status, lab);

  const bus = fxbus ? mountFxBus(fxbus, {
    lookup: (spec) => {
      if (typeof spec === "string") return S.byKey.get(`effect/${spec}`) || null;
      if (spec?.effect) return S.byKey.get(`effect/@${spec.effect}`) || { plugin: spec.effect, preset: null, label: spec.effect, stage: "stylise" };
      return null;
    },
    effects: () => S.items.filter((i) => i.kind === "effect" && i.status !== "rejected" && i.load !== "error"),
  }) : null;

  // ---- loading
  let loading = null;
  async function loadIndex(reason = "") {
    if (loading) return loading;
    loading = (async () => {
      let idx = null, source = null, note = null;
      if (ctx.mock) {
        const r = await http("GET", "/static/library-mock.json", undefined, { auth: false });
        if (r.ok && r.data?.items) { idx = r.data; source = "mock"; ctx.mockData = r.data._mock || {}; }
        else note = explain(r, "mock fixture");
      } else {
        const r = await http("GET", "/dev/library");
        if (r.ok && Array.isArray(r.data?.items)) { idx = r.data; source = "library"; }
        else {
          const c = ctx.catalog() || (await http("GET", "/api/catalog")).data;
          if (c && c.presets) {
            idx = indexFromCatalog(c); source = "catalog";
            note = r.status === 404 ? "no /dev/library on this server yet: the catalog, without thumbnails" : explain(r, "/dev/library");
          } else note = r.status === 401 || !c ? "save the bearer token to load the library" : explain(r, "library");
        }
      }
      S.source = source; S.note = note;
      if (idx) {
        S.idx = idx; S.items = normalise(idx, source === "mock" ? multiply : 1); S.byKey = new Map(S.items.map((i) => [i.key, i]));
        S.loadedAt = Date.now();
        if (ui.nextVia && !S.nextVia) S.nextVia = S.byKey.get(ui.nextVia) || null;
        for (const [k, c] of [...cards]) {
          const n = S.byKey.get(k);
          if (!n || n.thumb !== c.it.thumb || n.load !== c.it.load || n.preview !== c.it.preview) cards.delete(k);
          else c.it = n;
        }
        if (reason) ctx.log(`library reloaded (${reason}): ${S.items.length} items`, "ev");
      }
      renderAll();
      library.dataset.ready = idx ? "1" : "0";
    })();
    try { await loading; } finally { loading = null; }
  }

  async function loadLab() {
    if (ctx.mock) { S.lab = ctx.mockData?.lab_status || null; S.labAvail = S.lab ? "ok" : "missing"; renderLab(); return; }
    const r = await http("GET", "/dev/lab/status");
    if (r.ok && r.data) { S.lab = r.data; S.labAvail = "ok"; }
    else { S.lab = null; S.labAvail = r.status === 404 ? "missing" : "error"; S.labErr = explain(r, "lab status"); }
    renderLab();
  }

  // ---- filtering
  function hideRejected(kind) { return !S.filters[kind].status?.has("rejected"); }
  function matches(it, kind, except) {
    if (it.kind !== kind) return false;
    if (S.favOnly && !S.favs.has(it.key)) return false;
    for (const t of S.qt) if (!it._hay.includes(t)) return false;
    const f = S.filters[kind];
    for (const facet in f) {
      if (facet === except) continue;
      const set = f[facet];
      if (!set.size) continue;
      const vals = it.facets[facet];
      if (!vals || !vals.some((v) => set.has(v))) return false;
    }
    if (except !== "status" && hideRejected(kind) && it.status === "rejected") return false;
    return true;
  }
  function sorted(list) {
    const by = S.sort;
    const name = (a, b) => a.label.localeCompare(b.label, undefined, { sensitivity: "base", numeric: true });
    const cmp = {
      name,
      energy: (a, b) => (a._e ?? 9) - (b._e ?? 9) || name(a, b),
      newest: (a, b) => b._added - a._added || name(a, b),
      fastest: (a, b) => (a.bench_ms_4k ?? 1e9) - (b.bench_ms_4k ?? 1e9) || name(a, b),
      recommended: (a, b) =>
        (S.favs.has(b.key) - S.favs.has(a.key)) || ((a.load === "error") - (b.load === "error")) ||
        ((STATUS_RANK[a.status] ?? 1) - (STATUS_RANK[b.status] ?? 1)) || ((a.preset == null) - (b.preset == null)) ||
        ((b.weight ?? 1) - (a.weight ?? 1)) || name(a, b),
    }[by] || name;
    return list.sort(cmp);
  }
  const activeCount = (kind) => Object.values(S.filters[kind]).reduce((n, s) => n + s.size, 0);

  // ---- render: tabs, chips, filters, grid
  function renderTabs() {
    for (const [k] of KINDS) {
      const n = S.items.reduce((acc, it) => acc + (it.kind === k && (!S.favOnly || S.favs.has(it.key)) && S.qt.every((t) => it._hay.includes(t)) && !(hideRejected(k) && it.status === "rejected") ? 1 : 0), 0);
      tabEls[k].n.textContent = String(n);
      tabEls[k].b.setAttribute("aria-selected", String(k === S.kind));
      tabEls[k].b.tabIndex = k === S.kind ? 0 : -1;
    }
  }

  function toggleFilter(kind, facet, value, on) {
    const f = S.filters[kind];
    if (!f[facet]) f[facet] = new Set();
    const want = on ?? !f[facet].has(value);
    want ? f[facet].add(value) : f[facet].delete(value);
    if (!f[facet].size) delete f[facet];
    S.limit = BATCH; saveUi(); renderAll();
  }

  function renderActive() {
    const f = S.filters[S.kind];
    const chips = [];
    for (const [facet, set] of Object.entries(f)) for (const v of set) {
      chips.push(h("span", { class: "lib-pill acc" }, `${facet}: ${v}`,
        h("button", { "aria-label": `remove filter ${facet} ${v}`, title: "remove", onclick: () => toggleFilter(S.kind, facet, v, false) }, "×")));
    }
    if (chips.length > 1) chips.push(h("button", { class: "lib-iconbtn", style: { fontSize: "11px" }, onclick: () => { S.filters[S.kind] = {}; S.limit = BATCH; saveUi(); renderAll(); } }, "clear all"));
    put(active, ...chips);
    const n = activeCount(S.kind);
    filtN.textContent = String(n); filtN.hidden = !n;
  }

  function renderFilters() {
    filters.hidden = !S.filtersOpen;
    filtBtn.setAttribute("aria-expanded", String(S.filtersOpen));
    if (!S.filtersOpen) return;
    const kind = S.kind, facets = S.idx?.facets?.[kind] || DEFAULT_FACETS[kind];
    const ofKind = S.items.filter((i) => i.kind === kind);
    const kids = [];
    for (const facet of facets) {
      const all = new Map();
      for (const it of ofKind) for (const v of it.facets[facet] || []) all.set(v, 0);
      for (const v of S.filters[kind][facet] || []) all.set(v, 0);
      if (!all.size) continue;
      for (const it of ofKind) if (matches(it, kind, facet)) for (const v of it.facets[facet] || []) all.set(v, (all.get(v) || 0) + 1);
      const order = VALUE_ORDER[facet];
      const vals = [...all.entries()].sort((a, b) => order ? ((order.indexOf(a[0]) + 1 || 99) - (order.indexOf(b[0]) + 1 || 99)) || a[0].localeCompare(b[0])
                                                     : b[1] - a[1] || a[0].localeCompare(b[0]));
      const sel = S.filters[kind][facet] || new Set();
      kids.push(h("div", { class: "fname" }, facet),
        h("div", { class: "chips" }, vals.map(([v, n]) => h("button", {
          class: `lib-fchip${n ? "" : " zero"}`, "aria-pressed": String(sel.has(v)), onclick: () => toggleFilter(kind, facet, v),
          title: facet === "status" && v === "rejected" ? "rejected items are hidden unless this is on" : `${n} with ${facet} ${v}`,
        }, facet === "origin" ? v.replace(/^lab:/, "lab · ") : v, h("span", { class: "n" }, String(n))))));
    }
    const rej = ofKind.filter((i) => i.status === "rejected").length;
    kids.push(h("div", { class: "foot" },
      rej && hideRejected(kind) ? h("span", { class: "lib-note" }, `${rej} rejected hidden`) : null,
      h("button", { onclick: () => { S.filters[kind] = {}; S.limit = BATCH; saveUi(); renderAll(); } }, "Reset"),
      h("button", { onclick: () => { S.filtersOpen = false; renderFilters(); filtBtn.focus(); } }, "Done")));
    put(filters, ...kids);
  }

  function renderNext() {
    const st = ctx.state(), sid = ctx.sid();
    const showIt = S.kind === "scene" || S.nextVia || S.nextFx.length;
    next.hidden = !showIt;
    if (!showIt) return;
    const atSel = h("select", { "aria-label": "cue timing" }, h("option", { value: "beat" }, "on the beat"), h("option", { value: "now" }, "now"));
    atSel.value = S.at || document.getElementById("cAt")?.value || "beat";
    atSel.addEventListener("change", () => { S.at = atSel.value; saveUi(); });
    const via = S.nextVia
      ? h("span", { class: "lib-pill acc", title: "set from a Transition card: Use next" }, `via ${S.nextVia.label}`,
          h("button", { "aria-label": "clear the next transition", onclick: () => useNext(S.nextVia) }, "×"))
      : h("span", { class: "lib-note" }, "via the director's pick");
    const fxs = S.nextFx.map((e) => h("span", { class: "lib-pill", title: "scene-attached effect (part of the cued scene)" }, `+ ${e.label}`,
      h("button", { "aria-label": `detach ${e.label}`, onclick: () => attachNext(e) }, "×")));
    const live = sid && LIVE_STATES.has(st);
    put(next, h("span", { class: "lab" }, "Next cue"), via, ...fxs, atSel,
      h("span", { class: "lib-note", style: { flex: "1 1 auto", textAlign: "right" } },
        live ? "Cue on a Scene card" : ctx.mock ? "mock mode: cues are simulated without a session" : "Start the live demo to cue scenes"));
  }

  function renderBanner() {
    const sid = ctx.sid();
    const errs = S.errors.filter((e) => e.session === sid || ctx.mock);
    const n = errs.length;
    if (!n || n <= S.errorsDismissed) { banner.hidden = true; return; }
    banner.hidden = false;
    const list = h("ul", { hidden: !S.errorsOpen }, errs.slice(-12).map((e) => h("li", {},
      h("b", {}, `${e.kind} ${e.name}`), e.preset ? ` (${e.preset})` : "", `: ${e.action || "failed"}${e.phase ? " at " + e.phase : ""}${e.count > 1 ? ` ×${e.count}` : ""} — `, h("code", {}, e.detail || "?"))));
    put(banner, 
      h("div", { class: "row" }, h("span", {}, `⚠ ${n} plug-in error${n > 1 ? "s" : ""} in this session (the show kept running)`),
        h("button", { onclick: () => { S.errorsOpen = !S.errorsOpen; renderBanner(); } }, S.errorsOpen ? "Hide" : "Show"),
        h("button", { onclick: () => { S.errorsDismissed = n; renderBanner(); }, "aria-label": "dismiss" }, "Dismiss")), list);
  }

  // ---- cards
  const cards = new Map();           // key -> {el, it, thumb, img, btn, star, ...}
  let thumbIO = null;
  try {
    thumbIO = new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (!e.isIntersecting) continue;
        const img = e.target;
        thumbIO.unobserve(img);
        if (img.dataset.src && !img.src) img.src = img.dataset.src;
      }
    }, { root: scroller, rootMargin: "300px 0px" });
  } catch (_) { thumbIO = null; }

  function chipsFor(it) {
    const f = it.facets, out = [];
    if (it.kind === "effect") out.push(h("span", { class: "lib-chip stage" }, it.stage || "effect"));
    else if (f.family?.[0]) out.push(h("span", { class: "lib-chip" }, f.family[0]));
    if (it.kind === "transition" && it.duration_s != null) out.push(h("span", { class: "lib-chip" }, `${fmt(it.duration_s, 1).replace(/\.0$/, "")} s`));
    if (it.kind !== "transition" && f.dims?.[0]) out.push(h("span", { class: "lib-chip" }, f.dims[0]));
    if (f.style?.[0]) out.push(h("span", { class: "lib-chip" }, f.style[0]));
    else if (f.moods?.[0]) out.push(h("span", { class: "lib-chip" }, f.moods[0]));
    return out.slice(0, 3);
  }

  function makeCard(it) {
    const h1 = hashHue(it.plugin || it.name), h2 = hashHue(it.name, 7);
    const ph = h("span", { class: "lib-ph", "aria-hidden": "true" }, h("span", {}, (it.label || "?").replace(/[^A-Za-z0-9]/g, "").slice(0, 2) || "?", h("small", {}, it.kind === "effect" ? it.stage || "" : it.plugin || "")));
    const thumb = h("div", { class: "lib-thumb" }, ph);
    thumb.style.setProperty("--h1", h1); thumb.style.setProperty("--h2", h2);
    let img = null;
    if (it.thumb) {
      img = h("img", { alt: "", loading: "lazy", decoding: "async", draggable: "false" });
      img.dataset.src = it.thumb;
      img.addEventListener("load", () => img.classList.add("ok"));
      img.addEventListener("error", () => img.remove());
      thumb.appendChild(img);
      if (thumbIO) thumbIO.observe(img); else img.src = it.thumb;
    }
    const badges = h("span", { class: "lib-badges" });
    const star = h("button", { class: "lib-star", "aria-label": `favourite ${it.label}`, title: "favourite (f)", tabindex: "-1" }, "★");
    star.addEventListener("click", (e) => { e.stopPropagation(); toggleFav(it.key); });
    add(thumb, badges, star, it.preview ? h("span", { class: "lib-play", "aria-hidden": "true" }, "▶ preview") : null);
    const dot = h("span", { class: "lib-dot" });
    const title = h("div", { class: "lib-title" }, dot, h("span", { class: "t" }, it.label));
    const chips = h("div", { class: "lib-chips" }, chipsFor(it));
    const btn = h("button", { class: "go", tabindex: "-1" }, ACTION[it.kind]);
    btn.addEventListener("click", (e) => { e.stopPropagation(); primary(it); });
    const more = h("button", { class: "more", "aria-label": "details", title: "details (i)", tabindex: "-1", "aria-expanded": "false" }, "⋯");
    more.addEventListener("click", (e) => { e.stopPropagation(); toggleDetail(it.key); });
    const el = h("article", { class: "lib-card", tabindex: "-1", role: "listitem", "aria-label": `${it.label} (${it.kind})`, dataset: { key: it.key } },
      thumb, h("div", { class: "lib-body" }, title, chips, h("div", { class: "lib-actions" }, btn, more)));
    const c = { el, it, thumb, img, badges, star, dot, btn, more, chips };
    // previews: hover (mouse), tap on the thumbnail (touch), focus (keyboard)
    let hoverT = null, lastPointer = "mouse";
    el.addEventListener("pointerenter", (e) => { if (e.pointerType === "mouse") { clearTimeout(hoverT); hoverT = setTimeout(() => startPreview(c), 140); } });
    el.addEventListener("pointerleave", (e) => { if (e.pointerType === "mouse") { clearTimeout(hoverT); if (playing?.c === c) stopPreview(); } });
    thumb.addEventListener("pointerdown", (e) => { lastPointer = e.pointerType; });
    el.addEventListener("click", (e) => { if (!e.target.closest("button")) livePreview(it); });   // click = see it live
    el.addEventListener("focus", () => { roving = it.key; clearTimeout(hoverT); hoverT = setTimeout(() => { if (document.activeElement === el) startPreview(c); }, 350); });
    el.addEventListener("blur", () => { clearTimeout(hoverT); if (playing?.c === c && !el.matches(":hover")) stopPreview(); });
    cards.set(it.key, c);
    updateCard(c);
    return c;
  }

  function updateCard(c) {
    const it = c.it;
    const fav = S.favs.has(it.key);
    c.star.setAttribute("aria-pressed", String(fav));
    c.dot.className = `lib-dot ${it.status}`;
    c.dot.title = `status: ${it.status}`;
    const errText = it.load === "error" ? `import error: ${it.error || "?"}` : it.load === "stale" ? it.error
      : it.runtime_errors > 0 ? `${it.runtime_errors} runtime error(s)${it.runtime_error ? ": " + it.runtime_error : ""}` : null;
    const isNew = it._added && Date.now() - it._added < NEW_MS;
    c.el.title = it.load === "error" ? `BROKEN: ${it.error || "import error"}` : "";
    put(c.badges, 
      isNew ? h("span", { class: "lib-badge new", title: `added / changed ${ago(it.added_at)}` }, "new") : null,
      shortOrigin(it.origin) === "core" ? null : h("span", { class: "lib-badge lab", title: it.origin }, shortOrigin(it.origin)),
      it.heavy ? h("span", { class: "lib-badge heavy", title: `heavy: ${fmt(it.bench_ms_4k)} ms at 4K` }, "heavy") : null,
      it.slow ? h("span", { class: "lib-badge err", title: slowText(it.slow) }, it.slow.status === "broken" ? "broken" : "slow") : null,
      errText ? h("span", { class: "lib-badge err", title: errText }, "!") : null);
    c.el.classList.toggle("dim", it.load === "error" || it.status === "rejected" || !!it.slow);
    c.el.classList.toggle("now", it.kind === "scene" && !!S.current && (it.preset === S.current || it.name === S.current));
    c.el.classList.toggle("sel", it.kind === "transition" && S.nextVia?.key === it.key || it.kind === "effect" && S.nextFx.some((e) => e.key === it.key));
    c.el.classList.toggle("open", S.detail === it.key);
    c.more.setAttribute("aria-expanded", String(S.detail === it.key));
    updateAction(c.btn, it);
  }

  function updateAction(btn, it) {
    const st = ctx.state(), sid = ctx.sid();
    let label = ACTION[it.kind], dis = false, title = "", cls = "go";
    if (it.load === "error") { dis = true; title = `cannot load: ${it.error || "import error"}`; }
    else if (it.kind === "scene") {
      const live = sid && LIVE_STATES.has(st);
      dis = !live && !ctx.mock; cls = "go primary";
      title = live || ctx.mock ? `cue ${it.label}${S.nextVia ? " via " + S.nextVia.label : ""} (${S.at || "beat"})` : "Start the live demo first (Cue needs a live session)";
    } else if (it.kind === "transition") {
      const on = S.nextVia?.key === it.key;
      label = on ? "Next ✓" : "Use next"; cls = on ? "go on" : "go";
      title = on ? "the next cue goes via this transition (click to clear)" : "use this transition for the next cue";
    } else {
      const on = bus?.has(it.spec);
      const ok = bus?.canEdit();
      label = on ? "On bus ✓" : "+ FX"; cls = on ? "go on" : "go primary";
      dis = !bus || (!ok && !on);
      title = !bus ? "no FX bus panel" : on ? "on the FX bus (click to remove)" : ok ? "add to the master FX bus" : "needs a session with an FX bus (start the live demo)";
    }
    btn.textContent = label; btn.className = cls; btn.disabled = dis; btn.title = title;
  }

  function updateAllCards() { for (const c of cards.values()) if (c.el.isConnected) updateCard(c); }

  // ---- previews: only one plays at a time, created on demand (never 300 <video> elements)
  let playing = null;
  function startPreview(c, explicit = false) {
    if (!c.it.preview) { if (explicit) toast(`${c.it.label}: no preview yet`, "info", 1800); return; }
    if (playing?.c === c) return;
    stopPreview();
    const v = h("video", { muted: true, loop: true, playsinline: true, autoplay: true, preload: "auto", "aria-hidden": "true" });
    v.muted = true;
    v.addEventListener("playing", () => v.classList.add("on"));
    v.addEventListener("error", () => { if (playing?.v === v) stopPreview(); });
    v.src = c.it.preview;
    c.thumb.appendChild(v);
    v.play().catch(() => {});
    c.el.classList.add("playing");
    playing = { c, v };
  }
  function stopPreview() {
    if (!playing) return;
    const { c, v } = playing;
    playing = null;
    try { v.pause(); v.removeAttribute("src"); v.load(); } catch (_) {}
    v.remove();
    c.el.classList.remove("playing");
  }

  // ---- live preview: a card click plays the item LIVE in one reusable 720p session per tab (POST /dev/preview,
  // stream_service/preview.py); later clicks re-cue the same stream with a cut, so switching is instant
  const LP_CLIENT = (() => {
    const make = () => "t" + Math.random().toString(36).slice(2, 12);
    try { let c = sessionStorage.getItem("sm.preview.client"); if (!c) { c = make(); sessionStorage.setItem("sm.preview.client", c); } return c; }
    catch (_) { return make(); }
  })();
  const lpStage = h("div", { class: "lib-lp-stage" }, h("div", { class: "lib-lp-spin", "aria-hidden": "true" }));
  const lpTitle = h("span", { class: "lib-lp-title" });
  const lpNote = h("span", { class: "lib-lp-note" });
  const lp = h("div", { class: "lib-lp", role: "dialog", "aria-label": "live preview", hidden: true },
    h("div", { class: "lib-lp-bar" }, lpTitle, lpNote,
      h("button", { class: "lib-lp-close", "aria-label": "close the preview", title: "close (Esc)", onclick: () => liveStop() }, "×")),
    lpStage);
  document.body.appendChild(lp);
  let lpPlayer = null, lpUrl = null, lpKeep = null, lpSeq = 0, lpKey = null;
  const lpMark = () => { for (const c of cards.values()) c.el.classList.toggle("previewing", c.it.key === lpKey); };
  async function livePreview(it) {
    if (ctx.mock) { toast("mock mode: no live preview", "info", 1800); return; }
    if (it.load === "error") { toast(`${it.label}: cannot load (${it.error || "import error"})`, "err", 2500); return; }
    const seq = ++lpSeq;
    lpKey = it.key; lpMark();
    lp.hidden = false; lp.classList.add("loading");
    lpTitle.textContent = it.label;
    lpNote.textContent = it.kind === "transition" ? "looping between two scenes" : it.kind === "effect" ? "on a moving scene" : "live";
    const r = await http("POST", "/dev/preview", { client: LP_CLIENT, kind: it.kind, spec: it.spec });
    if (seq !== lpSeq) return;                                   // a newer click won
    if (!r.ok) { lp.classList.remove("loading"); lpNote.textContent = explain(r, "preview"); return; }
    const att = r.data.attach || {};
    if (att.url && att.url !== lpUrl) {                           // first click (or the old session ended): attach
      if (lpPlayer) lpPlayer.stop();
      lpUrl = att.url;
      lpPlayer = new LivePlayer({ url: att.url, targetBufferMs: att.target_buffer_ms ?? 300, mount: lpStage, sendStats: false });
      lpPlayer.start();
      lpPlayer.onFirstFrame(() => { if (seq === lpSeq) lp.classList.remove("loading"); });
    } else {
      setTimeout(() => { if (seq === lpSeq) lp.classList.remove("loading"); }, 300);   // same stream, cut in
    }
    clearInterval(lpKeep);
    lpKeep = setInterval(() => { http("POST", "/dev/preview", { client: LP_CLIENT, keepalive: true }); }, 10000);
  }
  function liveStop() {
    lpSeq++; lpKey = null; lpMark();
    clearInterval(lpKeep); lpKeep = null;
    if (lpPlayer) { lpPlayer.stop(); lpPlayer = null; lpUrl = null; }
    lp.hidden = true; lp.classList.remove("loading");
    http("DELETE", `/dev/preview?client=${encodeURIComponent(LP_CLIENT)}`);
  }
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !lp.hidden && !S.detail) liveStop(); });

  // ---- grid with progressive rendering
  const sentinel = h("div", { class: "lib-more-row" });
  let moreIO = null;
  try {
    moreIO = new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting) && S.limit < S.list.length) { S.limit += BATCH; renderGrid(true); } }, { root: scroller, rootMargin: "400px 0px" });
    moreIO.observe(sentinel);
  } catch (_) {}
  let roving = null;

  function renderGrid(append = false) {
    const list = S.list;
    const shown = list.slice(0, S.limit);
    const els = shown.map((it) => (cards.get(it.key) || makeCard(it)).el);
    if (!append) { stopPreview(); put(grid, ...els); }
    else for (const el of els) if (el.parentNode !== grid) grid.insertBefore(el, sentinel.parentNode === grid ? sentinel : null);
    if (!list.length) {
      put(grid, h("div", { class: "lib-empty" }, !S.items.length ? (S.note || "Loading the library…")
        : S.favOnly && !S.items.some((i) => i.kind === S.kind && S.favs.has(i.key)) ? "No favourites here yet: ★ a card to keep it handy."
        : "Nothing matches. ", S.items.length ? h("button", { onclick: () => { S.q = ""; S.qt = []; search.value = ""; S.filters[S.kind] = {}; S.favOnly = false; favBtn.setAttribute("aria-pressed", "false"); saveUi(); renderAll(); } }, "Clear search and filters") : null));
    }
    if (S.limit < list.length) {
      put(sentinel, h("button", { onclick: () => { S.limit += BATCH; renderGrid(true); } }, `Show more (${list.length - S.limit} left)`));
      grid.appendChild(sentinel);
    } else sentinel.remove();
    for (const c of cards.values()) if (c.el.isConnected) updateCard(c);
    // keyboard: exactly one card is in the tab order
    const keys = shown.map((i) => i.key);
    if (!keys.includes(roving)) roving = keys[0] || null;
    for (const k of keys) cards.get(k).el.tabIndex = k === roving ? 0 : -1;
    if (S.detail && keys.includes(S.detail)) placeDetail(); else if (S.detail && !append) closeDetail(false);
    const rej = S.items.filter((i) => i.kind === S.kind && i.status === "rejected").length;
    status.textContent = S.items.length
      ? `${Math.min(S.limit, list.length)} of ${list.length} shown${rej && hideRejected(S.kind) ? ` · ${rej} rejected hidden` : ""} · keys: / search, arrows move, Enter ${ACTION[S.kind]}, i details, f favourite, p preview`
      : "";
  }

  function renderSource() {
    const n = S.items.length;
    const cnt = (k) => S.items.filter((i) => i.kind === k).length;
    const broken = S.items.filter((i) => i.load === "error").length, fresh = S.items.filter((i) => i._added && Date.now() - i._added < NEW_MS).length;
    srcNote.textContent = S.source === "library" ? `${cnt("scene")} scenes · ${cnt("transition")} transitions · ${cnt("effect")} effects${broken ? ` · ${broken} broken` : ""}${fresh ? ` · ${fresh} new` : ""}`
      : S.source === "mock" ? `${n} items · MOCK (library-mock.json${multiply > 1 ? " ×" + multiply : ""})`
      : S.source === "catalog" ? `${n} items · catalog only` : "";
    srcNote.title = S.note || "";
  }

  function renderAll() {
    S.list = sorted(S.items.filter((it) => matches(it, S.kind)));
    renderSource(); renderTabs(); renderActive(); renderFilters(); renderNext(); renderBanner(); renderGrid();
    noteEl.hidden = !(S.note && S.source !== "library" && S.source !== "mock" && S.items.length);
    noteEl.textContent = S.note || "";
  }

  // ---- details unroll (spans the grid row under the card)
  function toggleDetail(key) { if (S.detail === key) closeDetail(); else openDetail(key); }
  function closeDetail(render = true) {
    const was = S.detail;
    S.detail = null;
    detailEl.querySelectorAll("video").forEach((v) => { try { v.pause(); v.removeAttribute("src"); v.load(); } catch (_) {} });
    detailEl.remove();
    if (render && was) { const c = cards.get(was); if (c) { updateCard(c); c.el.focus({ preventScroll: true }); } }
  }
  function placeDetail() {
    const c = cards.get(S.detail);
    if (!c || !c.el.isConnected) return;
    detailEl.remove();
    let last = c.el, n = c.el.nextElementSibling;
    while (n && n.classList.contains("lib-card") && n.offsetTop === c.el.offsetTop) { last = n; n = n.nextElementSibling; }
    last.after(detailEl);
  }
  function openDetail(key) {
    const it = S.byKey.get(key);
    if (!it) return;
    const prev = S.detail;
    S.detail = key;
    if (prev) { const p = cards.get(prev); if (p) updateCard(p); }
    stopPreview();
    const media = h("div", { class: "media" });
    if (it.preview) media.appendChild(h("video", { src: it.preview, muted: true, loop: true, autoplay: true, playsinline: true, preload: "auto", poster: it.thumb || null }));
    else if (it.thumb) media.appendChild(h("img", { src: it.thumb, alt: "" }));
    else media.appendChild(h("div", { class: "lib-thumb", style: { position: "absolute", inset: 0 } }, h("span", { class: "lib-ph" }, "no preview yet")));
    const v = media.querySelector("video"); if (v) { v.muted = true; v.play?.().catch(() => {}); }
    const kv = [];
    const row = (k, v2) => { if (v2 != null && v2 !== "") kv.push(h("span", {}, k), h("span", {}, v2)); };
    row("plug-in", it.plugin); row("preset", it.preset || "— (plug-in defaults)");
    row("origin", it.origin); row("status", it.status + (it.auto ? " · in auto pools" : ""));
    row("licence", it.facets.licence?.[0]);
    if (it.kind === "effect") row("stage", it.stage);
    if (it.kind === "transition") row("duration", it.duration_s != null ? `${it.duration_s} s` : null);
    row("cost", it.bench_ms_4k != null ? `${fmt(it.bench_ms_4k)} ms / frame at 4K${it.heavy ? " (heavy)" : ""}` : null);
    if (it.slow) row("gpu", h("span", { style: { color: "var(--bad)" } }, slowText(it.slow)));
    row("energy", Array.isArray(it.energy) ? `${fmt(it.energy[0])} – ${fmt(it.energy[1])}` : null);
    row("added", it.added_at ? ago(it.added_at) : null);
    if (it.load === "error") row("load", h("span", { style: { color: "var(--bad)" } }, it.error || "error"));
    if (it.runtime_errors > 0) row("runtime", h("span", { style: { color: "var(--bad)" } }, `${it.runtime_errors} error(s)`));
    const tagrows = [];
    for (const [facet, vals] of Object.entries(it.facets)) {
      if (!vals?.length) continue;
      tagrows.push(h("span", {}, facet), h("div", { class: "chips" }, vals.map((x) => h("button", { title: `filter: ${facet} = ${x}`, onclick: () => { if (S.kind !== it.kind) setKind(it.kind); toggleFilter(it.kind, facet, x, true); } }, x))));
    }
    const pr = [];
    const P = it.params && typeof it.params === "object" ? it.params : null;
    if (P && Object.keys(P).length) pr.push(...Object.entries(P).map(([k, x]) => `${k}: ${typeof x === "object" ? JSON.stringify(x) : x}`));
    const B = it.bindings && typeof it.bindings === "object" ? it.bindings : null;
    if (B && Object.keys(B).length) pr.push(...Object.entries(B).map(([k, x]) => `${k} ← ${typeof x === "object" ? JSON.stringify(x) : x}`));
    const presetRows = (it.presets || []).length > 1 ? h("div", { class: "lib-presets" }, h("div", { class: "small" }, `${it.presets.length} presets`),
      h("div", { class: "chips" }, it.presets.map((p) => h("button", {
        title: `${ACTION[it.kind]} ${p.label}${p.energy ? ` (energy ${fmt(p.energy[0])}–${fmt(p.energy[1])})` : ""}`,
        onclick: () => primary({ ...it, spec: p.name, preset: p.name, key: `${it.key}#${p.name}`, label: p.label }),
      }, p.label)))) : null;
    const acts = [];
    const mainBtn = h("button", { onclick: () => primary(it) }, ACTION[it.kind]);
    updateAction(mainBtn, it);
    mainBtn.style.flex = "0 0 auto";
    acts.push(mainBtn);
    const opt = (id, val) => { const s = document.getElementById(id); return s && [...s.options].some((o) => o.value === val) ? s : null; };
    if (it.kind === "scene" && typeof it.spec === "string" && opt("dEntry", it.spec))
      acts.push(h("button", { onclick: () => { opt("dEntry", it.spec).value = it.spec; toast(`Live demo entry scene: ${it.label}`); } }, "Use as Start scene"));
    if (it.kind === "transition" && typeof it.spec === "string") {
      if (opt("dIn", it.spec)) acts.push(h("button", { onclick: () => { opt("dIn", it.spec).value = it.spec; toast(`Start (art → live) via ${it.label}`); } }, "Use for Start"));
      if (opt("dOut", it.spec)) acts.push(h("button", { onclick: () => { opt("dOut", it.spec).value = it.spec; toast(`Stop (live → art) via ${it.label}`); } }, "Use for Stop"));
    }
    if (it.kind === "effect") {
      const ok = ctx.has("effects");
      const on = S.nextFx.some((e) => e.key === it.key);
      acts.push(h("button", { disabled: !ok, title: ok ? "part of the next cued scene (it transitions with the scene)" : "needs a server with scene effects (api feature 'effects')", onclick: () => attachNext(it) },
        on ? "Detach from next cue" : "Attach to next cue"));
    }
    acts.push(h("button", { onclick: () => toggleFav(it.key) }, S.favs.has(it.key) ? "★ Unfavourite" : "☆ Favourite"));
    if (it.preview) acts.push(h("a", { href: it.preview, target: "_blank", rel: "noopener", class: "small", style: { alignSelf: "center", color: "var(--info)" } }, "open preview"));
    acts.push(h("button", { onclick: () => closeDetail(), "aria-label": "close details" }, "Close"));
    put(detailEl, media, h("div", {},
      h("h3", {}, it.label, " ", h("span", { class: "small", style: { fontWeight: 400 } }, it.kind === "effect" ? `${it.stage || ""} effect` : it.kind)),
      it.description ? h("p", {}, it.description) : null,
      h("div", { class: "kv" }, kv),
      tagrows.length ? h("div", { class: "tagrows" }, tagrows) : null,
      presetRows,
      pr.length ? h("div", { class: "params" }, pr.join("\n")) : null,
      h("div", { class: "acts" }, acts)));
    const c = cards.get(key);
    if (c) { updateCard(c); placeDetail(); detailEl.scrollIntoView({ block: "nearest", behavior: "smooth" }); }
  }

  // ---- actions
  function toggleFav(key) {
    S.favs.has(key) ? S.favs.delete(key) : S.favs.add(key);
    store.set(FAV_KEY, [...S.favs]);
    const c = cards.get(key); if (c) updateCard(c);
    if (S.favOnly) renderAll(); else renderTabs();
    if (S.detail === key) openDetail(key);
  }

  function composeTo(it) {
    if (!S.nextFx.length) return it.spec;
    const effects = S.nextFx.map((e) => e.spec);
    return typeof it.spec === "string" ? { preset: it.spec, effects } : { ...it.spec, effects };
  }

  async function cue(it) {
    const sid = ctx.sid(), st = ctx.state();
    const body = { to: composeTo(it), at: S.at || document.getElementById("cAt")?.value || "beat", source: "panel" };
    if (S.nextVia) body.via = S.nextVia.spec;
    const what = `${it.label}${S.nextVia ? " via " + S.nextVia.label : ""}${S.nextFx.length ? " + " + S.nextFx.map((e) => e.label).join(", ") : ""}`;
    if (!sid || !LIVE_STATES.has(st)) {
      if (ctx.mock) { toast(`mock: would cue ${what} (${body.at})`, "info"); S.current = it.preset || it.name; updateAllCards(); return; }
      toast("Start the live demo first: Cue needs a live session", "err"); return;
    }
    const r = await http("POST", `/api/sessions/${sid}/cue`, body);
    if (r.ok) {
      toast(`Cued ${what}`);
      ctx.decide(`library cue ${r.data?.cue_id ?? ""}: ${JSON.stringify(body.to)}${body.via ? " via " + JSON.stringify(body.via) : ""} at ${body.at} (est. pts ${fmt(r.data?.at_pts)})`, "manual");
    } else {
      toast(explain(r, `cue ${it.label}`), "err", 6000);
      ctx.log(`POST cue -> ${r.status} ${r.error}: ${r.detail}`, "err");
    }
  }

  function useNext(it) {
    S.nextVia = S.nextVia?.key === it.key ? null : it;
    const sel = document.getElementById("cVia");
    if (sel && typeof it.spec === "string" && [...sel.options].some((o) => o.value === it.spec)) sel.value = S.nextVia ? it.spec : "";
    saveUi(); renderNext(); updateAllCards();
    toast(S.nextVia ? `Next cue goes via ${it.label}` : "Next cue: the director's pick", "info", 1800);
  }

  function attachNext(it) {
    const i = S.nextFx.findIndex((e) => e.key === it.key);
    if (i >= 0) S.nextFx.splice(i, 1);
    else if (S.nextFx.length >= 4) { toast("A scene takes at most 4 effects", "err"); return; }
    else S.nextFx.push(it);
    renderNext(); updateAllCards();
    if (S.detail === it.key) openDetail(it.key);
  }

  async function primary(it) {
    if (it.load === "error") return;
    if (it.kind === "scene") return cue(it);
    if (it.kind === "transition") return useNext(it);
    if (!bus) return;
    if (bus.has(it.spec)) await bus.removeSpec(it.spec); else await bus.add(it);
    updateAllCards();
  }

  function setKind(k) {
    if (S.kind === k) return;
    S.kind = k; S.limit = BATCH; S.detail = null; detailEl.remove(); roving = null;
    saveUi(); renderAll(); scroller.scrollTop = 0;
  }

  // ---- lab footer
  function renderLab() {
    const L = S.lab;
    const sumKids = [h("span", {}, "Plug-ins")];
    if (S.labAvail === "missing") {
      sumKids.push(h("span", { class: "small" }, ctx.mock ? "no mock status" : "no plug-in status on this server"));
      labBadge.hidden = true;
      put(labBody, h("div", { class: "lib-warnbox" }, "The lab loader is not running here. ", h("code", {}, "GET /dev/lab/status"), " returns 404: start the server with ", h("code", {}, "--lab-dir ~/dev/sm-fx-lab"), " to load the plug-in lab."));
      put(labSum, ...sumKids); return;
    }
    if (!L) { sumKids.push(h("span", { class: "small" }, S.labErr || "–")); put(labSum, ...sumKids); return; }
    const plugins = L.plugins || [];
    const errs = plugins.filter((p) => p.load === "error" || p.load === "stale");
    const shadow = plugins.filter((p) => p.shadowed_by).length + 0;
    const rt = plugins.reduce((n, p) => n + (p.runtime_errors?.count || 0), 0);
    sumKids.push(h("span", { class: "lib-pill" }, `${plugins.length} plug-ins`));
    if (errs.length) sumKids.push(h("span", { class: "lib-pill bad" }, `${errs.length} import error${errs.length > 1 ? "s" : ""}`));
    if (rt) sumKids.push(h("span", { class: "lib-pill warn" }, `${rt} runtime error${rt > 1 ? "s" : ""}`));
    if (shadow || L.collisions?.length) sumKids.push(h("span", { class: "lib-pill" }, `${Math.max(shadow, L.collisions?.length || 0)} shadowed by core`));
    sumKids.push(h("span", { class: "small" }, `rev ${L.rev ?? "–"} · rescanned ${L.rescanned_at ? ago(L.rescanned_at) : "–"} (every ${L.rescan_s ?? 20} s)`));
    put(labSum, ...sumKids);
    labBadge.hidden = false;
    labBadge.className = `lib-pill ${errs.length ? "bad" : rt ? "warn" : "ok"}`;
    labBadge.textContent = errs.length ? `${errs.length} broken` : rt ? `${rt} runtime error${rt > 1 ? "s" : ""}` : "plug-ins ok";
    labBadge.title = "plug-in status (details at the bottom of the Library)";
    if (!lab.open) return;
    const byKind = {};
    for (const p of plugins) { const k = (byKind[p.kind] ||= { ok: 0, error: 0, stale: 0, shadowed: 0 }); if (p.shadowed_by) k.shadowed++; else k[p.load] = (k[p.load] || 0) + 1; }
    const rescan = h("button", { class: "primary", onclick: () => doRescan(rescan) }, "Rescan");
    put(labBody, 
      h("div", { class: "row", style: { justifyContent: "space-between" } },
        h("div", { class: "small", style: { flex: "1 1 auto" } }, (L.roots || []).map((r) => h("div", {}, h("code", {}, r.path), ` · ${r.origin} · ${(r.packages || []).join(", ")}${r.errors?.length ? " · " + r.errors.join("; ") : ""}`))),
        h("div", { style: { flex: "0 0 auto" } }, rescan, " ", h("button", { onclick: () => { loadIndex("reload"); loadLab(); } }, "Reload index"))),
      h("table", {}, h("thead", {}, h("tr", {}, ["kind", "ok", "errors", "stale", "shadowed"].map((x) => h("th", {}, x)))),
        h("tbody", {}, Object.entries(byKind).map(([k, v]) => h("tr", {}, [k, v.ok, v.error, v.stale, v.shadowed].map((x) => h("td", {}, String(x ?? 0))))))),
      errs.length ? h("div", { style: { marginTop: "8px" } }, h("b", {}, "Import errors"), h("ul", {}, errs.map((p) => h("li", {}, h("code", {}, p.module || p.name), ": ", h("span", { class: "err" }, (p.error || p.load).split("\n")[0]))))) : null,
      plugins.some((p) => p.runtime_errors?.count) ? h("div", { style: { marginTop: "8px" } }, h("b", {}, "Runtime errors"), h("ul", {}, plugins.filter((p) => p.runtime_errors?.count).map((p) => h("li", {}, `${p.kind} ${p.name} ×${p.runtime_errors.count}: `, h("code", {}, p.runtime_errors.last_detail || "?"))))) : null,
      L.collisions?.length ? h("details", { style: { marginTop: "6px" } }, h("summary", { class: "small" }, `${L.collisions.length} names shadowed by core`), h("div", { class: "small" }, L.collisions.map((c) => `${c.kind} ${c.name} (kept ${c.kept})`).join(" · "))) : null,
      L.preset_problems?.length ? h("details", { style: { marginTop: "6px" } }, h("summary", { class: "small" }, `${L.preset_problems.length} preset problems`), h("ul", { class: "small" }, L.preset_problems.map((p) => h("li", {}, p)))) : null);
  }
  lab.addEventListener("toggle", () => { if (lab.open) { renderLab(); loadLab(); } });

  async function doRescan(btn) {
    if (ctx.mock) { toast("mock: rescan is a no-op", "info"); return; }
    btn.disabled = true; btn.textContent = "Rescanning…";
    const r = await http("POST", "/dev/lab/rescan", {});
    btn.disabled = false; btn.textContent = "Rescan";
    if (!r.ok) { toast(explain(r, "rescan"), "err", 6000); return; }
    const d = r.data || {};
    toast(`Rescanned in ${d.took_ms ?? "?"} ms: ${d.reloaded?.length || 0} reloaded, ${d.added?.length || 0} added, ${d.removed?.length || 0} removed${d.errors?.length ? `, ${d.errors.length} error(s)` : ""}`, d.errors?.length ? "err" : "", 6000);
    await Promise.all([loadIndex("rescan"), loadLab()]);
  }

  // ---- toolbar wiring
  const searchSoon = debounce(() => { S.q = search.value; S.qt = S.q.toLowerCase().split(/\s+/).filter(Boolean); S.limit = BATCH; renderAll(); }, 120);
  search.addEventListener("input", searchSoon);
  search.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { search.value = ""; searchSoon(); }
    if (e.key === "ArrowDown" || e.key === "Enter") { const first = grid.querySelector(".lib-card"); if (first) { e.preventDefault(); first.focus(); } }
  });
  filtBtn.addEventListener("click", () => { S.filtersOpen = !S.filtersOpen; renderFilters(); });
  sortSel.addEventListener("change", () => { S.sort = sortSel.value; S.limit = BATCH; saveUi(); renderAll(); });
  favBtn.addEventListener("click", () => { S.favOnly = !S.favOnly; favBtn.setAttribute("aria-pressed", String(S.favOnly)); S.limit = BATCH; saveUi(); renderAll(); });

  // keyboard: roving focus in the grid
  grid.addEventListener("keydown", (e) => {
    const card = e.target.closest?.(".lib-card");
    if (!card || e.target !== card) return;
    const c = cards.get(card.dataset.key);
    if (!c) return;
    const els = [...grid.querySelectorAll(".lib-card")];
    const i = els.indexOf(card);
    const cols = Math.max(1, getComputedStyle(grid).gridTemplateColumns.split(" ").length);
    const go = (j) => { const t = els[Math.max(0, Math.min(els.length - 1, j))]; if (t && t !== card) { card.tabIndex = -1; t.tabIndex = 0; roving = t.dataset.key; t.focus(); t.scrollIntoView({ block: "nearest" }); } };
    const k = e.key;
    if (k === "ArrowRight") go(i + 1);
    else if (k === "ArrowLeft") go(i - 1);
    else if (k === "ArrowDown") go(i + cols);
    else if (k === "ArrowUp") { if (i < cols) search.focus(); else go(i - cols); }
    else if (k === "Home") go(0);
    else if (k === "End") go(els.length - 1);
    else if (k === "Enter") primary(c.it);
    else if (k === "f" || k === "F") toggleFav(c.it.key);
    else if (k === "i" || k === "I" || k === ".") toggleDetail(c.it.key);
    else if (k === "p" || k === "P" || k === " ") { lpKey === c.it.key ? liveStop() : livePreview(c.it); }
    else if (k === "Escape") { if (S.detail) closeDetail(); else if (lpKey) liveStop(); else stopPreview(); }
    else return;
    e.preventDefault();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey) return;
    const t = e.target;
    if (t && (t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName))) return;
    e.preventDefault(); search.focus(); search.select();
  });
  let resizeT = null;
  window.addEventListener("resize", () => { clearTimeout(resizeT); resizeT = setTimeout(() => { if (S.detail) placeDetail(); }, 150); });

  // ---- events and session changes
  ctx.onEvent((ev) => {
    if (ev.type === "catalog.changed") { reloadSoon(ev.reason || "catalog.changed"); loadLab(); return; }
    if (ev.session !== ctx.sid()) return;
    if (ev.type === "session.plugin_error") {
      const prev = S.errors.find((x) => x.kind === ev.kind && x.name === ev.name && x.slot === ev.slot && x.session === ev.session);
      if (prev) Object.assign(prev, ev); else S.errors.push({ ...ev });
      renderBanner();
      toast(`${ev.kind} ${ev.name} failed (${ev.action}): ${ev.detail || ""}`, "err", 6000);
    } else if (ev.type === "session.transition" && ev.phase === "started") {
      S.current = ev.kind === "out" ? null : ev.scene || S.current;
      updateAllCards();
    } else if (ev.type === "session.state") {
      if (["ended", "failed", "held"].includes(ev.state)) { S.current = null; }
      renderNext(); updateAllCards();
    }
  });
  const reloadSoon = debounce((why) => loadIndex(why), 500);
  bus?.onChange(() => { for (const c of cards.values()) if (c.el.isConnected && c.it.kind === "effect") updateAction(c.btn, c.it); });

  let seen = `${ctx.sid()}|${ctx.state()}|${!!ctx.catalog()}`;
  setInterval(() => {
    const now = `${ctx.sid()}|${ctx.state()}|${!!ctx.catalog()}`;
    if (now !== seen) {
      const [prevSid] = seen.split("|");
      const hadCatalog = seen.endsWith("true");
      seen = now;
      if (ctx.sid() !== prevSid) { S.errors = []; S.errorsDismissed = 0; S.current = null; renderBanner(); }
      if (!hadCatalog && ctx.catalog() && S.source !== "library" && S.source !== "mock") loadIndex();
      renderNext(); updateAllCards();
    }
  }, 700);
  // the server rescans the plug-in folders every 20 s: refresh the index (and the status footer when open)
  setInterval(() => { if (!ctx.mock && !document.hidden) { loadIndex(); if (lab.open) loadLab(); } }, REFRESH_MS);

  renderAll();
  loadIndex().finally(loadLab);           // mock: the lab status lives in the fixture
  return { reload: () => loadIndex("manual"), state: S };
}
