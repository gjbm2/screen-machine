// Sessions: every renderer session, running ones by default ("Show ended" for the rest), with who runs each in
// words, its state, picture, frames, late % and render p95.  Row actions (running ones): Watch (in the Player) and Look at this.
// 0.5 Hz only while open and visible.
import { h, put, http, visibleInterval, fmt } from "../kit.js";
import { button, switchEl } from "../ui/controls.js";
import { sessionWords } from "../words.js";
import { WHO_WORDS } from "./hood.js";

// how an ended session ended, in its State cell (it has nothing to Watch or Look at)
const END_WORDS = { deleted: "Stopped", ttl: "Stopped (timed out)", error: "Failed" };
const over = (s) => ["ended", "failed"].includes(s.state);
const stateWords = (s) => !over(s) ? sessionWords(s.state)
  : s.error ? `Failed: ${String(s.error).split("\n")[0].slice(0, 80)}` : END_WORDS[s.end_reason] || sessionWords(s.state);

export function mountSessions(host, ctx) {
  let showEnded = false;
  const ended = switchEl("Show ended", false, (on) => { showEnded = on; draw(); }, { tid: "sessions-show-ended", word: false });
  const table = h("div", { tid: "sessions-table" });
  put(host, h("div", { class: "stack" }, ended, table));
  // the table needs room (it shares a half-width column on desktop); narrower, each session is a two-line row
  const wide = () => (host.clientWidth || 0) >= 720;
  let wasWide = null;
  try { new ResizeObserver(() => { if (ctx.sec.sessions.open && wide() !== wasWide) draw(); }).observe(host); } catch (_) {}
  function draw() {
    const rows = ctx.H.sessions.filter((s) => showEnded || !["ended", "failed"].includes(s.state));
    if (!rows.length) { put(table, h("p", { class: "hint" }, ctx.H.sessionsAt ? "No sessions running." : "Loading…")); return; }
    const acts = (s) => over(s) ? [] : [
      button("Watch", { kind: "quiet", cls: "btn-sm", onClick: async () => {
        const r = await http("GET", `/api/sessions/${s.session}`);
        if (r.ok && r.data?.attach?.url) { ctx.player.attach(r.data.attach.url, r.data.attach.target_buffer_ms ?? 300, null, { sid: s.session }); ctx.sec.player.open = true; }
      } }),
      button("Look at this", { kind: "quiet", cls: "btn-sm", onClick: () => ctx.setLookingAt(s.session) }),
    ];
    const pic = (s) => `${s.profile || "–"} · GPU ${s.gpu ?? "–"}`;
    const late = (s) => (s.frames ? `${fmt((100 * (s.late_frames || 0)) / s.frames, 1)} %` : "–");
    wasWide = wide();
    if (wasWide) {
      put(table, h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" },
        h("thead", {}, h("tr", {}, ["Screen", "Who", "State", "Picture", "Frames", "Late", "Render p95", ""].map((x) => h("th", {}, x)))),
        h("tbody", {}, rows.map((s) => {
          const who = ctx.whoOf(s);
          return h("tr", { tid: "session-row", dataset: { sid: s.session, who } },
            h("td", {}, s.screen, h("div", { class: "meta mono" }, s.session)), h("td", {}, WHO_WORDS[who]), h("td", { style: { whiteSpace: over(s) ? "normal" : "nowrap" } }, stateWords(s)),
            h("td", {}, pic(s)), h("td", { class: "num" }, String(s.frames ?? "–")), h("td", { class: "num" }, late(s)), h("td", { class: "num" }, `${fmt(s.render_ms?.p95, 1)} ms`),
            h("td", {}, h("div", { class: "row", style: { flexWrap: "nowrap" } }, acts(s))));
        })))));
    } else {
      put(table, rows.map((s) => {
        const who = ctx.whoOf(s);
        return h("div", { class: "srow", tid: "session-row", dataset: { sid: s.session, who } },
          h("div", { class: "l1" }, h("strong", {}, s.screen), h("span", {}, WHO_WORDS[who]), h("span", { class: "muted" }, stateWords(s))),
          h("div", { class: "l2" }, `${pic(s)} · ${s.frames ?? "–"} frames · ${late(s)} late · render p95 ${fmt(s.render_ms?.p95, 1)} ms`),
          over(s) ? null : h("div", { class: "row" }, acts(s)));
      }));
    }
  }
  ctx.onHood(() => { if (ctx.sec.sessions.open) draw(); });
  visibleInterval(() => ctx.loadSessions(), 2000, ctx.isOpen("sessions"));
  ctx.sec.sessions.addEventListener("toggle", () => { if (ctx.sec.sessions.open) { ctx.loadSessions(); draw(); } });
  draw();
}
