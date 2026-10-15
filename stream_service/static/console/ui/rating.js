// The rating strip: 5 stars (44 px targets) + Reject.  Clicking the current value clears it.
import { h } from "../kit.js";
import { icon } from "./icons.js";

/**
 * ratingStrip(it, rate, {size: "big"|"hover", tids}) -> element with .update(it)
 * rate(it, patch) does the work (optimistic, reverts on failure).
 */
export function ratingStrip(it, rate, { size = "big", tids = true } = {}) {
  const el = h("div", { class: size === "hover" ? "trate" : "rate", role: "group", "aria-label": "Rating", tid: size === "hover" ? "tile-rate" : null });
  const stars = [1, 2, 3, 4, 5].map((n) => {
    const b = h("button", { type: "button", class: size === "hover" ? "" : "star", "aria-label": `Rate ${n} star${n > 1 ? "s" : ""}`,
                            tid: tids && size !== "hover" ? `rate-${n}` : null, tabindex: size === "hover" ? "-1" : null, dataset: { n: String(n) } },
                   icon("star"));
    b.addEventListener("click", (e) => { e.stopPropagation(); const cur = el._it; rate(cur, { stars: cur._rating === n ? 0 : n }); });
    return b;
  });
  const rej = size === "hover"
    ? h("button", { type: "button", class: "rej", "aria-label": "Reject", tabindex: "-1" }, icon("ban"))
    : h("button", { type: "button", class: "chip rej", "aria-label": "Reject", "aria-pressed": "false", tid: tids ? "rate-reject" : null }, icon("ban"), h("span", { class: "cl" }, "Reject"));
  rej.addEventListener("click", (e) => { e.stopPropagation(); const cur = el._it; rate(cur, { reject: !cur._reject }); });
  el.append(...stars, rej);
  el.update = (cur) => {
    el._it = cur;
    stars.forEach((b, i) => {
      const on = i + 1 <= (cur._rating || 0);
      b.classList.toggle("on", on);
      b.querySelector("svg")?.setAttribute("fill", on ? "currentColor" : "none");
      b.setAttribute("aria-pressed", String(cur._rating === i + 1));
    });
    rej.classList.toggle("on", !!cur._reject);
    rej.setAttribute("aria-pressed", String(!!cur._reject));
    const lab = rej.querySelector(".cl");
    if (lab) lab.textContent = cur._reject ? "Rejected" : "Reject";
  };
  el.update(it);
  return el;
}
