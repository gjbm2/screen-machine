// Small building blocks (design §5.2): buttons, chips, segmented, switch, select, slider, unroll, status row, badge.
import { h, put } from "../kit.js";
import { icon } from "./icons.js";

export function button(label, { kind = "quiet", tid, icon: ic, onClick, title, disabled, cls = "", big = false, aria } = {}) {
  const b = h("button", { type: "button", class: `btn btn-${kind}${big ? " btn-big" : ""}${cls ? " " + cls : ""}`, tid, title,
                          "aria-label": aria, disabled: !!disabled }, ic ? icon(ic) : null, label != null ? h("span", { class: "label" }, label) : null);
  if (onClick) b.addEventListener("click", (e) => onClick(e, b));
  return b;
}
export function setLabel(btn, label) {
  const l = btn.querySelector(".label");
  if (l) l.textContent = label; else btn.append(h("span", { class: "label" }, label));
}
export function busy(btn, on, verb) {
  if (on) {
    btn._was = btn._was ?? btn.querySelector(".label")?.textContent;
    btn.disabled = true;
    btn.querySelector(".icon")?.setAttribute("hidden", "");
    if (!btn.querySelector(".spin")) btn.prepend(h("span", { class: "spin" }));
    if (verb) setLabel(btn, verb);
  } else {
    btn.disabled = false;
    btn.querySelector(".spin")?.remove();
    btn.querySelector(".icon")?.removeAttribute("hidden");
    if (btn._was != null) setLabel(btn, btn._was);
    btn._was = null;
  }
}

export function chip(label, { pressed = null, tid, onClick, count = null, removable = false, onRemove, title, cls = "", data, zero = false, removeLabel, removeTid } = {}) {
  const c = h("button", { type: "button", class: `chip${zero ? " zero" : ""}${cls ? " " + cls : ""}`, tid, title,
                          "aria-pressed": pressed == null ? null : String(!!pressed), dataset: data || null });
  if (pressed) c.append(icon("check"));
  c.append(h("span", { class: "cl" }, label));
  if (count != null) c.append(h("span", { class: "n" }, String(count)));
  if (removable) {
    const x = h("span", { class: "x", role: "button", tabindex: "0", "aria-label": removeLabel || `Remove ${label}`, tid: removeTid || null }, icon("x"));
    x.addEventListener("click", (e) => { e.stopPropagation(); onRemove?.(e); });
    x.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); e.stopPropagation(); onRemove?.(e); } });
    c.append(x);
  }
  if (onClick) c.addEventListener("click", (e) => onClick(e, c));
  return c;
}

export function seg(options, value, onChange, { tid, aria, role = "tablist", itemTid } = {}) {
  const el = h("div", { class: "seg", role, "aria-label": aria, tid });
  const btns = options.map(([v, label, extra]) => {
    const b = h("button", { type: "button", role: role === "tablist" ? "tab" : null, tid: itemTid ? itemTid(v) : null,
                            "aria-selected": role === "tablist" ? String(v === value) : null, "aria-pressed": role !== "tablist" ? String(v === value) : null,
                            tabindex: role === "tablist" ? (v === value ? "0" : "-1") : null, dataset: { v } }, label, extra || null);
    b.addEventListener("click", () => onChange(v));
    return b;
  });
  el.append(...btns);
  el.setValue = (v) => {
    for (const b of btns) {
      const on = b.dataset.v === String(v);
      if (role === "tablist") { b.setAttribute("aria-selected", String(on)); b.tabIndex = on ? 0 : -1; }
      else b.setAttribute("aria-pressed", String(on));
    }
  };
  el.buttons = btns;
  return el;
}

