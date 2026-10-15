// The picture grid (Browse and the Picker): tiles in the page's own scroll, 60 per batch (a sentinel 600 px ahead,
// plus "Show more"), lazy thumbnails, warm hashed placeholders, at most one badge, a stars pill, "On <screen>"
// ribbons, and on a fine pointer a star strip in place of the subtitle on hover.  Tiles carry no other buttons.
import { h, put, hashHue } from "../kit.js";
import { subtitle, cleanDescription, slowWords } from "../words.js";
import { badge } from "../ui/controls.js";
import { ratingStrip } from "../ui/rating.js";
import { icon } from "../ui/icons.js";

export const BATCH = 60;
const fine = () => window.matchMedia?.("(hover: hover) and (pointer: fine)").matches;

/**
 * createGrid({mode, onSelect(it, tile, how), rate, ribbons: () => Map(key -> [names]), selected: () => key})
 * -> {el, more, setList(list, {keepLimit}), refresh(), tileOf(key), focusKey(key), cols(), keys(), list()}
 */
export function createGrid({ mode = "browse", onSelect, rate, ribbons = () => new Map(), selected = () => null, tid = "grid" } = {}) {
  const el = h("div", { class: `grid${mode === "pick" ? " pick-grid" : ""}`, role: "listbox", "aria-label": mode === "pick" ? "Pick one" : "Library", tid });
  const moreBox = h("div", { class: "show-more" });
  const sentinel = h("div", { "aria-hidden": "true", style: { height: "1px" } });
  const tiles = new Map();
  let list = [], limit = BATCH, roving = null;

  const thumbIO = "IntersectionObserver" in window ? new IntersectionObserver((es) => {
    for (const e of es) if (e.isIntersecting) { const img = e.target; thumbIO.unobserve(img); if (img.dataset.src && !img.src) img.src = img.dataset.src; }
  }, { rootMargin: "300px 0px" }) : null;
  const moreIO = "IntersectionObserver" in window ? new IntersectionObserver((es) => {
    if (es.some((e) => e.isIntersecting) && limit < list.length) { limit += BATCH; render(true); }
  }, { rootMargin: "600px 0px" }) : null;
  moreIO?.observe(sentinel);

  function makeTile(it) {
    const hue = hashHue(it.plugin || it.name);
    const ph = h("span", { class: "ph", "aria-hidden": "true" }, (it.title || "?").replace(/[^A-Za-z0-9]/g, "").slice(0, 2) || "?");
    const thumb = h("div", { class: "thumb" }, ph);
    thumb.style.setProperty("--h", hue);
    if (it.thumb) {
      const img = h("img", { alt: "", decoding: "async", draggable: "false" });
      img.dataset.src = it.thumb;
      img.addEventListener("load", () => img.classList.add("ok"));
      img.addEventListener("error", () => img.remove());
      thumb.append(img);
      if (thumbIO) thumbIO.observe(img); else img.src = it.thumb;
    }
    const badgeBox = h("span", { class: "tbadge" });
    const ribbonBox = h("span", { class: "ribbon" });
    const stars = h("span", { class: "tstars", tid: "tile-stars", hidden: true });
    thumb.append(badgeBox, ribbonBox, stars);
    const title = h("div", { class: "ttitle" }, it.title);
    const sub = h("div", { class: "tsub" }, subtitle(it));
    const desc = it.kind !== "scene" && it.description ? h("div", { class: "tdesc" }, cleanDescription(it.description)) : null;
    const rateEl = mode === "browse" && rate && fine() ? ratingStrip(it, rate, { size: "hover" }) : null;
    const body = h("div", { class: "tbody" }, title, sub, rateEl, desc);
    const t = h("div", { class: "tile", role: "option", tabindex: "-1", "aria-selected": "false", tid: mode === "pick" ? "picker-tile" : "tile",
                         dataset: { key: it.key }, "aria-label": `${it.title}, ${subtitle(it)}` }, thumb, body);
    t.addEventListener("click", (e) => { if (e.target.closest(".trate")) return; onSelect?.(it, t, "click"); });
    t.addEventListener("focus", () => { roving = it.key; for (const x of el.querySelectorAll('.tile[tabindex="0"]')) if (x !== t) x.tabIndex = -1; t.tabIndex = 0; });
    if (it.preview && mode === "browse") {                // dormant until the server has recorded clips
      let ht = null, v = null;
      t.addEventListener("pointerenter", (e) => { if (e.pointerType !== "mouse") return; ht = setTimeout(() => {
        for (const o of document.querySelectorAll(".tile video.hover")) o.remove();
        v = h("video", { class: "hover", muted: true, loop: true, playsinline: true, autoplay: true, src: it.preview });
        v.muted = true; thumb.append(v); v.play().catch(() => {});
      }, 400); });
      t.addEventListener("pointerleave", () => { clearTimeout(ht); if (v) { v.pause(); v.remove(); v = null; } });
    }
    const rec = { el: t, it, badgeBox, ribbonBox, stars, rateEl, sub };
    tiles.set(it.key, rec);
    update(rec);
    return rec;
  }

  function badgeFor(it, rib) {
    if (it.load === "error" || it._broken) return badge("Broken", "bad", { onPic: true, tid: "tile-badge" });
    if (it.slow) return badge(it.slow.status === "broken" ? "Broken" : "Too slow", it.slow.status === "broken" ? "bad" : "warn", { onPic: true, tid: "tile-badge" });
    if (mode === "pick") return null;
    if (it.status && it.status !== "accepted") return badge("Needs work", "warn", { onPic: true, tid: "tile-badge" });
    if (rib?.length) return null;                        // the ribbon says it
    if (it._new) return badge("New", "info", { onPic: true, tid: "tile-badge" });
    if (it._reject) return badge("Rejected", "mute", { onPic: true, tid: "tile-badge" });
    return null;
  }

  function update(rec) {
    const it = rec.it;
    const rib = ribbons().get(it.key) || null;
    const b = badgeFor(it, rib);
    put(rec.badgeBox, b);
    if (b && it.slow) b.title = slowWords(it.slow);
    put(rec.ribbonBox, rib?.length ? badge(mode === "pick" ? "On now" : `On ${rib.join(", ")}`, "live", { onPic: true, tid: "tile-ribbon" }) : null);
    const n = it._rating || 0, looks = it._looks?.length || 0;
    rec.stars.hidden = mode === "pick" || (!n && !looks);
    put(rec.stars, n ? h("span", { class: "st", "aria-label": `${n} star${n > 1 ? "s" : ""}` }, Array.from({ length: n }, () => icon("star", { filled: true, cls: "tiny" }))) : null, looks ? h("span", { class: "lk", "aria-label": `${looks} saved looks` }, `◆${looks}`) : null);
    rec.el.classList.toggle("rejected", !!it._reject);
    const sel = selected() === it.key;
    rec.el.dataset.selected = String(sel);
    rec.el.setAttribute("aria-selected", String(sel));
    rec.rateEl?.update(it);
  }

  function render(append = false) {
    const shown = list.slice(0, limit);
    const same = el.children.length === shown.length && shown.every((it, i) => el.children[i]?.dataset.key === it.key && tiles.get(it.key)?.el === el.children[i]);
    if (same) { /* the same tiles in the same order: leave the DOM (and keyboard focus) alone */ }
    else if (!append) {
      const ae = document.activeElement;
      put(el, shown.map((it) => (tiles.get(it.key) || makeTile(it)).el));
      if (ae && ae.classList?.contains("tile") && ae.isConnected && document.activeElement !== ae) ae.focus({ preventScroll: true });
    }
    else for (const it of shown) { const r = tiles.get(it.key) || makeTile(it); if (r.el.parentNode !== el) el.append(r.el); }
    if (limit < list.length) {
      put(moreBox, h("button", { type: "button", class: "btn btn-outline", tid: "show-more", onclick: () => { limit += BATCH; render(true); } }, `Show more (${list.length - limit} left)`), sentinel);
      moreBox.hidden = false;
    } else { put(moreBox); moreBox.hidden = true; }
    const keys = shown.map((i) => i.key);
    if (!keys.includes(roving)) roving = keys.includes(selected()) ? selected() : keys[0] || null;
    for (const k of keys) { const r = tiles.get(k); r.el.tabIndex = k === roving ? 0 : -1; update(r); }
  }

  return {
    el, more: moreBox,
    setList(next, { keepLimit = false } = {}) {
      list = next;
      if (!keepLimit) limit = BATCH;
      render();
    },
    replaceItems(byKey) { for (const [k, r] of [...tiles]) { const n = byKey.get(k); if (!n || n.thumb !== r.it.thumb) tiles.delete(k); else r.it = n; } },
    refresh() { for (const r of tiles.values()) if (r.el.isConnected) update(r); },
    tileOf: (key) => tiles.get(key)?.el || null,
    ensureShown(key) {
      const i = list.findIndex((x) => x.key === key);
      if (i >= limit) { limit = Math.ceil((i + 1) / BATCH) * BATCH; render(true); }
      return tiles.get(key)?.el || null;
    },
    focusKey(key, { scroll = true } = {}) {
      const t = this.ensureShown(key);
      if (!t) return;
      roving = key;
      for (const x of el.querySelectorAll('.tile[tabindex="0"]')) x.tabIndex = -1;
      t.tabIndex = 0;
      t.focus({ preventScroll: true });
      if (scroll) t.scrollIntoView({ block: "nearest" });
    },
    cols() {
      const cs = getComputedStyle(el).gridTemplateColumns.split(" ").filter(Boolean).length;
      return Math.max(1, cs || 1);
    },
    list: () => list,
    limit: () => limit,
    shownKeys: () => list.slice(0, limit).map((i) => i.key),
  };
}
