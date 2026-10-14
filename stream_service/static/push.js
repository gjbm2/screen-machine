// Push a Library item to a real screen for a while, then put back what that screen was doing.
//
// Goes through room_service (it owns the TVs): PUT /api/v1/screens/{id} {desired: {live: {scene, transitions?}}}.
// From art that is a live entry; on a live screen room applies the change in place (a scene change uses the spec's
// `in` transition).  When the time is up (or "End", or the page closes) the screen's previous desired goes back,
// unless someone changed the screen meanwhile.  A scene plays as itself; an effect rides on the scene that is on
// screen (or a moving core scene) as a scene-attached effect; a transition alternates two core scenes through it.
//
// room_service is same-origin: the page comes through the app's /renderer pass-through (the renderer's own /dev
// redirects there).
import { h, put, http, toast, explain } from "./libkit.js";

const ROOM = location.origin;   // room's API is on this page's origin (the app's /renderer/ pass-through)
// the screens room knows (its status), as [id, name]: "north-screen" -> "North"; until it answers, none
export const SCREENS = [];
const friendly = (id) => { const n = id.replace(/-screen$/, "").replace(/[-_]+/g, " "); return n.charAt(0).toUpperCase() + n.slice(1); };
const PUSH_S = 120, MORE_S = 120;
const SCENE_A = { source: "warp_stack" }, SCENE_B = { source: "fractal_zoom" };
const HOLD_S = 4;                                   // a transition push: each side holds this long between runs
const STORE = "sm.push.";                          // + tab id: restore info, in case the page went away without ending
const TAB = Math.random().toString(36).slice(2, 10);
const BEAT_MS = 5000, STALE_MS = 20000;

const pushes = new Map();                           // screen -> {screen, name, label, kind, prev, ours, endsAt, loop}
const nameOf = (id) => (SCREENS.find(([s]) => s === id) || [id, id])[1];
const roomUrl = (p) => `${ROOM}${p}`;
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

async function status() {
  const r = await http("GET", roomUrl("/api/v1/status"));
  return r.ok ? r.data : null;
}
export const ready = (async () => {                 // the screen list, before the Library draws its buttons
  const s = await Promise.race([status(), new Promise((res) => setTimeout(() => res(null), 2500))]);
  for (const [id, sc] of Object.entries(s?.screens || {}).sort()) {      // only the live-capable ones; room's name if it has one
    if (sc?.policy?.live_enabled === false) continue;
    SCREENS.push([id, (sc?.name || friendly(id)).replace(/\s+screen$/i, "")]);
  }
})();
async function setDesired(screen, desired) {
  return http("PUT", roomUrl(`/api/v1/screens/${screen}`), { desired });
}
function unreachable(r) {
  return r.status === 0 || r.status === 403 || (r.error && /origin/i.test(String(r.error) + String(r.detail)));
}
function fail(r, what) {
  if (unreachable(r)) toast("Can't reach the room controls from this address: open the Library from the Room page", "err", 7000);
  else toast(explain(r, what), "err", 5000);
}

// what to put on the screen: the live spec from what it shows now (`cur`), the item, and for a transition the side
function liveSpec(cur, kind, spec, side = 0) {
  const base = cur && cur !== "art" && cur.live ? { ...cur.live } : {};
  delete base.params;
  if (kind === "scene") return { live: { ...base, scene: spec } };
  if (kind === "effect") {
    const on = base.scene || SCENE_A;
    const scene = typeof on === "string" ? { preset: on } : { ...on };
    scene.effects = [spec];
    return { live: { ...base, scene } };
  }
  return { live: { ...base, scene: side ? SCENE_B : SCENE_A, transitions: { ...(base.transitions || {}), in: spec } } };
}

function save() {
  try {
    const list = [...pushes.values()].map(({ screen, prev, ours }) => ({ screen, prev, ours, beat: Date.now() }));
    if (list.length) localStorage.setItem(STORE + TAB, JSON.stringify(list)); else localStorage.removeItem(STORE + TAB);
  } catch (_) {}
}

