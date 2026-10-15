// The logs' buffers (Under the hood ▸ Logs): imported at boot, so the Decisions and Events logs hold everything from
// page load, like the old console did, even though the Under the hood page itself mounts on its first visit.
// DOM-free: lines are {t, sid, text, cls}; the Logs section renders them.
import { onEvent } from "../data/renderer.js";
import { fmt } from "../kit.js";

const MAX_D = 300, MAX_E = 400;
export const decisions = [], events = [];
const subs = new Set();
/** fn() after each new line; returns an unsubscribe. */
export const onAppend = (fn) => { subs.add(fn); return () => subs.delete(fn); };
const tell = () => subs.forEach((f) => { try { f(); } catch (e) { console.error(e); } });
const ts = () => new Date().toISOString().slice(11, 23);

export function decision(ev) {
  if (ev.type === "session.transition" && ev.phase === "started") {
    const cls = ev.cause === "auto" ? "auto" : ev.cause === "go" || ev.cause === "exit" ? "info" : "manual";
    const what = ev.kind === "out" ? `→ artwork via ${ev.transition}` : `scene → ${ev.scene} via ${ev.transition}`;
    return [`${fmt(ev.pts, 2)}s  ${what}  [${ev.cause}${ev.reason ? " · " + ev.reason : ""}]${ev.detail ? "  " + ev.detail : ""}`, cls];
  }
  if (ev.type === "session.cue" && ev.phase !== "scheduled") return [`cue ${ev.cue_id} ${ev.phase}${ev.reason ? " (" + ev.reason + ")" : ""}${ev.why ? " — " + ev.why : ""}: ${ev.scene ?? ""}`, "err"];
  if (ev.type === "session.director") return [`director ${ev.armed ? "armed" : "disarmed"}${ev.enabled ? "" : " (auto off)"}: dwell ${(ev.dwell_s || []).map((x) => Number(x).toFixed(0)).join("–")} s (${ev.dwell_from}); next eligible at ${fmt(ev.eligible_at_pts)} s, max at ${fmt(ev.max_at_pts)} s`, ""];
  if (ev.type === "session.inputs") return [`inputs: lighting desk ${ev.online ? "receiving" : "silent"}, beat ${ev.beat_locked ? "locked" : "unlocked"}`, "info"];
  if (ev.type === "session.palette") return [`colours → ${ev.palette ?? "each scene's own"}`, "info"];
  if (ev.type === "session.fx") return [`fx ${ev.cause} rev ${ev.rev}${(ev.added || []).length ? " +" + ev.added.join(",") : ""}${(ev.removed || []).length ? " −" + ev.removed.join(",") : ""}`, ev.cause === "auto" ? "auto" : ev.cause === "error" ? "err" : "manual"];
  if (ev.type === "session.plugin_error") return [`plug-in ${ev.kind} ${ev.name} ${ev.action || "failed"}: ${ev.detail || ""}`, "err"];
  if (ev.type === "session.warning" || ev.type === "session.error") return [`${ev.type}: ${ev.detail}`, "err"];
  return null;
}

onEvent("*", (ev) => {
  const rest = Object.entries(ev).filter(([k]) => !["type", "session", "screen"].includes(k)).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(" ");
  const err = ev.type === "session.error" || ev.type === "session.plugin_error";
  events.push({ t: ts(), sid: ev.session, text: `${ev.type} ${ev.session ?? ""} ${rest}`, cls: err ? "err" : "" });
  if (events.length > MAX_E) events.shift();
  const d = decision(ev);
  if (d) { decisions.push({ t: ts(), sid: ev.session, text: d[0], cls: d[1] }); if (decisions.length > MAX_D) decisions.shift(); }
  tell();
});

/** A line from the page itself (window.smDev.log). */
export function local(msg, cls = "") {
  events.push({ t: ts(), sid: null, text: msg, cls });
  if (events.length > MAX_E) events.shift();
  tell();
}
export function clear() { decisions.length = 0; events.length = 0; tell(); }
