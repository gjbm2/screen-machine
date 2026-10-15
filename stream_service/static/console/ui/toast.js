// Toasts: plain words, at most 3, 4 s (errors 6 s), an optional action; never over the Now bar, the tab bar or
// Review's bars.
import { h } from "../kit.js";
import { button } from "./controls.js";

let box = null;
function ensure() {
  if (box && box.isConnected) return box;
  box = document.getElementById("toasts") || h("div", { id: "toasts" });
  box.className = "toasts";
  box.setAttribute("role", "status");
  box.setAttribute("aria-live", "polite");
  if (!box.isConnected) document.body.append(box);
  return box;
}

// Keep the stack above whatever sits at the bottom of the viewport (tab bar, Now bar, Review bars).
export function placeToasts() {
  const b = ensure();
  const review = document.querySelector(".review");
  if (review) { b.style.top = "64px"; b.style.bottom = "auto"; return; }
  b.style.top = "auto";
  let top = window.innerHeight;
  for (const sel of ["#nowbar", "#tabbar"]) {
    const el = document.querySelector(sel);
    if (!el || el.hidden || !el.offsetParent && getComputedStyle(el).position !== "fixed") continue;
    const r = el.getBoundingClientRect();
    if (r.height > 0 && getComputedStyle(el).display !== "none") top = Math.min(top, r.top);
  }
  b.style.bottom = `${Math.round(window.innerHeight - top + 8)}px`;
}
window.addEventListener("resize", () => box && placeToasts());

export function toast(msg, { tone = "", ms = null, action = null, onAction = null } = {}) {
  const b = ensure();
  placeToasts();
  const t = h("div", { class: `toast${tone ? " tone-" + tone : ""}`, tid: "toast" },
    tone ? h("span", { class: "dot" }) : null, h("span", { class: "msg" }, msg),
    action ? button(action, { kind: "quiet", onClick: () => { onAction?.(); t.remove(); } }) : null);
  b.prepend(t);
  while (b.children.length > 3) b.lastElementChild.remove();
  setTimeout(() => t.remove(), ms ?? (tone === "bad" ? 6000 : 4000));
  return t;
}
export const toastErr = (msg, opts = {}) => toast(msg, { tone: "bad", ...opts });