export function switchEl(label, on, onChange, { tid, sub, word = true, disabled = false, aria } = {}) {
  const sw = h("span", { class: "switch", role: "switch", tabindex: disabled ? "-1" : "0", "aria-checked": String(!!on), "aria-label": aria || label || null,
                         "aria-disabled": disabled ? "true" : null, tid });
  const w = word ? h("span", { class: "word" }, on ? "On" : "Off") : null;
  sw.append(h("span", { class: "track" }));
  if (w) sw.append(w);
  const set = (v, fire = true) => {
    sw.setAttribute("aria-checked", String(!!v));
    if (w) w.textContent = v ? "On" : "Off";
    if (fire) onChange?.(!!v);
  };
  const toggle = () => { if (sw.getAttribute("aria-disabled") === "true") return; set(sw.getAttribute("aria-checked") !== "true"); };
  sw.addEventListener("click", toggle);
  sw.addEventListener("keydown", (e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); toggle(); } });
  sw.set = (v) => set(v, false);
  sw.get = () => sw.getAttribute("aria-checked") === "true";
  sw.disable = (d) => { sw.setAttribute("aria-disabled", d ? "true" : "false"); sw.tabIndex = d ? -1 : 0; };
  if (!label) return sw;
  const row = h("label", { class: "switch-row" }, h("span", { class: "txt" }, label, sub != null ? h("span", { class: "sub" }, sub) : null), sw);
  row.sw = sw;
  row.addEventListener("click", (e) => { if (e.target === row || e.target.closest(".txt")) { e.preventDefault(); toggle(); } });
  return row;
}

export function select(options, value, onChange, { tid, aria, id, cls = "" } = {}) {
  const s = h("select", { class: `select${cls ? " " + cls : ""}`, tid, "aria-label": aria, id });
  for (const o of options) {
    if (o && o.group) {
      const g = h("optgroup", { label: o.group });
      for (const [v, l] of o.options) g.append(h("option", { value: v }, l));
      s.append(g);
    } else {
      const [v, l] = o;
      s.append(h("option", { value: v }, l));
    }
  }
  if (value != null) s.value = value;
  if (onChange) s.addEventListener("change", () => onChange(s.value));
  return s;
}

export function paintRange(r) {
  const lo = Number(r.min || 0), hi = Number(r.max || 100), v = Number(r.value);
  r.style.setProperty("--p", `${hi > lo ? ((v - lo) / (hi - lo)) * 100 : 0}%`);
}

// A labelled slider: name above, value at the right; onInput(v) as it moves, onCommit(v) on release.
export function slider(name, { min = 0, max = 1, step = 0.01, value = 0, tid, onInput, onCommit, fmt = (v) => Number(v).toFixed(2), glyph = "", title, extra } = {}) {
  const r = h("input", { type: "range", min, max, step, "aria-label": name, tid, title });
  r.value = String(value);
  const val = h("span", { class: "val" }, fmt(value));
  paintRange(r);
  r.addEventListener("input", () => { paintRange(r); val.textContent = fmt(Number(r.value)); onInput?.(Number(r.value)); });
  r.addEventListener("change", () => onCommit?.(Number(r.value)));
  const el = h("div", { class: "slider" }, h("span", { class: "name", title }, name, glyph ? (glyph instanceof Node ? glyph : h("span", { class: "gl" }, glyph)) : null), val,
               h("div", { class: "ctl" }, r, extra || null));
  el.input = r;
  el.set = (v) => { if (document.activeElement === r) return; r.value = String(v); paintRange(r); val.textContent = fmt(Number(v)); };
  return el;
}

export function unroll(title, { hint = "", open = false, tid, onToggle, body, cls = "" } = {}) {
  const d = h("details", { class: `unroll${cls ? " " + cls : ""}`, tid });
  if (open) d.open = true;
  const hintEl = h("span", { class: "hint" }, hint);
  const sum = h("summary", {}, h("span", { class: "st" }, title), hintEl);
  const b = h("div", { class: "body" });
  if (body) put(b, body);
  d.append(sum, b);
  d.addEventListener("toggle", () => onToggle?.(d.open));
  d.body = b;
  d.setHint = (t) => { hintEl.textContent = t || ""; };
  d.setTitle = (t) => { sum.querySelector(".st").textContent = t; };
  return d;
}

export function statusRow(tone, text, { icon: ic, action, tid } = {}) {
  return h("div", { class: `status tone-${tone}`, role: "status", tid }, ic ? icon(ic) : h("span", { class: "dot", style: { marginTop: "7px" } }),
    h("div", { class: "grow" }, text), action || null);
}

export function badge(text, tone = "mute", { tid, onPic = false, dot = true } = {}) {
  return h("span", { class: `badge tone-${tone}${onPic ? " on-pic" : ""}`, tid, dataset: { tone } }, dot ? h("span", { class: "dot" }) : null, text);
}

export function emptyState(text, actions = [], { tid = "empty-state" } = {}) {
  return h("div", { class: "empty", tid }, h("p", {}, text), actions.length ? h("div", { class: "row" }, actions) : null);
}

export const field = (label, ctl) => h("label", { class: "field" }, h("span", { class: "lab" }, label), ctl);
