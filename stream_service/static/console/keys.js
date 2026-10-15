// One keydown listener (capture) for the whole page (design §6.1).  Context, in order: a text field (only Esc and
// Enter) → a sheet or menu → Review → the Viewer / inspector → the grid → global.  Every key is optional: each action
// also has a button.  There is no key for Go live or Back to art.
import { h } from "./kit.js";
import { S } from "./store.js";
import { topLayer, closeTop, openSheet } from "./ui/sheet.js";
import * as browse from "./browse/browse.js";
import * as viewer from "./browse/viewer.js";
import * as review from "./browse/review.js";
import * as tune from "./browse/tune.js";

let modeFn = () => {};
export function init({ setMode }) {
  modeFn = setMode;
  document.addEventListener("keydown", onKey, true);
}

const TEXT = new Set(["text", "search", "number", "email", "url", "password", ""]);
const isText = (t) => t && (t.isContentEditable || t.tagName === "TEXTAREA" || (t.tagName === "INPUT" && TEXT.has(t.type)));
let gAt = 0;

function onKey(e) {
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  const t = e.target, k = e.key;
  // ---- a text field: Esc (search: clear, then blur; others: blur), Enter is the field's own
  if (isText(t)) {
    if (k === "Escape") {
      e.preventDefault(); e.stopPropagation();
      if (t.dataset.testid === "search" && t.value) { t.value = ""; t.dispatchEvent(new Event("input", { bubbles: true })); S.set({ q: "" }); }
      else t.blur();
    }
    return;
  }
  if (t && t.tagName === "SELECT") { if (k === "Escape") { e.preventDefault(); e.stopPropagation(); t.blur(); } return; }
  const slider = t && t.tagName === "INPUT" && t.type === "range";
  if (slider && ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End", "PageUp", "PageDown"].includes(k)) return;

  const top = topLayer();
  // ---- a sheet or a menu: Esc closes it; its own keys stay with it
  if (top && (top.kind === "sheet" || top.kind === "menu")) {
    if (k === "Escape") { e.preventDefault(); e.stopPropagation(); closeTop(); }
    return;
  }
  const done = () => { e.preventDefault(); e.stopPropagation(); };

  // ---- global chords and keys
  if (k === "g") { gAt = Date.now(); done(); return; }
  if (Date.now() - gAt < 1500 && ["b", "o", "u"].includes(k)) {
    gAt = 0; done();
    if (review.isOpen()) review.close();
    modeFn({ b: "browse", o: "onscreen", u: "hood" }[k]);
    return;
  }
  if (k === "?") { done(); shortcuts(); return; }
  if (k === "/") { done(); if (review.isOpen()) review.close(); if (S.get().mode !== "browse") modeFn("browse"); browse.focusSearch(); return; }

  const mode = S.get().mode;
  if (mode !== "browse") { if (k === "Escape" && top) { done(); closeTop(); } return; }

  const inReview = review.isOpen();
  const sel = S.get().sel;
  const tile = t && t.classList?.contains("tile") ? t : null;
  const inGrid = !!tile && !inReview && !(top && top.kind === "viewer");
  const tileItem = inGrid ? tileIt(tile) : null;
  const subject = inGrid ? tileItem : sel ? viewer.V.it : null;

  // ---- Esc: the top layer only (Review → Viewer → selection)
  if (k === "Escape") {
    if (inReview) { done(); review.close(); return; }
    if (top && top.kind === "viewer") { done(); closeTop(); return; }
    if (sel) { done(); browse.closeViewer(); return; }
    return;
  }

  // ---- the grid: arrows move the selection; Enter / Space / f open Review (desktop) or the Viewer
  if (inGrid) {
    const g = browse.gridApi();
    const keys = g.list().map((x) => x.key);        // the whole list: End reaches the real end (focusKey loads the batch)
    const i = keys.indexOf(tile.dataset.key);
    const cols = g.cols();
    const go = (j) => {
      j = Math.max(0, Math.min(keys.length - 1, j));
      const key = keys[j];
      if (!key) return;
      g.focusKey(key);
      if (browse.isDesktop()) browse.select(key, { open: false });
    };
    if (k === "ArrowRight") { done(); go(i + 1); return; }
    if (k === "ArrowLeft") { done(); go(i - 1); return; }
    if (k === "ArrowDown") { done(); go(i + cols); return; }
    if (k === "ArrowUp") { done(); if (i < cols) browse.focusSearch(); else go(i - cols); return; }
    if (k === "Home") { done(); go(0); return; }
    if (k === "End") { done(); go(keys.length - 1); return; }
    if (k === "Enter" || k === " " || k === "f") {
      done();
      browse.select(tile.dataset.key, { open: true });
      if (browse.isDesktop()) setTimeout(() => browse.openReview(), 0);
      return;
    }
    if (k === "i") { done(); if (S.get().sel !== tile.dataset.key) browse.select(tile.dataset.key, { open: true }); document.querySelector(".inspector [data-testid=viewer-primary]")?.focus(); return; }
    if (k === "-" || k === "=") { done(); S.set({ large: k === "=" }); return; }
    if (tileItem && rateKey(e, tileItem)) return;
    if (["t", "o", "c", "e"].includes(k)) {
      done();
      if (S.get().sel !== tile.dataset.key) browse.select(tile.dataset.key, { open: true });
      itemKey(k);
      return;
    }
    if (S.get().sel === tile.dataset.key && ["r", "n", "b", "p", "s", "[", "]", "a"].includes(k)) {
      done();
      if (k === "[" || k === "]") viewer.stepVariant(k === "]" ? 1 : -1);
      else if (k === "a") S.set({ advance: !S.get().advance });
      else itemKey(k);
    }
    return;
  }

  // ---- the Viewer / inspector / Review
  if (!sel || !subject) {
    if (k === "-" || k === "=") { done(); S.set({ large: k === "=" }); }
    return;
  }
  if (k === "ArrowRight") { done(); browse.step(1); return; }
  if (k === "ArrowLeft") { done(); browse.step(-1); return; }
  if (inReview && k === " ") { done(); review.toggleBars(); return; }
  if (k === "f") { done(); if (inReview) review.close(); else browse.openReview(); return; }
  if (rateKey(e, subject)) return;
  if (k === "[" || k === "]") { done(); viewer.stepVariant(k === "]" ? 1 : -1); return; }
  if (k === "a") { done(); S.set({ advance: !S.get().advance }); return; }
  if (["t", "o", "c", "e", "r", "n", "b", "p", "s"].includes(k)) { done(); itemKey(k); return; }
  if (k === "-" || k === "=") { done(); S.set({ large: k === "=" }); }
}

function tileIt(tile) {
  return browse.currentList().find((x) => x.key === tile.dataset.key) || null;
}
function rateKey(e, it) {
  const k = e.key;
  const done = () => { e.preventDefault(); e.stopPropagation(); };
  if (/^[0-5]$/.test(k)) { done(); viewer.rateAndMaybeAdvance(it, { stars: Number(k) }); return true; }
  if (k === "x") { done(); viewer.rateAndMaybeAdvance(it, { reject: !it._reject }); return true; }
  if (k === "u") { done(); viewer.rateAndMaybeAdvance(it, { stars: 0, reject: false }); return true; }
  return false;
}
function itemKey(k) {
  const inReview = review.isOpen();
  switch (k) {
    case "t": inReview ? review.focusTag() : viewer.focusTag(); break;
    case "o": viewer.showOn(); break;
    case "c": viewer.liveAction(); break;
    case "e": inReview ? review.toggleTune() : viewer.toggleTune(); break;
    case "r": case "n": case "b": case "p": case "s": {
      if (inReview) { if (!review.tuneIsOpen()) review.toggleTune(true); }
      else if (!viewer.tuneOpen()) viewer.toggleTune(true);
      if (k === "r") { tune.tourStop(); tune.vary("wide"); }
      else if (k === "n") { tune.tourStop(); tune.vary("near"); }
      else if (k === "b") tune.back();
      else if (k === "p") tune.tourToggle();
      else if (k === "s") tune.startSave();
      break;
    }
    default: break;
  }
}

const SHORTCUTS = [
  ["Anywhere", [["/", "Search"], ["g b · g o · g u", "Browse · On screen · Under the hood"], ["?", "These shortcuts"], ["Esc", "Close the top layer"]]],
  ["The grid", [["Arrows", "Move (and play, on a desktop)"], ["Home / End", "First / last"], ["Enter, Space, f", "Review (desktop) or the Viewer"], ["0–5 · x · u", "Stars · reject · clear"],
    ["t · o · c · e", "Tag · Show on · live action · Tune"], ["- / =", "Smaller / larger pictures"]]],
  ["Viewer and Review", [["← →", "Previous / next"], ["[ ]", "Previous / next variation"], ["0–5 · x · u", "Stars · reject · clear"], ["t", "Add a tag"],
    ["e", "Tune"], ["r · n · b · p · s", "Shuffle · nudge · back · tour · save look"], ["o", "Show on a screen"], ["c", "Play / add / use on the live screen"],
    ["a", "Next after rating"], ["f", "Review on / off"], ["Space", "Hide the controls (Review)"]]],
  ["Show on", [["1 · 2 · 3", "Pick a screen"], ["Enter", "Show it"], ["Esc", "Cancel"]]],
];
function shortcuts() {
  const body = h("div", { class: "stack" }, SHORTCUTS.map(([title, rows]) => h("div", {}, h("h3", { class: "group-h" }, title),
    h("div", { class: "keys-list" }, rows.map(([k, w]) => [h("span", { class: "kbd" }, k), h("span", {}, w)])))),
    h("p", { class: "hint" }, "There is no key for Go live or Back to art: those are buttons on the On screen page."));
  openSheet({ id: "shortcuts", title: "Keyboard shortcuts", body, tid: "shortcuts-sheet" });
}
