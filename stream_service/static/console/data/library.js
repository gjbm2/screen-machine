// The Library index: GET /dev/library (normalised), the plug-in status (GET /dev/lab/status: its rev is checked every
// 60 s while visible and the index refetched only when it moved), catalog.changed (2 s debounce), and the slow /
// broken marks from the renderer's plug-in errors.
import { http, withBase, debounce } from "../kit.js";
import { S } from "../store.js";
import { normalise, presetIndex } from "./query.js";
import { onEvent } from "./renderer.js";

export const lib = { items: [], byKey: new Map(), pidx: new Map(), rev: null, loaded: false, note: null, lab: null, labAt: 0, labErr: null };
export function bump() { S.set({ libVersion: S.get().libVersion + 1 }); }
/** A plug-in's Library label ("Circuits"), or null: for words about a plug-in named by its id ("st_circuits"). */
export function pluginTitle(name) {
  if (!name) return null;
  const it = lib.items.find((x) => x.plugin === name) || lib.items.find((x) => x.name === name);
  return it ? it.title : null;
}

let loading = null;
export async function load(reason = "") {
  if (loading) return loading;
  loading = (async () => {
    const r = await http("GET", "/dev/library", undefined, { timeoutMs: 45000 });
    let idx = null;
    if (r.ok && Array.isArray(r.data?.items)) idx = r.data;
    else {
      const c = await http("GET", "/api/catalog", undefined, { timeoutMs: 60000 });
      if (c.ok && c.data?.presets) { idx = indexFromCatalog(c.data); lib.note = "No /dev/library here: the catalog, without pictures"; }
      else lib.note = r.status === 0 ? "Can't reach the renderer" : "The library couldn't be loaded";
    }
    if (idx) {
      const slow = new Map(lib.items.filter((i) => i.slow).map((i) => [i.key, i.slow]));
      const items = normalise(idx, { withBase });
      for (const it of items) if (!it.slow && slow.has(it.key)) it.slow = slow.get(it.key);
      lib.items = items;
      lib.byKey = new Map(items.map((i) => [i.key, i]));
      lib.pidx = presetIndex(items);
      lib.loaded = true;
      lib.reason = reason;
      const { applyAll } = await import("./ratings.js");
      applyAll();
      bump();
    }
    return !!idx;
  })();
  try { return await loading; } finally { loading = null; }
}

export async function loadLab() {
  const r = await http("GET", "/dev/lab/status");
  if (r.ok && r.data) { lib.lab = r.data; lib.labAt = Date.now(); lib.labErr = null; }
  else lib.labErr = r;
  S.set({ labVersion: (S.get().labVersion || 0) + 1 });
  return r;
}

export function startRefresh() {
  const reloadSoon = debounce(async () => { await load("catalog.changed"); loadLab(); }, 2000);
  onEvent("catalog.changed", () => reloadSoon());
  setInterval(async () => {
    if (document.visibilityState !== "visible") return;
    const prev = lib.lab?.rev;
    await loadLab();
    if (lib.lab && prev != null && lib.lab.rev !== prev) load("rev");
  }, 60000);
  // a plug-in the GPU guard tripped (or that failed to prepare): marked at once on its tile
  onEvent("session.plugin_error", (ev) => {
    const kind = ev.kind === "source" ? "scene" : ev.kind;
    const it = lib.byKey.get(`${kind}/${ev.name}`);
    if (!it) return;
    const slow = ev.reason === "slow" || /GpuTooSlow/.test(String(ev.detail || ""));
    if (slow) it.slow = { ms: ev.gpu_ms ?? Number(String(ev.detail || "").match(/([\d.]+)\s*ms/)?.[1]), budget_ms: ev.budget_ms ?? 33.3, source: "guard" };
    else if (ev.phase === "prepare" || ev.action === "rejected") it._broken = String(ev.detail || "failed to start");
    it.runtime_errors = (it.runtime_errors || 0) + 1;
    it.runtime_error = ev.detail;
    bump();
  });
}

// /api/catalog as an index (no pictures): only when the server has no /dev/library.
export function indexFromCatalog(c) {
  const items = [];
  const byPlugin = new Map();
  const add = (kind, plugin, o, presetName, preset) => {
    const key = `${kind}/${plugin}`;
    let it = byPlugin.get(key);
    if (!it) {
      it = { key, kind, name: plugin, plugin, label: o.label || plugin, description: o.description || null, preset: presetName,
             spec: presetName || { [kind === "scene" ? "source" : kind]: plugin }, presets: [], stage: o.stage || null,
             facets: { origin: [o.origin || "core"], family: [].concat(preset?.tags?.family || []), moods: preset?.tags?.moods || [],
                       dims: [].concat(preset?.tags?.dims || []), technique: preset?.tags?.technique || [] },
             thumb: null, preview: null, bench_ms_4k: null, load: "ok", runtime_errors: 0, added_at: null, status: "accepted",
             energy: preset?.tags?.energy || null, duration_s: preset?.params?.duration_s };
      byPlugin.set(key, it); items.push(it);
    }
    if (presetName) it.presets.push({ name: presetName, label: preset?.label || presetName, energy: preset?.tags?.energy });
  };
  const P = c.presets || {};
  const stageOf = (name) => (c.effects || []).find((e) => e.name === name)?.stage;
  for (const [n, p] of Object.entries(P.scenes || {})) add("scene", p.source, (c.sources || []).find((s) => s.name === p.source) || {}, n, p);
  for (const [n, p] of Object.entries(P.transitions || {})) add("transition", p.transition, (c.transitions || []).find((s) => s.name === p.transition) || {}, n, p);
  for (const [n, p] of Object.entries(P.effects || {})) add("effect", p.effect, { ...((c.effects || []).find((s) => s.name === p.effect) || {}), stage: p.stage || stageOf(p.effect) }, n, p);
  return { items };
}