// ---------------------------------------------------------------- the bar: what is pushed where, time left, +2 min, End
const bar = h("div", { class: "lib-pushbar", role: "status", "aria-live": "polite", hidden: true });
let phases = {};
function renderBar() {
  if (!bar.isConnected) document.body.append(bar);
  bar.hidden = !pushes.size;
  put(bar, [...pushes.values()].map((p) => {
    const left = Math.max(0, Math.round((p.endsAt - Date.now()) / 1000));
    const ph = phases[p.screen];
    return h("div", { class: "lib-push" },
      h("span", { class: "where" }, `📺 ${p.name}`),
      h("span", { class: "what", title: p.label }, p.label),
      h("span", { class: "phase" }, ph || "switching…"),
      h("span", { class: "left" }, `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`),
      h("button", { title: `${MORE_S / 60} more minutes`, onclick: () => { p.endsAt += MORE_S * 1000; renderBar(); } }, `+${MORE_S / 60} min`),
      h("button", { title: "put the screen back now", onclick: () => end(p.screen) }, "End"));
  }));
}
async function pollPhases() {
  if (!pushes.size) return;
  const s = await status();
  if (!s) return;
  phases = {};
  for (const id of pushes.keys()) {
    const o = s.screens?.[id]?.observed || {};
    phases[id] = o.layer === "live" && ["live", "entering"].includes(o.phase) ? "on screen"
      : o.phase === "failed" ? `failed: ${o.detail || ""}` : o.page_connected === false ? "screen page not connected" : "switching…";
  }
  renderBar();
}

// ---------------------------------------------------------------- push / end
export async function push(screen, item, spec) {
  const s = await status();
  if (!s) { fail({ status: 0 }, "room"); return; }
  const cur = s.screens?.[screen]?.desired;
  if (cur === undefined) { toast(`no screen ${screen} in the room`, "err", 4000); return; }
  const was = pushes.get(screen);
  stopLoop(was);
  const prev = was ? was.prev : cur;                 // a push over a push still restores the original
  const r = await setDesired(screen, liveSpec(cur, item.kind, spec));
  if (!r.ok) { fail(r, `${nameOf(screen)}`); if (was) save(); return; }
  const p = { screen, name: nameOf(screen), label: item.label, kind: item.kind, prev, ours: r.data?.desired,
              endsAt: Date.now() + Math.max(PUSH_S * 1000, was ? was.endsAt - Date.now() : 0), loop: null };
  pushes.set(screen, p);
  if (item.kind === "transition") startLoop(p, cur, spec);
  phases[screen] = null;
  save(); renderBar();
  toast(`${item.label} → ${p.name} for ${Math.round((p.endsAt - Date.now()) / 60000)} min`, "", 2200);
}
function startLoop(p, cur, spec) {
  const dur = Number(typeof spec === "object" ? spec.params?.duration_s : NaN) || 1.5;
  let side = 1;
  p.loop = setInterval(async () => {
    if (pushes.get(p.screen) !== p) return stopLoop(p);
    const r = await setDesired(p.screen, liveSpec(cur, "transition", spec, side));
    if (r.ok) { p.ours = r.data?.desired; side ^= 1; save(); }
  }, (dur + HOLD_S) * 1000);
}
function stopLoop(p) { if (p?.loop) { clearInterval(p.loop); p.loop = null; } }

export async function end(screen) {
  const p = pushes.get(screen);
  if (!p) return;
  stopLoop(p);
  pushes.delete(screen);
  save(); renderBar();
  const s = await status();
  const now = s?.screens?.[screen]?.desired;
  if (s && !same(now, p.ours)) { toast(`${p.name} was changed meanwhile: left as it is`, "info", 3500); return; }
  const r = await setDesired(screen, p.prev);
  if (!r.ok) fail(r, `${p.name} back`);
  else toast(`${p.name}: back to ${p.prev === "art" ? "the artwork" : "what it was showing"}`, "", 2200);
}
export const active = () => pushes;

// ---------------------------------------------------------------- clock, page close, leftovers
setInterval(() => {
  if (!pushes.size) return;
  for (const p of [...pushes.values()]) if (Date.now() >= p.endsAt) end(p.screen);
  renderBar();
}, 1000);
setInterval(() => { if (pushes.size) { save(); pollPhases(); } }, BEAT_MS);
window.addEventListener("pagehide", () => {          // the page is going: put the screens back now
  for (const p of pushes.values()) {
    try { fetch(roomUrl(`/api/v1/screens/${p.screen}`), { method: "PUT", keepalive: true, headers: { "Content-Type": "application/json" }, body: JSON.stringify({ desired: p.prev }) }); } catch (_) {}
  }
  pushes.clear();
  try { localStorage.removeItem(STORE + TAB); } catch (_) {}
});
(async () => {                                       // a push whose page vanished without ending it (no heartbeat)
  const stale = [];
  try {
    for (const k of Object.keys(localStorage).filter((k) => k.startsWith(STORE))) {
      let list = [];
      try { list = JSON.parse(localStorage.getItem(k) || "[]"); } catch (_) {}
      if (!list.length || list.every((x) => Date.now() - (x?.beat || 0) > STALE_MS)) { stale.push(...list); localStorage.removeItem(k); }
    }
  } catch (_) {}
  if (!stale.length) return;
  const s = await status();
  for (const x of stale) {
    if (s && same(s.screens?.[x.screen]?.desired, x.ours)) await setDesired(x.screen, x.prev);
  }
})();
