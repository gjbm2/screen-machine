// The renderer's full catalog (/api/catalog, ~2.4 MB): loaded lazily, once, by the first Tune, On screen, Under the
// hood, Picker or ?attach; again after catalog.changed.
import { http } from "../kit.js";
import { onEvent } from "./renderer.js";

let promise = null, cat = null;
export function ready() {
  if (!promise) {
    promise = (async () => {
      for (let i = 0; i < 3; i++) {
        const r = await http("GET", "/api/catalog", undefined, { timeoutMs: 60000 });
        if (r.ok && r.data?.presets) { cat = r.data; return cat; }
        await new Promise((res) => setTimeout(res, 1500 * (i + 1)));
      }
      promise = null;
      return null;
    })();
  }
  return promise;
}
export const get = () => cat;
onEvent("catalog.changed", () => { if (cat) { promise = null; ready(); } });

const listFor = (c, kind) => (kind === "scene" || kind === "source" ? c?.sources : kind === "transition" ? c?.transitions : c?.effects) || [];
/** The schema properties of a plug-in. */
export function propsFor(kind, plugin, c = cat) {
  return listFor(c, kind).find((x) => x.name === plugin)?.schema?.properties || {};
}
/** A scene spec -> {plugin, params} (preset values merged over defaults' absence). */
export function scenePreset(name, c = cat) { return c?.presets?.scenes?.[name] || null; }
export function transitionPresets(c = cat) { return Object.entries(c?.presets?.transitions || {}); }
export function features(c = cat) { return new Set(c?.api?.features || []); }
