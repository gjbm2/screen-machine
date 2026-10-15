// A small observable store: get(), set(patch), on(keys, fn).  Notifications are batched (one per microtask) and
// carry the set of keys that changed.  The app's store persists the per-device keys of design §6.9.
import { store as ls } from "./kit.js";

export function createStore(initial = {}) {
  let state = { ...initial };
  const subs = new Set();
  let dirty = new Set(), queued = false;
  const flush = () => {
    queued = false;
    const changed = dirty; dirty = new Set();
    for (const s of [...subs]) {
      if (!s.keys || s.keys.some((k) => changed.has(k))) { try { s.fn(state, changed); } catch (e) { console.error(e); } }
    }
  };
  return {
    get: () => state,
    set(patch) {
      let any = false;
      for (const [k, v] of Object.entries(patch || {})) {
        if (state[k] !== v) { any = true; dirty.add(k); }
      }
      if (!any) return;
      state = { ...state, ...patch };
      if (!queued) { queued = true; Promise.resolve().then(flush); }
    },
    on(keys, fn) {
      const s = { keys: keys ? (Array.isArray(keys) ? keys : [keys]) : null, fn };
      subs.add(s);
      return () => subs.delete(s);
    },
  };
}

// ---------------------------------------------------------------- the app's state
const KINDS = ["scene", "transition", "effect"];
const SORTS = ["best", "rating", "az", "energy", "newest", "fastest"];
const UI_KEY = "sm.console.ui";

function loadUi() {
  let ui = ls.get(UI_KEY, null);
  if (!ui) {                                   // migrated once from the old Library's per-browser settings
    const old = ls.get("sm.library.ui", null);
    if (old && typeof old === "object") {
      const sortMap = { recommended: "best", rating: "rating", name: "az", energy: "energy", newest: "newest" };
      const rf = old.rf || {};
      ui = {
        kind: old.kind, sort: sortMap[old.sort] || "best",
        stars: rf.n ? { n: rf.n, op: rf.op || "ge" } : { n: null, op: "ge" },
        rejects: rf.flag === "only" ? "only" : rf.flag === "all" ? "show" : "hide",
        facets: {},
      };
    }
  }
  ui = ui && typeof ui === "object" ? ui : {};
  const facets = {};
  for (const k of KINDS) {
    facets[k] = {};
    for (const [f, vals] of Object.entries(ui.facets?.[k] || {})) if (Array.isArray(vals) && vals.length) facets[k][f] = [...vals];
  }
  const st = ui.stars && typeof ui.stars === "object" ? ui.stars : {};
  return {
    kind: KINDS.includes(ui.kind) ? ui.kind : "scene",
    sort: SORTS.includes(ui.sort) ? ui.sort : "best",
    stars: { n: Number.isInteger(st.n) && st.n >= 0 && st.n <= 5 ? st.n : null, op: ["ge", "eq", "le"].includes(st.op) ? st.op : "ge" },
    rejects: ["hide", "show", "only"].includes(ui.rejects) ? ui.rejects : "hide",
    facets,
    large: !!ui.large,
    onlyUnrated: !!ui.onlyUnrated,
    advance: !!ui.advance,
  };
}

const ui0 = loadUi();
const compose0 = ls.get("sm.console.compose", {}) || {};

export const S = createStore({
  ...ui0,
  q: "",
  mode: "browse",
  target: ls.get("sm.console.screen", "devtest") || "devtest",
  sel: null,                       // the selected item key (inspector / Viewer)
  viewer: false,                   // the phone / tablet Viewer sheet is open
  review: false,
  compose: { via: compose0.via || null, at: compose0.at === "now" ? "now" : "beat", fx: [] },
  golive: ls.get("sm.console.golive", {}) || {},
  dur: [120, 600, 1800].includes(ls.get("sm.console.dur", 120)) ? ls.get("sm.console.dur", 120) : 120,
  libVersion: 0,                   // bumps when the index or ratings change
  roomStatus: null,
  roomLink: "connecting",          // up | down | missing | connecting
  rendererLink: "connecting",      // up | down | connecting
  preview: { sid: null, state: "idle" },
  lpTuned: null,
});

const saveUi = () => {
  const s = S.get();
  ls.set(UI_KEY, { kind: s.kind, sort: s.sort, stars: s.stars, rejects: s.rejects, facets: s.facets, large: s.large,
                   onlyUnrated: s.onlyUnrated, advance: s.advance });
};
S.on(["kind", "sort", "stars", "rejects", "facets", "large", "onlyUnrated", "advance"], saveUi);
S.on(["target"], (s) => ls.set("sm.console.screen", s.target));
S.on(["compose"], (s) => ls.set("sm.console.compose", { via: s.compose.via, at: s.compose.at }));
S.on(["dur"], (s) => ls.set("sm.console.dur", s.dur));
if (!ls.has(UI_KEY)) saveUi();
