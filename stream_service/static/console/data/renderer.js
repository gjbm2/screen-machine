// The renderer (stream_service, under BASE): its events socket (backoff 1-2-4-8-10 s with jitter), fan-out by event
// type, and a few API helpers.  Only the tab's own preview and reference sessions are driven from here; room-owned
// screens go through room.js.
import { BASE, wsUrl, http, emitter } from "../kit.js";
import { S } from "../store.js";

const ev = emitter();
export const onEvent = (type, fn) => ev.on(type, fn);
let ws = null, backoff = 1000, timer = null, opened = 0;

function connect() {
  clearTimeout(timer);
  if (ws) { ws.onclose = null; try { ws.close(); } catch (_) {} }
  let sock;
  try { sock = ws = new WebSocket(wsUrl(`${BASE}/api/events`)); } catch (_) { schedule(); return; }
  sock.onopen = () => { backoff = 1000; opened = Date.now(); S.set({ rendererLink: "up" }); };
  sock.onmessage = (e) => {
    let m;
    try { m = JSON.parse(e.data); } catch (_) { return; }
    if (m && m.type) ev.emit(m.type, m);
  };
  sock.onclose = () => { if (ws !== sock) return; S.set({ rendererLink: "down" }); schedule(); };
  sock.onerror = () => {};
}
function schedule() {
  clearTimeout(timer);
  const d = backoff + Math.random() * 400;
  backoff = Math.min(10000, backoff * 2);
  timer = setTimeout(connect, d);
}
export function start() { connect(); }
export function reconnect() { backoff = 1000; connect(); }
export const isOpen = () => ws && ws.readyState === 1;

export const api = (method, path, body, opts) => http(method, path, body, opts);
export const sessions = () => http("GET", "/api/sessions");
export const session = (sid) => http("GET", `/api/sessions/${sid}`);
export const diag = (sid) => http("GET", `/api/diag${sid ? "?session=" + encodeURIComponent(sid) : ""}`);
export const inputs = (sid) => http("GET", `/api/sessions/${sid}/inputs`);
