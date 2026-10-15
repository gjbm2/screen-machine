// Layers (sheets, menus, Review, the phone Viewer) with one history entry each, so the phone's Back gesture and Esc
// close the top layer first.  Bottom sheets on a phone (drag handle, swipe down), centred dialogs on desktop, a right
// side sheet for the tablet Viewer.  Focus is trapped in the top layer and returned to its opener.
import { h, put } from "../kit.js";
import { icon } from "./icons.js";

export const layers = [];          // {id, kind, el, close(fromHistory)}
let skipPops = 0;
let routeFn = () => {};
export const onRoute = (fn) => { routeFn = fn; };

// body.viewer-top while the Viewer (phone sheet / tablet side sheet) is the top layer: the Now bar rises above it
// (its +2 min / Put back stay reachable) and the Viewer leaves room for it; any sheet over the Viewer covers it again.
function changed() {
  const top = layers[layers.length - 1];
  document.body.classList.toggle("viewer-top", !!top && top.kind === "viewer");
  window.dispatchEvent(new Event("sm-layers"));
}

window.addEventListener("popstate", () => {
  if (skipPops > 0) { skipPops--; return; }
  if (layers.length) { const L = layers.pop(); L.teardown(); changed(); return; }
  routeFn();
});

// Push a layer: one history entry (hash optional).  teardown() removes it from the DOM.
export function pushLayer(id, kind, teardown, { hash = null, el = null } = {}) {
  const L = { id, kind, el, teardown, opener: document.activeElement };
  try { history.pushState({ layer: id }, "", hash || location.href); } catch (_) {}
  layers.push(L);
  changed();
  return L;
}
export const topLayer = () => layers[layers.length - 1] || null;
export const hasLayer = (kind) => layers.some((L) => L.kind === kind);

// Close a layer (and everything above it) from the UI: tears them down, then steps history back past their entries.
export function closeLayer(L) {
  const i = layers.indexOf(L);
  if (i < 0) return false;
  const gone = layers.splice(i);
  for (const x of gone.reverse()) { try { x.teardown(); } catch (e) { console.error(e); } }
  changed();
  skipPops++;
  try { history.go(-gone.length); } catch (_) { skipPops--; }
  return true;
}
export function closeTop() {
  const L = topLayer();
  return L ? closeLayer(L) : false;
}
export function replaceHash(hash) {
  try { history.replaceState(history.state, "", hash); } catch (_) {}
}

function focusables(root) {
  return [...root.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])')]
    .filter((e) => !e.disabled && e.offsetParent !== null);
}
function trap(el) {
  el.addEventListener("keydown", (e) => {
    if (e.key !== "Tab") return;
    const f = focusables(el);
    if (!f.length) return;
    const first = f[0], last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  });
}

/**
 * openSheet({id, title, body, foot, tid, cls, wide, side, full, onClose, focus}) -> {el, body, foot, head, close(), layer}
 * body / foot: Nodes (or arrays).  onClose runs on every way out (button, Esc, Back, scrim, swipe).
 */
