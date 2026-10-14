// Shared helpers for the console's Library and FX bus panels (library.js, fxbus.js).
// Everything here is defensive: the backend may not have the endpoints yet (404), localStorage may throw,
// and window.smDev (the dev.html hook) may be missing when a page embeds these panels on its own.

export const TOKEN_KEY = "sm.stream.token";

// The renderer's base path: "" at :8702/dev, "/renderer" behind the Screen Machine app's pass-through.  Read once
// from this module's own URL (served at <base>/static/libkit.js), else from the page path (<base>/dev).
export const BASE = (() => {
  try { const m = new URL(import.meta.url).pathname.match(/^(.*)\/static\/libkit\.js$/); if (m) return m[1]; } catch (_) {}
  const m = location.pathname.match(/^(.*?)\/dev(\/|$)/);
  return m ? m[1] : "";
})();
// A server path ("/dev/...", "/api/...", "/static/...") under the base; absolute URLs (ws://..., https://...) unchanged.
export const withBase = (p) => (typeof p === "string" && p.startsWith("/") && !p.startsWith("//") && !(BASE && p.startsWith(BASE + "/")) ? BASE + p : p);
export const LIVE_STATES = new Set(["entering", "live"]);          // a cue is accepted
export const FX_STATES = new Set(["starting", "holding", "entering", "live"]);   // PUT /fx is accepted (D.2)
export const STAGES = ["warp", "feedback", "glitch", "stylise", "colour", "light", "projection", "motion", "overlay"];

// ---------------------------------------------------------------- DOM
export function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") e.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(e.style, v);
    else if (k === "dataset") Object.assign(e.dataset, v);
    else if (k.startsWith("on") && typeof v === "function") e.addEventListener(k.slice(2), v);
    else if (k === "text") e.textContent = v;
    else e.setAttribute(k, v === true ? "" : String(v));
  }
  for (const c of kids.flat(Infinity)) if (c != null && c !== false) e.append(c instanceof Node ? c : String(c));
  return e;
}

// replaceChildren / append that skip null, undefined and false (the DOM would print them as "null")
const real = (k) => k.flat(Infinity).filter((x) => x != null && x !== false);
export function put(el, ...kids) { el.replaceChildren(...real(kids)); return el; }
export function add(el, ...kids) { el.append(...real(kids)); return el; }

export const clamp01 = (v) => Math.min(1, Math.max(0, Number(v) || 0));
export const fmt = (v, d = 2) => (v == null || !isFinite(v) ? "–" : Number(v).toFixed(d));

export function debounce(fn, ms) {
  let t = null;
  const d = (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
  d.cancel = () => clearTimeout(t);
  return d;
}

export function hashHue(s, salt = 0) {
  let x = 2166136261 ^ salt;
  for (let i = 0; i < s.length; i++) { x ^= s.charCodeAt(i); x = Math.imul(x, 16777619); }
  return ((x >>> 0) % 360);
}

export function ago(iso) {
  const t = Date.parse(iso);
  if (!isFinite(t)) return "–";
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 90) return `${Math.round(s)} s ago`;
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  if (s < 172800) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

// ---------------------------------------------------------------- storage (per-viewer conveniences only)
export const store = {
  get(key, dflt) {
    try { const v = localStorage.getItem(key); return v == null ? dflt : JSON.parse(v); } catch (_) { return dflt; }
  },
  set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch (_) {} },
};

function token() {
  return "";   // no password (owner, 14 Oct)
}

// ---------------------------------------------------------------- HTTP: never throws; {ok, status, data, error, detail}
export async function http(method, path, body, { auth = true } = {}) {
  const headers = {};
  const tok = auth ? token() : "";
  if (tok) headers.Authorization = `Bearer ${tok}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let r;
  try {
    r = await fetch(withBase(path), { method, headers, body: body !== undefined ? JSON.stringify(body) : undefined, cache: "no-store" });
  } catch (e) {
    return { ok: false, status: 0, data: null, error: "network", detail: e.message };
  }
  const text = await r.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (_) { data = text; }
  const error = r.ok ? null : (data && typeof data === "object" && data.error) || `http_${r.status}`;
  const detail = r.ok ? null : (data && typeof data === "object" && data.detail) || (typeof data === "string" ? data.slice(0, 200) : r.statusText);
  return { ok: r.ok, status: r.status, data, error, detail };
}

// ---------------------------------------------------------------- console context (dev.html's window.smDev)
export const ctx = {
  mock: false,
  mockData: null,
  dev: () => window.smDev || null,
  session() { return this.dev()?.session?.() || null; },
  sid() { return this.session()?.id || null; },
  state() { const s = this.dev()?.state?.(); return s && s !== "-" ? s : null; },
  catalog() { return this.dev()?.catalog?.() || null; },
  features() { return new Set(this.catalog()?.api?.features || []); },
  has(feature) { return this.mock || this.features().has(feature); },
  log(msg, cls = "") { try { this.dev()?.log?.(msg, cls); } catch (_) {} },
  decide(msg, cls = "") { try { this.dev()?.decide?.(msg, cls); } catch (_) {} },
  onEvent(fn) { try { this.dev()?.onEvent?.(fn); } catch (_) {} },
};

// ---------------------------------------------------------------- toasts
let toastBox = null;
export function toast(msg, cls = "", ms = 3200) {
  if (!toastBox) { toastBox = h("div", { class: "lib-toasts", role: "status", "aria-live": "polite" }); document.body.appendChild(toastBox); }
  const t = h("div", { class: `lib-toast ${cls}` }, msg);
  toastBox.appendChild(t);
  while (toastBox.childNodes.length > 4) toastBox.removeChild(toastBox.firstChild);
  setTimeout(() => t.remove(), ms);
}

// A friendly line for an API failure.
export function explain(res, what) {
  if (!res) return `${what} failed`;
  if (res.status === 0) return `${what}: the server is unreachable`;
  if (res.status === 401) return `${what}: refused by the server (401)`;
  if (res.status === 404 && /^http_404|not_found$/.test(res.error || "")) return `${what}: not available on this server yet`;
  return `${what}: ${res.error}${res.detail ? " — " + res.detail : ""}`;
}

// The spec label an item or slot shows.
export function itemLabel(it) { return it?.label || it?.preset || it?.name || it?.effect || "?"; }
