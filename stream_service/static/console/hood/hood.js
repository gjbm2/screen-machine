// Under the hood: why something is slow or broken.  Health in sentences leads; "Looking at" chooses the session for
// Diagnostics, Show inputs and the logs (it never silently picks someone else's).  Sections are unrolls whose open
// state is remembered; every loop runs only while its section is open and the page visible.
import { h, put, store as ls, visibleInterval, reach } from "../kit.js";
import { S } from "../store.js";
import * as room from "../data/room.js";
import * as catalog from "../data/catalog.js";
import { lib } from "../data/library.js";
import { sessions as getSessions, diag as getDiag } from "../data/renderer.js";
import * as preview from "../browse/preview.js";
import { unroll, select } from "../ui/controls.js";
import { friendly } from "../words.js";
import { mountPlugins } from "./plugins.js";
import { mountSessions } from "./sessions.js";
import { mountDiag } from "./diag.js";
import { mountPlayer } from "./player.js";
import { mountReference } from "./reference.js";
import { mountInputs } from "./inputs.js";
import { mountLogs } from "./logs.js";

const OPEN_KEY = "sm.console.hood";
const saved = ls.get(OPEN_KEY, {}) || {};
const openState = { plugins: false, sessions: false, diag: false, player: false, ref: false, inputs: false, logs: false, ...(saved.open || {}) };
export const H = { lookingAt: null, chosen: false, sessions: [], sessionsAt: 0, refSid: null, refArt: null, healthDiag: null, logFilter: saved.logFilter || "this" };
const listeners = new Set();
export const onHood = (fn) => { listeners.add(fn); return () => listeners.delete(fn); };
export const notify = () => listeners.forEach((f) => { try { f(H); } catch (e) { console.error(e); } });
const saveOpen = () => ls.set(OPEN_KEY, { open: openState, logFilter: H.logFilter });

// ---------------------------------------------------------------- who runs a session
export function whoOf(s) {
  const sid = s.session || s.id;
  if (sid && sid === preview.sessionId()) return "your-preview";
  if (sid && sid === H.refSid) return "your-reference";
  if (String(s.screen || "").startsWith("dev-preview-")) return "other-preview";
  const st = S.get().roomStatus;
  if (st?.screens && Object.values(st.screens).some((sc) => sc?.observed?.session === sid)) return "room";
  if (st?.screens && s.screen && st.screens[s.screen]) return "room";       // a room screen's session, running or ended
  return "other";
}
export const WHO_WORDS = { room: "Room", "your-preview": "Your preview", "other-preview": "Preview · other tab", "your-reference": "Your reference", other: "Other" };
export const roomScreenOf = (sid) => !sid ? null : Object.entries(S.get().roomStatus?.screens || {}).find(([, sc]) => sc?.observed?.session === sid)?.[0] || null;

export async function loadSessions() {
  const r = await getSessions();
  if (r.ok) { H.sessions = r.data?.sessions || []; H.sessionsAt = Date.now(); notify(); }
  return r;
}
/** The default "Looking at": the target screen's room session, else your reference, else your preview. */
function defaultLook() {
  const t = S.get().target;
  const ts = room.screenOf(t)?.observed?.session;
  if (ts && room.isLive(room.screenOf(t))) return ts;
  if (H.refSid) return H.refSid;
  return preview.sessionId() || null;
}
export function setLookingAt(sid, chosen = true) {
  H.lookingAt = sid || null; H.chosen = chosen && !!sid;
  notify();
}
export function lookingAt() {
  if (!H.chosen || (H.lookingAt && !candidates().some((c) => c.sid === H.lookingAt))) { const d = defaultLook(); if (d !== H.lookingAt) { H.lookingAt = d; } }
  return H.lookingAt;
}
function candidates() {
  const out = new Map();
  const st = S.get().roomStatus;
  for (const [id, sc] of Object.entries(st?.screens || {})) if (sc?.observed?.session) out.set(sc.observed.session, { sid: sc.observed.session, label: `${sc.name || friendly(id)}, ${room.isLive(sc) ? "live" : sc.observed.phase} (room)` });
  if (H.refSid) out.set(H.refSid, { sid: H.refSid, label: `Your reference session (${H.refSid})` });
  if (preview.sessionId()) out.set(preview.sessionId(), { sid: preview.sessionId(), label: "Your preview" });
  for (const s of H.sessions) {
    if (["ended", "failed"].includes(s.state) || out.has(s.session)) continue;
    const who = whoOf(s);
    out.set(s.session, { sid: s.session, label: `${WHO_WORDS[who]}: ${s.screen} (${s.session})` });
  }
  return [...out.values()];
}

