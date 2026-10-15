// The Picker: the Browse grid in pick mode (Change scene, + Effect, Start with).  Fixed kind, the same search and
// look chips; a tap picks.  Change scene's footer sets the transition and the timing (the Next-change state).
import { h, put, debounce } from "../kit.js";
import { S } from "../store.js";
import { lib } from "../data/library.js";
import * as room from "../data/room.js";
import { matches, sorted, tokens, facetValues, facetWord, itemForSpec, variantTitle } from "../data/query.js";
import { createGrid } from "../browse/grid.js";
import { openSheet } from "../ui/sheet.js";
import { chip, select, switchEl, button } from "../ui/controls.js";
import { icon } from "../ui/icons.js";
import { setVia, setAt } from "./compose.js";
import { desiredScene } from "../words.js";
import { KIND_WORDS } from "../browse/filters.js";

export function transitionOptions() {
  const out = [["", "The room's choice"], ["cut", "Cut"]];
  const items = lib.items.filter((i) => i.kind === "transition").sort((a, b) => a.title.localeCompare(b.title));
  for (const it of items) for (const p of it.presets || []) if (p.name !== "cut") out.push([p.name, it.presets.length > 1 ? variantTitle(it.title, p.label) : it.title]);
  return out;
}
export function viaFromValue(v) {
  if (!v) return null;
  if (v === "cut") return { key: "transition/cut", spec: "cut", label: "Cut" };
  const hit = lib.pidx.get(`transition:${v}`);
  return { key: hit?.item.key || `transition/${v}`, spec: v, label: hit ? (hit.item.presets.length > 1 ? variantTitle(hit.item.title, hit.preset.label) : hit.item.title) : v };
}

/** openPicker(kind, {title, onPick(item, presetName), footer: "change" | null, allowSurprise}) */
export function openPicker(kind, { title, onPick, footer = null, surprise = null } = {}) {
  let q = "", look = null;
  const search = h("input", { type: "search", placeholder: `Search ${KIND_WORDS[kind][1]}…`, "aria-label": "Search", autocomplete: "off" });
  const sbox = h("div", { class: "search" }, icon("search"), search);
  const chips = h("div", { class: "chips-x quick" });
  const t = S.get().target;
  const onNow = itemForSpec(desiredScene(room.screenOf(t)?.desired), lib.items, lib.pidx);
  const ribbons = () => (kind === "scene" && onNow ? new Map([[onNow.key, ["now"]]]) : new Map());
  let sheet = null;
  const grid = createGrid({ mode: "pick", tid: "picker-grid", ribbons, selected: () => null,
    onSelect: (it) => { sheet?.close(); onPick?.(it, typeof it.spec === "string" ? it.spec : it.preset); } });
  const body = h("div", { class: "stack" }, h("div", { class: "row" }, sbox, surprise ? button("Surprise me", { kind: "outline", icon: "shuffle", onClick: () => { sheet?.close(); surprise(); } }) : null),
    chips, grid.el, grid.more);
  let foot = null;
  if (footer === "change") {
    const c = S.get().compose;
    const tr = select(transitionOptions(), c.via?.spec ?? "", (v) => setVia(viaFromValue(v)), { tid: "picker-transition", aria: "Transition" });
    const beat = switchEl("On the beat", c.at !== "now", (on) => setAt(on ? "beat" : "now"), { tid: "picker-beat" });
    foot = h("div", { class: "row", style: { gap: "12px" } }, h("label", { class: "field grow", style: { minWidth: "200px" } }, h("span", { class: "lab" }, "Transition"), tr), beat);
  }
  sheet = openSheet({ id: "picker", title: title || `Pick ${KIND_WORDS[kind][0] === "effect" ? "an effect" : `a ${KIND_WORDS[kind][0]}`}`, body, foot, tid: "picker-sheet", wide: true, full: true });
  // One tag filter at a time, from any facet (sets, your tags, look, technique, style): searching "piano" offers the
  // tag piano with how many scenes carry it; tapping it filters by the tag and clears the words.
  const draw = () => {
    const s = { q, qt: tokens(q), stars: { n: null }, rejects: "hide", facets: { [kind]: look ? { [look.facet]: [look.value] } : {} } };
    const list = sorted(lib.items.filter((it) => matches(it, s, kind)), "best");
    grid.setList(list);
    const base = { ...s, q: "", qt: [], facets: { [kind]: {} } };
    const words = String(q || "").toLowerCase().split(/\s+/).filter((w) => w.length >= 2);
    const cands = [];
    for (const facet of words.length ? ["modes", "mytags", "look", "technique", "style"] : ["modes", "look"]) {
      for (const [v, n] of facetValues(lib.items, base, kind, facet)) {
        if (n && (!words.length || words.some((w) => String(v).toLowerCase().includes(w)))) cands.push({ facet, value: v, n });
      }
    }
    const on = look ? [{ ...look, n: null }] : [];
    const shown = [...on, ...cands.filter((c) => !look || c.facet !== look.facet || c.value !== look.value)].slice(0, 10);
    put(chips, shown.map((c) => chip(facetWord(c.facet, c.value), {
      count: c.n, pressed: !!look && look.facet === c.facet && look.value === c.value, tid: "picker-tag", data: { facet: c.facet, value: c.value },
      onClick: () => {
        const same = look && look.facet === c.facet && look.value === c.value;
        look = same ? null : { facet: c.facet, value: c.value };
        if (!same && words.length) { q = ""; search.value = ""; }
        draw();
      } })));
  };
  search.addEventListener("input", debounce(() => { q = search.value; draw(); }, 120));
  draw();
  requestAnimationFrame(() => search.focus({ preventScroll: true }));
  return sheet;
}
