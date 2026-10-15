// room_service, the owner of the screens: its events socket (the full status on connect and on every change, at most
// 4 a second), a 2 s status poll only while the socket is down, and wrappers for the per-screen API.  Root paths on
// the page's own origin (the app's /renderer pass-through), never under BASE.  Without room (the renderer opened on
// its own) /api/v1/* is a 404: the link becomes "missing" and nothing throws.
import { http, wsUrl, emitter, sleep } from "../kit.js";
import { S } from "../store.js";
import { summariseScreen, friendly } from "../words.js";

const ev = emitter();
export const onRoom = (type, fn) => ev.on(type, fn);
let ws = null, backoff = 1000, timer = null, pollT = null, wsUp = false, missing = false;

function setStatus(st) {
  if (!st || typeof st !== "object" || !st.screens) return;
  S.set({ roomStatus: st });
  ev.emit("status", st);
}
function connect() {
  clearTimeout(timer);
  if (ws) { ws.onclose = null; try { ws.close(); } catch (_) {} }
  let sock;
  try { sock = ws = new WebSocket(wsUrl("/api/v1/events")); } catch (_) { down(); return; }
  sock.onopen = () => { wsUp = true; backoff = 1000; missing = false; stopPoll(); S.set({ roomLink: "up" }); };
  sock.onmessage = (e) => {
    let m;
    try { m = JSON.parse(e.data); } catch (_) { return; }
    if (!m) return;
    if (m.type === "status") setStatus(m.data);
    else ev.emit(m.type, m.data);
  };
  sock.onclose = () => { if (ws !== sock) return; down(); };
  sock.onerror = () => {};
}
function down() {
  wsUp = false;
  S.set({ roomLink: missing ? "missing" : "down" });
  startPoll();
  clearTimeout(timer);
  const d = backoff + Math.random() * 400;
  backoff = Math.min(10000, backoff * 2);
  timer = setTimeout(connect, d);
}
function startPoll() {
  if (pollT) return;
  const tick = async () => {
    if (wsUp) { stopPoll(); return; }
    if (document.visibilityState === "visible") {
      const r = await http("GET", "/api/v1/status");
      if (r.ok) setStatus(r.data);
      else if (r.status === 404) { missing = true; S.set({ roomLink: "missing" }); }
    }
    pollT = setTimeout(tick, missing ? 10000 : 2000);
  };
  pollT = setTimeout(tick, 0);
}
function stopPoll() { clearTimeout(pollT); pollT = null; }

export function start() { connect(); }
export function reconnect() { backoff = 1000; connect(); }
export const status = () => http("GET", "/api/v1/status");
export const isUp = () => S.get().roomLink === "up" || !!S.get().roomStatus && S.get().roomLink !== "missing";

// ---------------------------------------------------------------- screens
export function screenOf(id) { return S.get().roomStatus?.screens?.[id] || null; }
export function nameOf(id) { return screenOf(id)?.name || friendly(id); }

/** The live-capable screens grouped by room: [{id, name, real, screens: [id...]}] ("Other" for the rest). */
export function groups(st = S.get().roomStatus) {
  if (!st?.screens) return [];
  const ok = (id) => st.screens[id] && st.screens[id]?.policy?.live_enabled !== false;
  const out = [];
  const seen = new Set();
  for (const r of st.rooms || []) {
    const ids = (r.screens || []).filter(ok);
    ids.forEach((i) => seen.add(i));
    if (ids.length) out.push({ id: r.id, name: r.name || friendly(r.id), screens: ids });
  }
  const rest = Object.keys(st.screens).filter((i) => ok(i) && !seen.has(i)).sort();
  if (rest.length) out.push({ id: "other", name: "Other", screens: rest });
  // a room whose screens aren't the test screen is where people see them
  for (const g of out) g.real = !g.screens.every((i) => /dev|test/i.test(i));
  return out;
}
export const liveScreens = (st = S.get().roomStatus) => groups(st).flatMap((g) => g.screens);
export const isReal = (id) => groups().some((g) => g.real && g.screens.includes(id));
export const isLive = (sc) => !!sc && (sc.observed?.phase === "live" || sc.observed?.layer === "live");
/** live and settled: the per-screen live API (look, fx, inputs) answers instead of 409 not_live.  Room's own test
 *  (live_api.live_session): a session, layer live, desired not art and no handshake; phase "live" is set only once a
 *  cut has finished, so it stands in for "no handshake". */
