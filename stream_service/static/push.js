// Push a Library item to a real screen for a while, then put back what that screen was doing.
//
// Goes through room_service (it owns the TVs): PUT /api/v1/screens/{id} {desired: {live: {scene, transitions?}},
// for_s}.  From art that is a live entry; on a live screen room applies the change in place (a scene change uses the
// spec's `in` transition).  Room owns the clock: at for_s it restores what the screen was doing before, whether or not
// this page is still open; every PUT here carries for_s (one without it would cancel the timer).  "End" puts the
// previous value back now; "+2 min" re-sends with a longer for_s.  The bar shows every timed screen room reports
// (status desired_expires_in_s), so a push started elsewhere, or before a reload, shows up too.
//
// A scene plays as itself; an effect rides on the scene that is on screen (or a moving core scene) as a scene-attached
// effect; a transition alternates two core scenes through it.
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
const POLL_MS = 4000;

const pushes = new Map();                           // screen -> {screen, name, label, prev, ours, endsAt, loop}
const nameOf = (id) => (SCREENS.find(([s]) => s === id) || [id, friendly(id)])[1];
const roomUrl = (p) => `${ROOM}${p}`;
const leftS = (p) => Math.max(1, Math.round((p.endsAt - Date.now()) / 1000));

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
  if (s) sync(s);
})();
// forS: room restores the previous desired after this many seconds; null = no timer (and cancels a running one)
async function setDesired(screen, desired, forS = null) {
  return http("PUT", roomUrl(`/api/v1/screens/${screen}`), forS == null ? { desired } : { desired, for_s: Math.round(forS) });
}
function unreachable(r) {
  return r.status === 0 || r.status === 403 || (r.error && /origin/i.test(String(r.error) + String(r.detail)));
}
function fail(r, what) {
  if (unreachable(r)) toast("Can't reach the room controls from this address: open the Library from the Room page", "err", 7000);
  else toast(explain(r, what), "err", 5000);
}
const expiresAt = (data, fallbackS) => Date.now() + 1000 * (Number.isFinite(data?.desired_expires_in_s) ? data.desired_expires_in_s : fallbackS);

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

// ---------------------------------------------------------------- the bar: what is pushed where, time left, +2 min, End
const bar = h("div", { class: "lib-pushbar", role: "status", "aria-live": "polite", hidden: true });
let phases = {};
function renderBar() {
  if (!bar.isConnected) document.body.append(bar);
  bar.hidden = !pushes.size;
  put(bar, [...pushes.values()].map((p) => {
    const left = Math.max(0, Math.round((p.endsAt - Date.now()) / 1000));
    return h("div", { class: "lib-push" },
      h("span", { class: "where" }, `📺 ${p.name}`),
      h("span", { class: "what", title: p.label }, p.label),
      h("span", { class: "phase" }, phases[p.screen] || "switching…"),
      h("span", { class: "left" }, `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`),
      h("button", { title: `${MORE_S / 60} more minutes`, onclick: () => more(p.screen) }, `+${MORE_S / 60} min`),
      h("button", { title: "put the screen back now", onclick: () => end(p.screen) }, "End"));
  }));
}
// room's view: timed screens (desired_expires_in_s) are pushes; one that is no longer timed has ended (expired,
// ended elsewhere, or changed by hand)
function sync(s) {
  phases = {};
  for (const [id, sc] of Object.entries(s.screens || {})) {
    const left = sc?.desired_expires_in_s;
    let p = pushes.get(id);
    if (!Number.isFinite(left) || left <= 0) {
      if (p) { stopLoop(p); pushes.delete(id); }
      continue;
    }
    if (!p) {                                       // pushed from elsewhere, or before this page loaded
      p = { screen: id, name: nameOf(id), label: liveLabel(sc.desired), prev: null, ours: sc.desired, loop: null };
      pushes.set(id, p);
    }
    p.endsAt = Date.now() + left * 1000;
    const o = sc.observed || {};
    phases[id] = o.layer === "live" && ["live", "entering"].includes(o.phase) ? "on screen"
      : o.phase === "failed" ? `failed: ${o.detail || ""}` : "switching…";
  }
  renderBar();
}
function liveLabel(d) {
  const sc = d?.live?.scene;
  return typeof sc === "string" ? sc : sc?.preset || sc?.source || "live";
}
async function poll() {
  if (!pushes.size) return;
  const s = await status();
  if (s) sync(s);
}