// ---------------------------------------------------------------- mount
let api = null;
export function mount(host) {
  const lookSel = select([], "", (v) => setLookingAt(v), { tid: "looking-at", aria: "Looking at" });
  lookSel.addEventListener("focus", () => loadSessions());
  lookSel.addEventListener("pointerdown", () => loadSessions());
  const healthLine = h("span", { class: "healthline" });
  const sticky = h("div", { class: "hood-sticky" }, h("label", { class: "lookat" }, h("span", { style: { fontWeight: 600, whiteSpace: "nowrap" } }, "Looking at"), lookSel), healthLine);
  const health = unroll("Health", { tid: "hood-health", open: true });
  const sec = {};
  const mk = (k, title) => {
    const u = unroll(title, { tid: `sec-${k}`, open: !!openState[k], onToggle: (o) => { openState[k] = o; saveOpen(); notify(); } });
    sec[k] = u;
    return u;
  };
  const left = h("div", {}, mk("plugins", "Plug-ins"), mk("sessions", "Sessions"), mk("ref", "Reference session"));
  const right = h("div", {}, mk("player", "Player"), mk("diag", "Diagnostics"), mk("inputs", "Show inputs"), mk("logs", "Logs"));
  put(host, h("div", { class: "page" }, sticky, health, h("div", { class: "hood-cols" }, left, right)));
  const isOpen = (k) => () => S.get().mode === "hood" && sec[k].open;
  const ctx = { H, isOpen, lookingAt, setLookingAt, whoOf, loadSessions, notify, onHood, sec, roomScreenOf };
  const player = mountPlayer(sec.player.body, ctx);
  ctx.player = player;
  mountPlugins(sec.plugins.body, ctx);
  mountSessions(sec.sessions.body, ctx);
  mountDiag(sec.diag.body, ctx);
  mountReference(sec.ref.body, ctx);
  mountInputs(sec.inputs.body, ctx);
  const logs = mountLogs(sec.logs.body, ctx);
  api = { logs, player, sec };

  // ---- Looking at
  const drawLook = () => {
    const cur = lookingAt();
    const c = candidates();
    const opts = c.map((x) => [x.sid, x.label]);
    if (!cur) opts.unshift(["", "Nothing is running that's yours"]);
    if (cur && !c.some((x) => x.sid === cur)) opts.unshift([cur, cur]);
    const sig = JSON.stringify(opts);
    if (lookSel._sig !== sig) { put(lookSel, opts.map(([v, l]) => h("option", { value: v }, l))); lookSel._sig = sig; }
    if (document.activeElement !== lookSel) lookSel.value = cur || "";
  };
  onHood(drawLook);
  S.on(["roomStatus", "preview", "target"], () => { drawLook(); drawHealth(); });

  // ---- Health: three sentences and the GPU line
  const drawHealth = () => {
    const s = S.get();
    const lab = lib.lab;
    const n = lab?.plugins?.length || lib.items.length;
    const slow = lib.items.filter((i) => i.slow).length;
    const rt = (lab?.plugins || []).reduce((a, p) => a + (p.runtime_errors?.count ? 1 : 0), 0);
    const broken = (lab?.plugins || []).filter((p) => p.load !== "ok").length;
    const rAns = reach.renderer && Date.now() - reach.renderer < 30000;
    const l1 = rAns ? `Renderer: answering · ${n} plug-ins${slow ? `, ${slow} too slow` : ""}${broken ? `, ${broken} broken` : ""}${rt ? ` · ${rt} with runtime errors (see Plug-ins)` : ""}` : "Renderer: not answering";
    const l2 = s.roomLink === "missing" ? "Room service: not at this address (open the Library from the Room page)" : s.roomLink === "up" ? "Room service: answering · live updates on" : "Room service: not answering";
    const d = H.healthDiag;
    const gpu = d?.gpus?.length ? d.gpus.map((g) => `GPU ${g.index} (${String(g.name).replace(/NVIDIA |GeForce /g, "")}): ${g.util ?? "–"} % busy`).join(" · ") : "GPUs: no reading";
    const t = s.target, ts = room.screenOf(t);
    let l3 = `${room.nameOf(t)}: ${room.isLive(ts) ? "live" : "showing art"}`;
    if (room.isLive(ts) && d) {
      const ss = d.sessions?.find((x) => x.session === ts.observed?.session);
      l3 = `${room.nameOf(t)}: ${!ss ? "live" : ss.window && (ss.window.late_pct ?? 0) > 5 ? `dropping frames (${Math.round(ss.window.late_pct)} % late)` : "running smoothly"}`;
    }
    const mus = S.get().musicMissing ? "There's no lighting input configured in the room." : null;
    put(health.body, h("div", { class: "health" }, h("p", {}, `${l1}.`), h("p", {}, `${l2}.`), h("p", {}, `${l3}.`), mus ? h("p", {}, mus) : null, h("p", { class: "gpu" }, gpu)));
    healthLine.textContent = `${rAns ? "Renderer answering" : "Renderer not answering"} · ${s.roomLink === "up" ? "room answering" : "room not answering"}${d?.gpus?.length ? ` · GPUs ${d.gpus.map((g) => `${g.util ?? "–"} %`).join(" and ")} busy` : ""}`;
  };
  S.on(["labVersion", "libVersion", "roomLink", "rendererLink"], drawHealth);
  visibleInterval(async () => { const r = await getDiag(null); if (r.ok) { H.healthDiag = r.data; drawHealth(); } }, 10000, () => S.get().mode === "hood" && health.open && !sec.diag.open);
  onHood(() => drawHealth());
  drawLook(); drawHealth();
  shown();
}
export async function shown() {
  catalog.ready();
  const r = await getDiag(null);
  if (r.ok) { H.healthDiag = r.data; notify(); }
  loadSessions();
}
export function log(msg, cls = "") { api?.logs?.local(msg, cls); }
export async function attachFromQuery(url, targetMs) {
  api.sec.player.open = true; api.sec.ref.open = true;
  catalog.ready();
  api.player.attach(url, targetMs, null);
}