export const isReady = (sc) => !!sc && sc.observed?.phase === "live" && sc.observed?.layer === "live" && !!sc.observed?.session && sc.desired !== "art";
export const isBusy = (sc) => !!sc && (isLive(sc) || Number.isFinite(sc.desired_expires_in_s) ||
  (sc.desired && typeof sc.desired === "object" && "live" in sc.desired));

export function summary(id, label) { const sc = screenOf(id); return sc ? summariseScreen(sc, label) : { tone: "mute", text: "Not known", live: false }; }

// ---------------------------------------------------------------- per-screen API
export function putDesired(id, desired, forS = null) {
  return http("PUT", `/api/v1/screens/${id}`, forS == null ? { desired } : { desired, for_s: Math.round(forS) });
}
export async function cue(id, body) {
  let r = await http("POST", `/api/v1/screens/${id}/cue`, body);
  if (r.status === 429 && r.error === "too_fast") { await sleep(1000); r = await http("POST", `/api/v1/screens/${id}/cue`, body); }
  return r;
}
export const look = (id) => http("GET", `/api/v1/screens/${id}/look`);
export const patchLook = (id, body) => http("PATCH", `/api/v1/screens/${id}/look`, body);
export const fx = (id) => http("GET", `/api/v1/screens/${id}/fx`);
export const patchLive = (id, body) => http("PATCH", `/api/v1/screens/${id}/live`, body);
export const inputs = (id) => http("GET", `/api/v1/screens/${id}/inputs`);
export const fake = () => http("GET", "/api/v1/dmx/fake");
export const postFake = (body) => http("POST", "/api/v1/dmx/fake", body);
export const artUrl = (id) => `/api/v1/screens/${encodeURIComponent(id)}/art.png`;

// FX bus read-change-write: if_rev, one retry on 409 fx_conflict.  mutate(cur) -> body | null.
const fxLocks = new Map();
export async function putFx(id, mutate) {
  const prev = fxLocks.get(id);
  const run = (async () => {
    if (prev) await prev.catch(() => {});
    for (let attempt = 0; attempt < 2; attempt++) {
      const cur = await fx(id);
      if (!cur.ok) return cur;
      const body = mutate(cur.data);
      if (!body) return { ok: true, status: 204, data: cur.data, skipped: true };
      body.if_rev = cur.data.rev;
      const r = await http("PUT", `/api/v1/screens/${id}/fx`, body);
      if (r.ok || !(r.status === 409 && r.error === "fx_conflict" && attempt === 0)) return r;
    }
    return { ok: false, status: 409, error: "fx_conflict", detail: "" };
  })();
  fxLocks.set(id, run);
  try { return await run; } finally { if (fxLocks.get(id) === run) fxLocks.delete(id); }
}

// GET slot -> PUT item (fxbus.js itemOf)
export function fxItem(s) {
  const o = { id: s.id };
  if (s.preset) o.preset = s.preset;
  else {
    o.effect = s.effect;
    const b = s.bindings && typeof s.bindings === "object" ? s.bindings : {};
    const p = { ...(s.params || {}) };
    for (const k of Object.keys(b)) delete p[k];
    if (Object.keys(p).length) o.params = p;
    if (Object.keys(b).length) o.bindings = b;
  }
  if (s.level != null) o.level = s.level;
  if (s.bypass) o.bypass = true;
  return o;
}
export function masterOut(m) {
  const pick = (v, d) => (v == null ? d : typeof v === "object" ? { bind: v.bind, range: v.range, ...(v.rest != null ? { rest: v.rest } : {}) } : v);
  return { amount: pick(m?.amount, 1), pulse: pick(m?.pulse, 1) };
}
export function sameFx(slot, spec) {
  if (typeof spec === "string") return slot.preset === spec;
  if (spec?.preset) return slot.preset === spec.preset;
  return !slot.preset && spec && slot.effect === spec.effect;
}