// ---------------------------------------------------------------- push / more / end
export async function push(screen, item, spec) {
  const s = await status();
  if (!s) { fail({ status: 0 }, "room"); return; }
  const cur = s.screens?.[screen]?.desired;
  if (cur === undefined) { toast(`no screen ${screen} in the room`, "err", 4000); return; }
  const was = pushes.get(screen);
  stopLoop(was);
  const forS = Math.max(PUSH_S, was ? leftS(was) : 0);
  const r = await setDesired(screen, liveSpec(cur, item.kind, spec), forS);   // over a push, room keeps the original to restore
  if (!r.ok) { fail(r, `${nameOf(screen)}`); return; }
  const p = { screen, name: nameOf(screen), label: item.label, prev: was ? was.prev : cur, ours: r.data?.desired,
              endsAt: expiresAt(r.data, forS), loop: null };
  pushes.set(screen, p);
  if (item.kind === "transition") startLoop(p, cur, spec);
  phases[screen] = null;
  renderBar();
  toast(`${item.label} → ${p.name} for ${Math.round(forS / 60)} min`, "", 2200);
}
function startLoop(p, cur, spec) {
  const dur = Number(typeof spec === "object" ? spec.params?.duration_s : NaN) || 1.5;
  let side = 1;
  p.loop = setInterval(async () => {
    if (pushes.get(p.screen) !== p) return stopLoop(p);
    const r = await setDesired(p.screen, liveSpec(cur, "transition", spec, side), leftS(p));   // keeps room's timer
    if (r.ok) { p.ours = r.data?.desired; side ^= 1; }
  }, (dur + HOLD_S) * 1000);
}
function stopLoop(p) { if (p?.loop) { clearInterval(p.loop); p.loop = null; } }

async function more(screen) {
  const p = pushes.get(screen);
  if (!p) return;
  const s = await status();
  const now = s?.screens?.[screen]?.desired ?? p.ours;
  const forS = leftS(p) + MORE_S;
  const r = await setDesired(screen, now, forS);
  if (!r.ok) { fail(r, `${p.name}: more time`); return; }
  p.endsAt = expiresAt(r.data, forS);
  renderBar();
}

export async function end(screen) {
  const p = pushes.get(screen);
  if (!p) return;
  stopLoop(p);
  pushes.delete(screen);
  renderBar();
  const s = await status();
  const sc = s?.screens?.[screen];
  if (!sc || !Number.isFinite(sc.desired_expires_in_s)) return;        // already over (expired or ended elsewhere)
  const back = sc.desired_expires_to !== undefined ? sc.desired_expires_to : p.prev;   // room's restore target first
  if (back === undefined) { toast(`${p.name}: room didn't say what to restore; it ends at its timer`, "info", 3500); return; }
  const r = await setDesired(screen, back);                            // no for_s: cancels room's timer, restores now
  if (!r.ok) fail(r, `${p.name} back`);
  else toast(`${p.name}: back to ${back === "art" ? "the artwork" : "what it was showing"}`, "", 2200);
}
export const active = () => pushes;

// ---------------------------------------------------------------- clock
setInterval(() => {
  if (!pushes.size) return;
  for (const p of [...pushes.values()]) if (Date.now() >= p.endsAt + 3000) { stopLoop(p); pushes.delete(p.screen); }   // room restores it
  renderBar();
}, 1000);
setInterval(poll, POLL_MS);
