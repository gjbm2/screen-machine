// Logs: Decisions (the director's transitions, cues, palette, FX and warnings, coloured) and Events (every renderer
// event as key=JSON), for "This session" (Looking at) or "All sessions"; Errors only; Pause; Clear.  The lines are
// collected from page load by logbuf.js; this section only renders them.
import { h, put } from "../kit.js";
import { seg, switchEl, button } from "../ui/controls.js";
import * as buf from "./logbuf.js";

export function mountLogs(host, ctx) {
  let paused = false, errorsOnly = false, tab = "decisions";
  const tabs = seg([["decisions", "Decisions"], ["events", "Events"]], tab, (v) => { tab = v; tabs.setValue(v); draw(); }, { role: "group", aria: "Log" });
  const filter = h("select", { class: "select sm", tid: "log-filter", "aria-label": "Which sessions" }, h("option", { value: "this" }, "This session"), h("option", { value: "all" }, "All sessions"));
  filter.value = ctx.H.logFilter || "this";
  filter.addEventListener("change", () => { ctx.H.logFilter = filter.value; draw(); });
  const errs = switchEl("Errors only", false, (on) => { errorsOnly = on; draw(); }, { word: false });
  const pause = button("Pause", { kind: "quiet", tid: "log-pause", cls: "btn-sm", onClick: () => { paused = !paused; pause.querySelector(".label").textContent = paused ? "Resume" : "Pause"; if (!paused) draw(); } });
  const clear = button("Clear", { kind: "quiet", tid: "log-clear", cls: "btn-sm", onClick: () => { buf.clear(); draw(); } });
  const dEl = h("div", { class: "log", tid: "log-decisions" });
  const eEl = h("div", { class: "log", tid: "log-events", hidden: true });
  put(host, h("div", { class: "stack" }, h("div", { class: "row" }, tabs, h("div", { style: { width: "160px" } }, filter), errs, pause, clear), dEl, eEl));

  buf.onAppend(() => { if (!paused && ctx.sec.logs.open) drawSoon(); });
  let q = false;
  const drawSoon = () => { if (q) return; q = true; requestAnimationFrame(() => { q = false; draw(); }); };
  function lines(list) {
    const sid = ctx.lookingAt();
    return list.filter((x) => (filter.value === "all" || !x.sid || x.sid === sid) && (!errorsOnly || x.cls === "err"));
  }
  function render(el, list) {
    const atEnd = el.scrollTop + el.clientHeight >= el.scrollHeight - 8;
    put(el, lines(list).map((x) => h("div", { class: x.cls }, `${x.t}  ${x.text}`)));
    if (atEnd) el.scrollTop = el.scrollHeight;
  }
  function draw() {
    if (paused) return;
    dEl.hidden = tab !== "decisions"; eEl.hidden = tab !== "events";
    render(dEl, buf.decisions); render(eEl, buf.events);
  }
  ctx.onHood(() => draw());
  ctx.sec.logs.addEventListener("toggle", () => draw());
  draw();
  return {
    local: (msg, cls = "") => buf.local(msg, cls),
    counts: () => ({ decisions: buf.decisions.length, events: buf.events.length }),
  };
}
