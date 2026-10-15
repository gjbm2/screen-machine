// Shared helpers for the console (the successor to libkit.js): base path, DOM, HTTP that never throws,
// per-device storage that never throws, small timing helpers.
//
// The renderer's paths (/dev/..., /api/..., /static/...) go under BASE: "" when stream_service serves the page itself,
// "/renderer" behind the Screen Machine app's pass-through.  Room's paths (/api/v1/...) are root paths on the page's
// own origin.  No hosts, ports, tokens or auth anywhere.

export const BASE = (() => {
  try {
    const m = new URL(import.meta.url).pathname.match(/^(.*)\/static\/console\/kit\.js$/);
    if (m) return m[1];
  } catch (_) {}
  const m = location.pathname.match(/^(.*?)\/dev(\/|$)/);
  return m ? m[1] : "";
})();

// A renderer path under the base; absolute URLs and room paths stay as they are.
export const withBase = (p) =>
  typeof p === "string" && p.startsWith("/") && !p.startsWith("//") && !p.startsWith("/api/v1/") && !(BASE && p.startsWith(BASE + "/"))
    ? BASE + p : p;

export const wsUrl = (path) => `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${path}`;

// ---------------------------------------------------------------- DOM
export function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") e.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(e.style, v);
    else if (k === "dataset") Object.assign(e.dataset, v);
    else if (k === "tid") e.setAttribute("data-testid", v);
    else if (k === "html") e.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") e.addEventListener(k.slice(2), v);
    else if (k === "text") e.textContent = v;
    else if (k === "value" && (tag === "input" || tag === "select" || tag === "textarea")) e.value = v;
    else e.setAttribute(k, v === true ? "" : String(v));
  }
  for (const c of kids.flat(Infinity)) if (c != null && c !== false) e.append(c instanceof Node ? c : String(c));
  return e;
}
const real = (k) => k.flat(Infinity).filter((x) => x != null && x !== false);
export function put(el, ...kids) { el.replaceChildren(...real(kids)); return el; }
export function add(el, ...kids) { el.append(...real(kids)); return el; }
export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

// ---------------------------------------------------------------- numbers and text
export const clamp01 = (v) => Math.min(1, Math.max(0, Number(v) || 0));
export const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
export const fmt = (v, d = 2) => (v == null || !isFinite(v) ? "–" : Number(v).toFixed(d));
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
export const mmss = (s) => { s = Math.max(0, Math.round(s)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };

export function hashHue(s, salt = 0) {
  let x = 2166136261 ^ salt;
  for (let i = 0; i < String(s).length; i++) { x ^= String(s).charCodeAt(i); x = Math.imul(x, 16777619); }
  return (x >>> 0) % 360;
}

export function ago(iso) {
  const t = typeof iso === "number" ? iso : Date.parse(iso);
  if (!isFinite(t)) return "–";
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 90) return `${Math.round(s)} s ago`;
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  if (s < 172800) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
}

export function deepEqual(a, b) {
  if (a === b) return true;
  if (typeof a !== "object" || typeof b !== "object" || a == null || b == null) return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  const ka = Object.keys(a), kb = Object.keys(b);
  if (ka.length !== kb.length) return false;
  return ka.every((k) => deepEqual(a[k], b[k]));
}

// ---------------------------------------------------------------- timing
export function debounce(fn, ms) {
  let t = null;
  const d = (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
  d.cancel = () => clearTimeout(t);
  d.flush = (...a) => { clearTimeout(t); fn(...a); };
  return d;
}

// Coalesce a stream of updates into at most one request in flight: the latest value wins, sent `ms` after the last
// change (or as soon as the one in flight returns).  send(value) -> Promise.
export function oneInFlight(send, ms = 200) {
  let pending = null, has = false, busy = false, t = null;
  const kick = async () => {
    if (busy || !has) return;
    busy = true;
    const v = pending; has = false; pending = null;
    try { await send(v); } catch (_) {}
    busy = false;
    if (has) t = setTimeout(kick, ms);
  };
  const fn = (v) => { pending = v; has = true; clearTimeout(t); t = setTimeout(kick, ms); };
  fn.cancel = () => { clearTimeout(t); has = false; pending = null; };
  return fn;
}

// setInterval that only fires while the page is visible and isOpen() says so.
export function visibleInterval(fn, ms, isOpen = () => true) {
  let busy = false;
  const id = setInterval(async () => {
    if (busy || document.visibilityState !== "visible" || !isOpen()) return;
    busy = true;
    try { await fn(); } catch (_) {} finally { busy = false; }
  }, ms);
  return () => clearInterval(id);
}

// ---------------------------------------------------------------- per-device storage (never throws)
export const store = {
  get(key, dflt) {
    try { const v = localStorage.getItem(key); return v == null ? dflt : JSON.parse(v); } catch (_) { return dflt; }
  },
  set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch (_) {} },
  del(key) { try { localStorage.removeItem(key); } catch (_) {} },
  has(key) { try { return localStorage.getItem(key) != null; } catch (_) { return false; } },
};

// ---------------------------------------------------------------- HTTP: never throws; {ok, status, data, error, detail}
// Every answer (any status) counts as "the server is answering" for the connection pill.
export const reach = { renderer: 0, room: 0, roomMissing: false, listeners: new Set() };
function noteReach(isRoom, status) {
  if (status > 0) {
    reach[isRoom ? "room" : "renderer"] = Date.now();
    if (isRoom) reach.roomMissing = status === 404 && !reach.roomSeen;
    if (isRoom && status !== 404) reach.roomSeen = true;
  }
  reach.listeners.forEach((f) => { try { f(); } catch (_) {} });
}

export async function http(method, path, body, { keepalive = false, timeoutMs = 20000 } = {}) {
  const isRoom = path.startsWith("/api/v1/");
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let r;
  const ac = typeof AbortController !== "undefined" ? new AbortController() : null;
  const timer = ac ? setTimeout(() => ac.abort(), timeoutMs) : null;
  try {
    r = await fetch(withBase(path), { method, headers, body: body !== undefined ? JSON.stringify(body) : undefined,
                                      cache: "no-store", keepalive, signal: ac?.signal });
  } catch (e) {
    clearTimeout(timer);
    return { ok: false, status: 0, data: null, error: "network", detail: String(e?.message || e) };
  }
  clearTimeout(timer);
  noteReach(isRoom, r.status);
  const text = await r.text().catch(() => "");
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (_) { data = text; }
  const error = r.ok ? null : (data && typeof data === "object" && data.error) || `http_${r.status}`;
  const detail = r.ok ? null : (data && typeof data === "object" && data.detail) || (typeof data === "string" ? data.slice(0, 200) : r.statusText);
  return { ok: r.ok, status: r.status, data, error, detail };
}

export function blobToB64(blob) {
  return new Promise((res, rej) => {
    const fr = new FileReader();
    fr.onload = () => res(String(fr.result).split(",", 2)[1]);
    fr.onerror = rej;
    fr.readAsDataURL(blob);
  });
}

// A tiny event emitter.
export function emitter() {
  const m = new Map();
  return {
    on(type, fn) { if (!m.has(type)) m.set(type, new Set()); m.get(type).add(fn); return () => m.get(type)?.delete(fn); },
    emit(type, ...a) {
      for (const k of [type, "*"]) for (const fn of m.get(k) || []) { try { fn(...a); } catch (e) { console.error(e); } }
    },
  };
}