export function openSheet({ id = "sheet", title = "", body = null, foot = null, tid, cls = "", wide = false, side = false, full = false,
                            onClose, focus = true, hash = null, headExtra = null, scrim = true } = {}) {
  const opener = document.activeElement;
  const titleEl = h("h2", { id: `${id}-t` }, title);
  const closeBtn = h("button", { type: "button", class: "btn btn-ghost btn-icon", "aria-label": "Close", tid: `${id}-close` }, icon("x"));
  const head = h("div", { class: "sheet-head" }, titleEl, headExtra, closeBtn);
  const bodyEl = h("div", { class: "sheet-body" });
  const footEl = h("div", { class: "sheet-foot" });
  if (body) put(bodyEl, body);
  if (foot) put(footEl, foot); else footEl.hidden = true;
  const el = h("div", { class: `sheet${wide ? " wide" : ""}${side ? " side" : ""}${full ? " full" : ""}${cls ? " " + cls : ""}`, role: "dialog",
                        "aria-modal": "true", "aria-labelledby": title ? `${id}-t` : null, tabindex: "-1", tid },
               h("div", { class: "handle", "aria-hidden": "true" }), title || headExtra ? head : null, bodyEl, footEl);
  if (!title && !headExtra) { closeBtn.remove(); }
  const sc = scrim ? h("div", { class: "sheet-scrim", "aria-hidden": "true" }) : null;
  const host = document.getElementById("sheets") || document.body;
  if (sc) host.append(sc);
  host.append(el);
  document.body.classList.add("noscroll");
  trap(el);
  let closed = false;
  const teardown = () => {
    if (closed) return;
    closed = true;
    el.remove(); sc?.remove();
    if (!layers.some((L) => L.kind === "sheet" || L.kind === "viewer")) document.body.classList.remove("noscroll");
    try { onClose?.(); } catch (e) { console.error(e); }
    if (opener && opener.isConnected && typeof opener.focus === "function") { try { opener.focus({ preventScroll: true }); } catch (_) {} }
  };
  const layer = pushLayer(id, side || cls.includes("viewer-sheet") ? "viewer" : "sheet", teardown, { hash, el });
  const close = () => { if (!closed) closeLayer(layer); };
  closeBtn.addEventListener("click", close);
  sc?.addEventListener("click", close);
  // swipe down on the handle or the head closes a bottom sheet (> 80 px)
  let y0 = null;
  const startDrag = (e) => { if (window.innerWidth >= 700) return; y0 = e.touches ? e.touches[0].clientY : e.clientY; };
  const endDrag = (e) => {
    if (y0 == null) return;
    const y = e.changedTouches ? e.changedTouches[0].clientY : e.clientY;
    if (y - y0 > 80) close();
    y0 = null;
  };
  for (const zone of [el.querySelector(".handle"), head]) {
    zone?.addEventListener("touchstart", startDrag, { passive: true });
    zone?.addEventListener("touchend", endDrag);
  }
  if (focus) requestAnimationFrame(() => { const f = focusables(bodyEl)[0] || closeBtn; try { (f.isConnected ? f : el).focus({ preventScroll: true }); } catch (_) {} });
  return { el, body: bodyEl, foot: footEl, head, titleEl, close, layer, isOpen: () => !closed };
}

/** A small popover menu next to an anchor.  items: [{label, sub, tid, onClick, header, sep, checked}] */
export function openMenu(anchor, items, { tid = "menu", align = "right" } = {}) {
  const el = h("div", { class: "menu", role: "menu", tid });
  for (const it of items) {
    if (!it) continue;
    if (it.sep) { el.append(h("hr")); continue; }
    if (it.header) { el.append(h("div", { class: "mh" }, it.header)); continue; }
    if (it.node) { el.append(it.node); continue; }
    const b = h("button", { type: "button", class: "mi", role: it.checked != null ? "menuitemcheckbox" : "menuitem", tid: it.tid,
                            "aria-checked": it.checked != null ? String(!!it.checked) : null, disabled: it.disabled ? true : null },
      it.icon ? icon(it.icon) : null, h("span", {}, it.label), it.sub ? h("span", { class: "sub" }, it.sub) : null,
      it.checked != null ? h("span", { class: "sub" }, it.checked ? "On" : "Off") : null);
    b.addEventListener("click", () => { close(); it.onClick?.(); });
    el.append(b);
  }
  (document.getElementById("sheets") || document.body).append(el);
  const r = anchor.getBoundingClientRect();
  const w = el.offsetWidth, hgt = el.offsetHeight;
  let left = align === "right" ? r.right - w : r.left;
  left = Math.max(8, Math.min(window.innerWidth - w - 8, left));
  let top = r.bottom + 6;
  if (top + hgt > window.innerHeight - 8) top = Math.max(8, r.top - hgt - 6);
  Object.assign(el.style, { left: `${left}px`, top: `${top}px` });
  trap(el);
  const outside = (e) => { if (!el.contains(e.target) && e.target !== anchor && !anchor.contains(e.target)) close(); };
  let closed = false;
  const teardown = () => { if (closed) return; closed = true; el.remove(); document.removeEventListener("pointerdown", outside, true); try { anchor.focus({ preventScroll: true }); } catch (_) {} };
  const layer = pushLayer("menu", "menu", teardown, { el });
  const close = () => { if (!closed) closeLayer(layer); };
  setTimeout(() => document.addEventListener("pointerdown", outside, true), 0);
  requestAnimationFrame(() => el.querySelector(".mi")?.focus({ preventScroll: true }));
  return { el, close };
}
