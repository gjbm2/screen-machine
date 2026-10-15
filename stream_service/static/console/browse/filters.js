// The Filter sheet (design §3.2): stars (one chip group + at least / exactly / at most), rejected (hide / show / only),
// Look (family ∪ moods), Movement, Depth, Stage, Style; Technique, Source, Licence, My tags, Saved looks, Status under
// "More filters"; order and picture size.  Changes apply live; the footer says how many will show.
import { h, put } from "../kit.js";
import { S } from "../store.js";
import { lib } from "../data/library.js";
import { FACETS, FACET_DEFS, facetValues, facetWord, counts, sortOptions } from "../data/query.js";
import { openSheet } from "../ui/sheet.js";
import { chip, seg, select, switchEl, unroll, button } from "../ui/controls.js";
import { icon } from "../ui/icons.js";

export const KIND_WORDS = { scene: ["scene", "scenes", "Scenes"], transition: ["transition", "transitions", "Transitions"], effect: ["effect", "effects", "Effects"] };
export const nWord = (n, kind) => `${n} ${n === 1 ? KIND_WORDS[kind][0] : KIND_WORDS[kind][1]}`;

export function setFacet(kind, facet, value, on = null) {
  const s = S.get();
  const f = { ...s.facets, [kind]: { ...(s.facets[kind] || {}) } };
  const cur = new Set(f[kind][facet] || []);
  const want = on ?? !cur.has(value);
  if (want) cur.add(value); else cur.delete(value);
  if (cur.size) f[kind][facet] = [...cur]; else delete f[kind][facet];
  S.set({ facets: f });
}
export function clearFilters(kind = S.get().kind) {
  const s = S.get();
  S.set({ facets: { ...s.facets, [kind]: {} }, stars: { n: null, op: s.stars.op }, rejects: "hide" });
}

const showAll = { look: false };
function starChip(text, n, opts) {
  const c = chip(text, opts);
  c.querySelector(".cl").prepend(icon("star", { filled: true, cls: "tiny star-ic" }));
  c.setAttribute("aria-label", n === 5 ? "5 stars" : `${n} or more stars`);
  return c;
}
export function openFilters() {
  const body = h("div", { class: "stack", style: { gap: "4px" } });
  const done = button("", { kind: "primary", big: true, tid: "filters-done", cls: "btn-block" });
  const reset = button("Reset", { kind: "ghost", tid: "filters-reset" });
  const keepOpen = { more: false };
  const sheet = openSheet({ id: "filters", title: "Filters", body, tid: "filters-sheet", headExtra: reset, foot: done, focus: false });
  reset.addEventListener("click", () => clearFilters());
  done.addEventListener("click", () => sheet.close());

  function group(title, kids, sub) {
    return h("div", { class: "vsec", style: { margin: "10px 0 4px" } }, h("h3", {}, title, sub ? h("span", { class: "hint", style: { fontWeight: 400 } }, ` (${sub})`) : null), kids);
  }
  function facetChips(kind, facet, { limit = 0 } = {}) {
    const s = S.get();
    let vals = facetValues(lib.items, s, kind, facet);
    const sel = new Set(s.facets[kind]?.[facet] || []);
    if (!vals.length) return h("p", { class: "hint" }, FACET_DEFS[facet]?.empty || "Nothing to filter by yet");
    let extra = null;
    if (limit && vals.length > limit && !showAll[facet]) {
      const keep = vals.slice(0, limit);
      for (const v of vals) if (sel.has(v[0]) && !keep.includes(v)) keep.push(v);
      extra = h("button", { type: "button", class: "link", style: { margin: "6px 0" }, onclick: () => { showAll[facet] = true; draw(); } }, `Show all ${vals.length}`);
      vals = keep;
    }
    return h("div", {}, h("div", { class: "chips" }, vals.map(([v, n]) => chip(facetWord(facet, v), {
      pressed: sel.has(v), count: n, zero: !n, tid: `facet-${facet}-${v}`, data: { facet, value: v },
      onClick: () => setFacet(kind, facet, v) }))), extra);
  }

  function draw() {
    const s = S.get(), kind = s.kind;
    const scroll = sheet.body.scrollTop;
    sheet.titleEl.textContent = `Filters for ${KIND_WORDS[kind][2]}`;
    const n = s.stars.n;
    const starChips = h("div", { class: "chips", role: "radiogroup", "aria-label": "Stars" },
      chip("Any", { pressed: n == null, tid: "stars-any", onClick: () => S.set({ stars: { ...s.stars, n: null } }) }),
      [1, 2, 3, 4, 5].map((k) => starChip(k === 5 ? "5" : `${k}+`, k, { pressed: n === k, tid: `stars-${k}`, onClick: () => S.set({ stars: { ...s.stars, n: n === k ? null : k } }) })),
      chip("Unrated", { pressed: n === 0, tid: "stars-unrated", onClick: () => S.set({ stars: { ...s.stars, n: n === 0 ? null : 0 } }) }));
    const ops = n ? seg([["ge", "At least"], ["eq", "Exactly"], ["le", "At most"]], s.stars.op, (op) => S.set({ stars: { ...S.get().stars, op } }),
                       { role: "group", aria: "Compare stars", itemTid: (v) => `stars-op-${v}` }) : null;
    if (ops) ops.style.marginTop = "6px";
    const rej = seg([["hide", "Hide"], ["show", "Show"], ["only", "Only"]], s.rejects, (v) => S.set({ rejects: v }),
                    { role: "group", aria: "Rejected items", itemTid: (v) => `rejects-${v}` });
    const main = FACETS[kind].filter((f) => FACET_DEFS[f].group === "main");
    const more = FACETS[kind].filter((f) => FACET_DEFS[f].group === "more");
    const moreU = unroll("More filters", { tid: "more-filters", open: keepOpen.more, onToggle: (o) => { keepOpen.more = o; },
      hint: more.map((f) => FACET_DEFS[f].label).join(" · ") });
    put(moreU.body, more.map((f) => group(FACET_DEFS[f].label, facetChips(kind, f))));
    const sorts = sortOptions(lib.items);
    const sortSel = select(sorts, sorts.some(([k]) => k === s.sort) ? s.sort : "best", (v) => S.set({ sort: v }), { tid: "sort-select", aria: "Order" });
    const large = switchEl("Large pictures", s.large, (on) => S.set({ large: on }), { tid: "large-switch" });
    put(body,
      group("Stars", [starChips, ops], "applies to all three kinds"),
      group("Rejected", rej),
      main.map((f) => group(FACET_DEFS[f].label, facetChips(kind, f, { limit: f === "look" ? 12 : 0 }))),
      h("div", { style: { marginTop: "8px" } }, moreU),
      h("div", { class: "row", style: { marginTop: "8px" } }, h("span", { class: "lab", style: { fontWeight: 600, minWidth: "60px" } }, "Order"), h("div", { class: "grow" }, sortSel)),
      large);
    sheet.body.scrollTop = scroll;
    const c = counts(lib.items, s)[kind];
    done.querySelector(".label").textContent = `Show ${nWord(c, kind)}`;
  }
  draw();
  const off = S.on(["stars", "rejects", "facets", "kind", "sort", "large", "libVersion"], () => { if (sheet.isOpen()) draw(); else off(); });
  return sheet;
}
