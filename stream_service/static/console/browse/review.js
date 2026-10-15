// Review (desktop): a dark stage nothing covers but the caption, ‹ › in their own gutters, a filmstrip of ±10, a
// rating / tags / variations bar, Tune at the side (it shrinks the picture, never covers it).  "Only unrated" and
// "Next after rating" make judging an import quick; Space hides the bars.  The same <video> moves in from the
// inspector (no re-cue).
import { h, put } from "../kit.js";
import { S } from "../store.js";

import * as room from "../data/room.js";
import * as preview from "./preview.js";
import * as tune from "./tune.js";
import * as viewer from "./viewer.js";
import { rate } from "../data/ratings.js";
import { button, chip, switchEl } from "../ui/controls.js";
import { icon } from "../ui/icons.js";
import { ratingStrip } from "../ui/rating.js";
import { pushLayer, closeLayer, replaceHash } from "../ui/sheet.js";
import { subtitle, cleanTitle } from "../words.js";
import { placeToasts } from "../ui/toast.js";

let R = null;   // {el, layer, ...}
let nav = { step: () => {}, list: () => [], select: () => {}, counter: () => "" };
export function setNav(n) { nav = { ...nav, ...n }; }
export const isOpen = () => !!R;

export function open() {
  if (R || !viewer.V.it) return;
  const pvhost = h("div", { class: "pvhost" });
  const capText = h("span", {});
  const cap = h("span", { class: "cap" }, h("span", { class: "dot" }), capText);
  const stage = h("div", { class: "stage", tid: "review-stage" }, pvhost, h("div", { class: "caption" }, cap));
  const title = h("span", { class: "rtitle" });
  const sub = h("span", { class: "rsub" });
  const counter = h("span", { class: "counter" });
  const only = switchEl("Only unrated", S.get().onlyUnrated, (on) => S.set({ onlyUnrated: on }), { tid: "review-only-unrated", word: false });
  const adv = switchEl("Next after rating", S.get().advance, (on) => S.set({ advance: on }), { tid: "review-advance", word: false });
  for (const r of [only, adv]) { r.style.gap = "8px"; r.style.fontSize = "15px"; }
  const tuneBtn = button("Tune", { kind: "quiet", icon: "sliders", tid: "review-tune", onClick: () => toggleTune() });
  const closeBtn = button(null, { kind: "quiet", icon: "x", aria: "Close Review (Esc)", tid: "review-close", cls: "btn-icon", onClick: () => close() });
  const topbar = h("div", { class: "rbar", tid: "review-bars" }, closeBtn, h("div", { style: { minWidth: 0, display: "flex", gap: "10px", alignItems: "baseline", flex: "1 1 auto" } }, title, sub),
    counter, only, adv, tuneBtn);
  const prev = button(null, { kind: "quiet", icon: "left", aria: "Previous (←)", tid: "review-prev", onClick: () => nav.step(-1) });
  const next = button(null, { kind: "quiet", icon: "right", aria: "Next (→)", tid: "review-next", onClick: () => nav.step(1) });
  const side = h("div", { class: "tune-side", hidden: true });
  const mid = h("div", { class: "rmid" }, h("div", { class: "gut" }, prev), h("div", { class: "rstage-wrap" }, stage), h("div", { class: "gut" }, next), side);
  const strip = ratingStrip(viewer.V.it, (it, p) => viewer.rateAndMaybeAdvance(it, p), { tids: false });
  const tagsBox = h("span", { class: "chips", style: { alignItems: "center" } });
  const tagIn = h("input", { placeholder: "+ tag (t)", "aria-label": "Add tags (comma separated)", autocomplete: "off", list: "own-tags", class: "rtag" });
  tagIn.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    const it = viewer.V.it, add = tagIn.value.split(",").map((x) => x.trim().toLowerCase()).filter(Boolean);
    tagIn.value = "";
    if (it && add.length) rate(it, { tags: [...new Set([...it._mytags, ...add])] });
  });
  const sys = h("span", { class: "rsys" });
  const vars = h("span", { class: "chips" });
  const showOn = button("Show on…", { kind: "quiet", onClick: () => viewer.showOn() });
  showOn.append(h("span", { class: "kbd", style: { marginLeft: "6px" } }, "o"));
  const play = button("", { kind: "primary", onClick: () => viewer.liveAction() });
  const bottom = h("div", { class: "rbottom" }, strip, tagsBox, h("span", { class: "tag-input" }, tagIn), sys, vars, h("span", { class: "ract" }, showOn, play));
  const film = h("div", { class: "filmstrip", tid: "review-filmstrip", role: "listbox", "aria-label": "Nearby items" });
  const el = h("div", { class: "review", tid: "review", role: "dialog", "aria-modal": "true", "aria-label": "Review" }, topbar, mid, bottom, film);
  document.body.append(el);
  document.body.classList.add("noscroll");
  const it0 = viewer.V.it;
  const layer = pushLayer("review", "review", () => teardown(), { hash: `#review/${it0.kind}/${encodeURIComponent(it0.name)}`, el });
  R = { el, layer, pvhost, stage, title, sub, counter, strip, tagsBox, tagIn, sys, vars, play, film, side, only, adv, tuneOpen: false, capText, cap };
  preview.host(pvhost);
  S.set({ review: true });
  draw();
  placeToasts();
  const offs = [S.on(["libVersion", "sel", "preview", "onlyUnrated", "advance", "roomStatus", "compose"], () => draw()), tune.onTune(() => { if (R?.tuneOpen) drawTune(); })];
  R.offs = offs;
  if (viewer.tuneOpen()) toggleTune(true);
  el.focus?.();
}

