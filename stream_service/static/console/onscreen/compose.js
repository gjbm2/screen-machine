// The Next-change state, one for the whole page (Browse strip, On screen, the Picker's footer): the transition to use
// (or the room's choice), up to 4 effects attached to the next scene, and the timing (on the beat by default).
import { h, put } from "../kit.js";
import { S } from "../store.js";
import { chip, select } from "../ui/controls.js";
import { nameOf } from "../data/room.js";
import { toast } from "../ui/toast.js";

export const MAX_FX = 4;
const C = () => S.get().compose;
export function setVia(v) { S.set({ compose: { ...C(), via: v ? { key: v.key, spec: v.spec, label: v.label } : null } }); }
export function setAt(at) { S.set({ compose: { ...C(), at: at === "now" ? "now" : "beat" } }); }
export function hasFx(key) { return C().fx.some((e) => e.key === key); }
export function toggleFx(e) {
  const fx = [...C().fx];
  const i = fx.findIndex((x) => x.key === e.key);
  if (i >= 0) fx.splice(i, 1);
  else if (fx.length >= MAX_FX) { toast(`A scene takes at most ${MAX_FX} effects`, { tone: "warn" }); return false; }
  else fx.push({ key: e.key, spec: e.spec, label: e.label });
  S.set({ compose: { ...C(), fx } });
  return true;
}

/** The `to` of a cue: the scene spec, or {preset|source, params?, effects} when effects are attached. */
export function composeTo(spec) {
  const fx = C().fx;
  if (!fx.length) return spec;
  const effects = fx.map((e) => e.spec);
  return typeof spec === "string" ? { preset: spec, effects } : { ...spec, effects };
}
/** The body for room's POST /api/v1/screens/{id}/cue. */
export function cueBody(spec) {
  const c = C();
  const body = { to: composeTo(spec), at: c.at };
  if (c.via) body.transition = c.via.spec;
  return body;
}
export const atWords = (at) => (at === "now" ? "Now" : "On the beat");

/** Render the one-line strip into el. */
export function renderStrip(el, { target, tid = "next-strip", showName = true, label = true } = {}) {
  const c = C();
  const via = c.via
    ? chip(`via ${c.via.label}`, { removable: true, removeLabel: "Use the room's choice of transition", onRemove: () => setVia(null), cls: "on", data: { role: "via" } })
    : h("span", { class: "muted" }, "via the room's choice");
  const fx = c.fx.map((e) => chip(`+ ${e.label}`, { removable: true, removeLabel: `Detach ${e.label}`, onRemove: () => toggleFx(e), data: { role: "fx", key: e.key } }));
  const at = select([["beat", "On the beat"], ["now", "Now"]], c.at, (v) => setAt(v), { aria: "When the next change happens", cls: "sm" });
  put(el, label ? h("span", { class: "lab" }, showName && target ? `Next change on ${nameOf(target).replace(/ screen$/i, "")}:` : "Next change:") : null, via, fx, at);
  el.dataset.testid = tid;
  return el;
}
