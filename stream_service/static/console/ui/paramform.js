// Schema -> parameter rows (Tune, Scene settings, the Reference form).  Rows are grouped Main (x-ui.basic first) /
// More settings / Engine settings (structural, quality, seed: changing them restarts the picture).  Each row carries
// data-param=<name>.  A note icon marks a parameter that follows the music, ↻ one that restarts the plug-in.
import { h, put, fmt } from "../kit.js";
import { slider, switchEl, unroll, paintRange } from "./controls.js";
import { icon } from "./icons.js";

export const isColor = (p) => p?.type === "string" && (p.format === "color" || p["x-ui"]?.widget === "color");
export const restarts = (p) => p?.["x-tier"] === "structural";
export const isEngine = (k, p) => p?.["x-tier"] === "structural" || p?.["x-tier"] === "quality" || /(^|_)seed$/.test(k) || k === "render_scale";
const bindObj = (v) => v && typeof v === "object" && ("bind" in v || "signal" in v);
export const plainValue = (v, p) => (bindObj(v) ? (v.rest ?? p?.default) : v);

export function groupParams(props) {
  const names = Object.keys(props || {});
  const engine = names.filter((k) => isEngine(k, props[k]));
  const rest = names.filter((k) => !engine.includes(k));
  let main = rest.filter((k) => props[k]?.["x-ui"]?.basic === true);
  if (main.length < 4) for (const k of rest) { if (main.length >= 6) break; if (!main.includes(k)) main.push(k); }
  const more = rest.filter((k) => !main.includes(k));
  return { main, more, engine };
}

function numFmt(p, lo, hi) {
  return (n) => p.type === "integer" ? (Math.abs(n) >= 1e5 ? `${(n / 1e6).toFixed(n >= 1e7 ? 0 : 1)}M` : String(Math.round(n)))
    : fmt(n, Math.abs(hi - lo) >= 50 ? 0 : 2) + (p["x-ui"]?.unit ? ` ${p["x-ui"].unit}` : "");
}

/** One row.  onChange(name, value).  Returns the row element with .set(value) */
export function paramRow(name, p, value, onChange, { bound = false, onReset = null } = {}) {
  const ui = p["x-ui"] || {};
  const label = ui.label || name;
  const glyph = () => h("span", { class: "gl" }, bound ? icon("music", { cls: "tiny" }) : null, restarts(p) ? " ↻" : null);
  const hasGlyph = bound || restarts(p);
  const title = `${name}${bound ? " · follows the music (moving it takes over)" : ""}${restarts(p) ? " · changing it restarts the picture" : ""}`;
  const v0 = plainValue(value, p) ?? p.default;
  let row;
  const reset = onReset ? h("button", { type: "button", class: "reset", "aria-label": `Reset ${label}`, title: "Back to the preset's value" }, icon("undo")) : null;
  if (p.type === "boolean") {
    const sw = switchEl(null, !!v0, (on) => onChange(name, on), { aria: label });
    row = h("div", { class: "prow chk" }, h("span", { class: "pname", title }, label, hasGlyph ? glyph() : null), h("span", { class: "row" }, sw, reset));
    row.set = (v) => sw.set(!!plainValue(v, p));
  } else if (Array.isArray(p.enum)) {
    const s = h("select", { class: "select sm", "aria-label": label }, p.enum.map((x, i) => h("option", { value: String(x) }, ui.labels?.[i] ?? String(x))));
    s.value = String(v0 ?? p.default);
    s.addEventListener("change", () => onChange(name, p.type === "string" ? s.value : Number(s.value)));
    row = h("div", { class: "prow sel" }, h("span", { class: "pname", title }, label, hasGlyph ? glyph() : null), s, reset);
    row.set = (v) => { if (document.activeElement !== s) s.value = String(plainValue(v, p)); };
  } else if (isColor(p)) {
    const c = h("input", { type: "color", class: "color", "aria-label": label });
    c.value = /^#[0-9a-f]{6}$/i.test(String(v0)) ? v0 : (p.default || "#ffffff");
    c.addEventListener("input", () => onChange(name, c.value));
    row = h("div", { class: "prow col" }, h("span", { class: "pname", title }, label, hasGlyph ? glyph() : null), h("span", { class: "row" }, c, reset));
    row.set = (v) => { if (document.activeElement !== c && /^#/.test(String(plainValue(v, p)))) c.value = plainValue(v, p); };
  } else if (p.type === "number" || p.type === "integer") {
    const lo = p.minimum ?? 0, hi = p.maximum ?? Math.max(1, (Number(v0) || 0) * 2, Number(p.default) * 2 || 1);
    const step = p.type === "integer" ? 1 : (ui.step || (hi - lo) / 200);
    const sl = slider(label, { min: lo, max: hi, step, value: Number(v0 ?? lo), glyph: hasGlyph ? glyph() : "", title, fmt: numFmt(p, lo, hi),
                               onInput: (v) => onChange(name, p.type === "integer" ? Math.round(v) : v), extra: reset });
    row = h("div", { class: "prow num" }, sl);
    row.set = (v) => sl.set(Number(plainValue(v, p)));
    row.input = sl.input;
  } else if (p.type === "string") {
    const t = h("input", { class: "input sm", "aria-label": label });
    t.value = v0 ?? "";
    t.addEventListener("change", () => onChange(name, t.value));
    row = h("div", { class: "prow sel" }, h("span", { class: "pname", title }, label), t);
    row.set = (v) => { if (document.activeElement !== t) t.value = plainValue(v, p) ?? ""; };
  } else return null;
  row.dataset.param = name;
  if (reset) reset.addEventListener("click", (e) => { e.preventDefault(); onReset(name); });
  return row;
}

/**
 * paramForm(container, props, values, onChange, {bound, onReset, grouped, engineNote})
 * -> {rows: Map, set(values)}.  Grouped: Main rows, then "More settings" and "Engine settings" unrolls.
 */
export function paramForm(container, props, values, onChange, { bound = new Set(), onReset = null, grouped = true, keepOpen = {} } = {}) {
  const rows = new Map();
  const mk = (k) => {
    const r = paramRow(k, props[k], values?.[k], onChange, { bound: bound.has(k), onReset });
    if (r) rows.set(k, r);
    return r;
  };
  if (!grouped) {
    put(container, Object.keys(props || {}).map(mk));
  } else {
    const g = groupParams(props);
    const more = g.more.length ? unroll("More settings", { open: !!keepOpen.more, onToggle: (o) => { keepOpen.more = o; } }) : null;
    const eng = g.engine.length ? unroll("Engine settings (restart the picture)", { open: !!keepOpen.engine, onToggle: (o) => { keepOpen.engine = o; } }) : null;
    if (more) put(more.body, g.more.map(mk));
    if (eng) put(eng.body, g.engine.map(mk));
    put(container, h("div", { class: "grp main" }, g.main.map(mk)), more, eng,
        !Object.keys(props || {}).length ? h("p", { class: "hint" }, "No settings for this one.") : null);
  }
  return {
    rows,
    set(vals) { for (const [k, r] of rows) if (vals && k in vals) r.set(vals[k]); },
  };
}

export { paintRange };