function teardown() {
  if (!R) return;
  R.offs.forEach((f) => f());
  const reopenTune = R.tuneOpen;
  R.el.remove();
  R = null;
  document.body.classList.remove("noscroll");
  S.set({ review: false });
  viewer.hostPreview();
  if (reopenTune) viewer.toggleTune(true);
  placeToasts();
  const it = viewer.V.it;
  if (it) nav.reveal?.(it.key);
}
export function close() { if (R) closeLayer(R.layer); }

export function toggleTune(open = !R?.tuneOpen) {
  if (!R) return;
  R.tuneOpen = open;
  R.side.hidden = !open;
  if (open) { viewer.toggleTune(false); viewer.el.querySelector('[data-testid="tune-unroll"] .body')?.replaceChildren(); drawTune(); }
  else R.side.replaceChildren();
}
function drawTune() {
  if (!R?.tuneOpen) return;
  const it = viewer.V.it;
  if (!it) return;
  const ae = document.activeElement;
  if (ae && R.side.contains(ae) && ae.type === "range") return;
  tune.renderPanel(R.side, it, {});
}
export const tuneIsOpen = () => !!R?.tuneOpen;
export const focusTag = () => R?.tagIn.focus();
export function toggleBars() { R?.el.classList.toggle("bare"); }

function draw() {
  if (!R) return;
  const it = viewer.V.it;
  if (!it) return;
  replaceHash(`#review/${it.kind}/${encodeURIComponent(it.name)}`);
  R.title.textContent = it.title;
  R.sub.textContent = subtitle(it);
  R.counter.textContent = nav.counter(it);
  R.strip.update(it);
  R.only.sw.set(S.get().onlyUnrated);
  R.adv.sw.set(S.get().advance);
  const p = S.get().preview;
  R.capText.textContent = p.text || "Starting…";
  R.cap.className = `cap tone-${{ playing: "live", failed: "warn", full: "warn" }[p.state] || "info"}`;
  put(R.tagsBox, (it._mytags || []).map((t) => chip(t, { removable: true, removeLabel: `Remove the tag ${t}`, onRemove: () => rate(it, { tags: it._mytags.filter((x) => x !== t) }) })));
  R.sys.textContent = [...(it.ff?.look || []).slice(0, 3), ...(it.ff?.dims || [])].join(" · ");
  const ps = it.presets || [];
  put(R.vars, ps.length > 1 ? ps.map((pr) => chip(cleanTitle(pr.label), { pressed: pr.name === viewer.V.variant, onClick: () => viewer.setVariant(pr.name) })) : null);
  const a = viewer.actionFor(it);
  R.play.hidden = a === "showon";
  const name = room.nameOf(S.get().target);
  R.play.querySelector(".label").textContent = a === "play" ? `Play on ${name}` : a === "add" ? `Add to ${name}` : a === "remove" ? `Take off ${name}` : "Use for the next change";
  if (!R.play.querySelector(".kbd")) R.play.append(h("span", { class: "kbd", style: { marginLeft: "6px" } }, "c"));
  drawFilm(it);
}
function drawFilm(it) {
  const list = nav.list();
  const i = list.findIndex((x) => x.key === it.key);
  const lo = Math.max(0, i - 10), hi = Math.min(list.length, (i < 0 ? 0 : i) + 11);
  const items = i < 0 ? [it] : list.slice(lo, hi);
  put(R.film, items.map((x) => {
    const b = h("button", { type: "button", role: "option", tid: "filmstrip-item", dataset: { key: x.key }, "aria-label": x.title, title: x.title,
                            "aria-current": x.key === it.key ? "true" : null, "aria-selected": String(x.key === it.key) });
    if (x.thumb) b.style.backgroundImage = `url("${x.thumb}")`;
    b.addEventListener("click", () => nav.select(x.key));
    return b;
  }));
  R.film.querySelector('[aria-current="true"]')?.scrollIntoView({ inline: "center", block: "nearest" });
}
