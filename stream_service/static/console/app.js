// The console shell (design §2, §7.2): header (‹ Room, title, mode switch, target chip, connection pill), the phone's
// tab bar, the Now bar, toasts, the hash router (#browse[/kind/name], #review/kind/name, #on-screen, #hood) and boot.
// Browse mounts at once; On screen and Under the hood on their first visit.
import { h, put, store as ls, reach } from "./kit.js";
import { S } from "./store.js";
import * as renderer from "./data/renderer.js";
import * as room from "./data/room.js";
import * as catalog from "./data/catalog.js";
import { lib, load as loadLibrary, loadLab, startRefresh as libRefresh } from "./data/library.js";
import { load as loadRatings, startRefresh as ratingsRefresh, migrateFavs } from "./data/ratings.js";
import { labelForSpec } from "./data/query.js";
import * as browse from "./browse/browse.js";
import * as review from "./browse/review.js";
import * as preview from "./browse/preview.js";
import * as viewer from "./browse/viewer.js";
import * as keys from "./keys.js";
import * as nowbar from "./onscreen/nowbar.js";
import * as logbuf from "./hood/logbuf.js";          // the logs collect from page load; Under the hood mounts later
import "./onscreen/live.js";                         // so does "What the director did"; On screen mounts later
import { icon } from "./ui/icons.js";
import { seg, button } from "./ui/controls.js";
import { openSheet, openMenu, onRoute, layers, closeLayer } from "./ui/sheet.js";
import { placeToasts } from "./ui/toast.js";


const VERSION = "console-2026.10.15";
const MODES = [["browse", "Browse", "grid", "#browse", "Library"], ["onscreen", "On screen", "tv", "#on-screen", "On screen"], ["hood", "Under the hood", "wrench", "#hood", "Under the hood"]];
const q = new URLSearchParams(location.search);
const $ = (id) => document.getElementById(id);
const mounted = {};
let hood = null, onscreen = null;

// ---------------------------------------------------------------- window.smDev (the verifier's and old panels' hook)
window.smDev = {
  version: VERSION,
  previewClient: preview.LP_CLIENT,
  state: () => S.get(),
  catalog: () => catalog.get(),
  onEvent: (fn) => renderer.onEvent("*", fn),
  log: (msg, cls = "") => logbuf.local(msg, cls),
};

// ---------------------------------------------------------------- ‹ Room (§7.4)
(function roomHref() {
  try {
    const ref = document.referrer;
    if (ref) {
      const u = new URL(ref);
      if (u.origin !== location.origin) ls.set("sm.console.roomHref", `${u.origin}/room`);
      else if (/\/room\/?$/.test(u.pathname)) ls.set("sm.console.roomHref", u.href);
    }
  } catch (_) {}
})();

// ---------------------------------------------------------------- the shell
const hdr = $("hdr"), main = $("main"), tabbar = $("tabbar");
const roomBack = ls.get("sm.console.roomHref", null);
const title = h("h1", {}, "Library");
const modesSeg = seg(MODES.map(([m, l]) => [m, l]), "browse", (m) => setMode(m), { aria: "Mode", role: "tablist" });
modesSeg.classList.add("modes");
modesSeg.buttons.forEach((b, i) => { b.dataset.label = MODES[i][1]; });
const targetText = h("span", { class: "txt" });
const targetDot = h("span", { class: "dot" });
const targetChip = h("button", { type: "button", class: "target", tid: "target-chip", "aria-haspopup": "menu" }, targetDot, targetText, icon("down"));
const connDot = h("span", { class: "dot" });
const connWord = h("span", { class: "word" });
const conn = h("button", { type: "button", class: "conn", tid: "conn-pill" }, connDot, connWord);
hdr.dataset.testid = "app-header";
hdr.className = "hdr";
put(hdr, roomBack ? h("a", { class: "room-back", href: roomBack, tid: "room-back" }, icon("left"), "Room") : null, title, modesSeg, targetChip, conn);
const tabs = MODES.map(([m, l, ic]) => {
  const dot = h("span", { class: "tdot", hidden: true });
  const b = h("button", { type: "button", role: "tab", "aria-selected": String(m === "browse"), dataset: { m } }, icon(ic), h("span", {}, l), dot);
  b.addEventListener("click", () => setMode(m));
  b.dot = dot;
  return b;
});
tabbar.className = "tabbar";
tabbar.setAttribute("role", "tablist");
tabbar.setAttribute("aria-label", "Mode");
tabbar.dataset.testid = "tabbar";
put(tabbar, tabs);
const sections = {};
for (const [m] of MODES) { sections[m] = h("section", { class: "mode", id: `mode-${m}`, hidden: m !== "browse" }); main.append(sections[m]); }

// mode test ids sit on whichever switch is showing (the tab bar on a phone, the header on wider screens)
const phoneMq = window.matchMedia("(max-width: 699px)");
function placeModeIds() {
  const phone = phoneMq.matches;
  MODES.forEach(([m], i) => {
    const tid = `mode-${m === "onscreen" ? "onscreen" : m}`;
    if (phone) { tabs[i].dataset.testid = tid; delete modesSeg.buttons[i].dataset.testid; }
    else { modesSeg.buttons[i].dataset.testid = tid; delete tabs[i].dataset.testid; }
  });
  placeToasts();
}
phoneMq.addEventListener?.("change", placeModeIds);
placeModeIds();

// ---------------------------------------------------------------- modes and the router
export async function setMode(m, { push = false, keepHash = false } = {}) {
  if (!MODES.some(([x]) => x === m)) m = "browse";
  const prev = S.get().mode;
  if (prev === "browse" && m !== "browse") {
    if (review.isOpen()) review.close();
    while (layers.length && layers[layers.length - 1].kind !== "review") { const L = layers[layers.length - 1]; if (L.kind === "viewer" || L.kind === "sheet" || L.kind === "menu") closeLayer(L); else break; }
    if (S.get().sel) preview.release();
  }
  S.set({ mode: m });
  document.body.dataset.mode = m;                    // the Now bar sits over the grid column (not the inspector) in Browse
  for (const [x] of MODES) sections[x].hidden = x !== m;
  modesSeg.setValue(m);
  tabs.forEach((b) => b.setAttribute("aria-selected", String(b.dataset.m === m)));
  title.textContent = phoneMq.matches ? MODES.find(([x]) => x === m)[4] : "Library";
  if (!keepHash) {
    const hash = MODES.find(([x]) => x === m)[3];
    try { history[push ? "pushState" : "replaceState"](history.state, "", hash); } catch (_) {}
  }
  if (m === "onscreen" && !mounted.onscreen) { mounted.onscreen = true; onscreen = await import("./onscreen/onscreen.js"); onscreen.mount(sections.onscreen, { openPicker: pickerProxy }); }
  if (m === "hood" && !mounted.hood) { mounted.hood = true; hood = await import("./hood/hood.js"); hood.mount(sections.hood); }
  if (m !== "browse") catalog.ready();
  if (m === "onscreen") onscreen?.shown?.();
  if (m === "hood") hood?.shown?.();
  if (m === "browse" && prev !== "browse") {
    const sel = S.get().sel;
    const it = sel && lib.byKey.get(sel);
    if (it && browse.isDesktop()) viewer.show(it, { keepVariant: true });
    window.scrollTo({ top: 0 });
  }
  if (prev !== m) window.scrollTo({ top: 0 });
}
async function pickerProxy(kind, opts) { const m = await import("./onscreen/picker.js"); return m.openPicker(kind, opts); }
window.smDev.setMode = setMode;

function parseHash() {
  const hs = decodeURIComponent(location.hash.replace(/^#/, ""));
  const [a, kind, ...rest] = hs.split("/");
  const name = rest.join("/");
  if (a === "on-screen") return { mode: "onscreen" };
  if (a === "hood") return { mode: "hood" };
  if (a === "review" && kind && name) return { mode: "browse", key: `${kind}/${name}`, review: true };
  if (a === "browse" && kind && name) return { mode: "browse", key: `${kind}/${name}` };
  return { mode: "browse" };
}
onRoute(() => { const r = parseHash(); setMode(r.mode, { keepHash: true }); });

// ---------------------------------------------------------------- target chip
function drawTarget() {
  const t = S.get().target;
  const sum = room.summary(t, (s) => labelForSpec(s, lib.items, lib.pidx));
  targetText.textContent = `${room.nameOf(t)} · ${sum.text}`;
  targetChip.className = `target tone-${sum.tone}`;
  targetChip.setAttribute("aria-label", `Target screen: ${room.nameOf(t)}, ${sum.text}. Change`);
  const liveDot = tabs[1].dot;
  liveDot.hidden = !sum.live;
  liveDot.className = "tdot live";
}
targetChip.addEventListener("click", () => {
  const items = [];
  for (const g of room.groups()) {
    items.push({ header: g.real ? `${g.name} · real TVs` : g.name });
    for (const id of g.screens) {
      const sum = room.summary(id, (s) => labelForSpec(s, lib.items, lib.pidx));
      items.push({ label: room.nameOf(id), sub: sum.text, tid: `target-${id}`, checked: null, onClick: () => S.set({ target: id }) });
    }
  }
  if (!items.length) items.push({ header: "The room service isn't reachable from here" });
  openMenu(targetChip, items, { tid: "target-menu" });
});
S.on(["roomStatus", "target", "libVersion"], drawTarget);

// ---------------------------------------------------------------- connection pill
function connState() {
  const s = S.get();
  const r = s.rendererLink === "up", m = s.roomLink === "up";
  const now = Date.now();
  if (now - Math.max(reach.renderer, reach.room) > 15000 && !r && !m) return ["Offline", "bad"];
  if (r && m) return ["Connected", "ok"];
  if (r || m) return ["Partly connected", "warn"];
  return ["Reconnecting", "warn"];
}
function drawConn() {
  const [w, tone] = connState();
  connWord.textContent = w;
  conn.className = `conn tone-${tone}`;
  conn.setAttribute("aria-label", `Connection: ${w}`);
}
S.on(["rendererLink", "roomLink"], drawConn);
setInterval(drawConn, 5000);
conn.addEventListener("click", () => {
  const body = h("div", { class: "stack" });
  const draw = () => {
    const s = S.get();
    const lines = [];
    lines.push(reach.renderer && Date.now() - reach.renderer < 30000 ? "The renderer is answering." : "The renderer isn't answering: previews and the Library won't load until it's back.");
    lines.push(s.rendererLink === "up" ? "Live updates from the renderer are on." : "Live updates from the renderer are off; retrying.");
    if (s.roomLink === "missing") lines.push("There's no room service at this address: going live and Show on need the Library opened from the Room page.");
    else lines.push(s.roomLink === "up" ? "The room service is answering, with live updates." : "The room service isn't answering: going live and Show on won't work until it's back.");
    put(body, lines.map((l) => h("p", {}, l)));
  };
  draw();
  const off = S.on(["rendererLink", "roomLink"], draw);
  const retry = button("Retry now", { kind: "primary", tid: "conn-retry", onClick: () => { renderer.reconnect(); room.reconnect(); room.status(); loadLibrary("retry"); } });
  openSheet({ id: "conn", title: "Connection", body, foot: retry, tid: "conn-sheet", onClose: off });
});

// ---------------------------------------------------------------- the hood's amber dot: plug-in runtime errors
S.on(["labVersion"], () => {
  const n = (lib.lab?.plugins || []).reduce((a, p) => a + (p.runtime_errors?.count || 0) + (p.load !== "ok" ? 1 : 0), 0);
  tabs[2].dot.hidden = !n;
  tabs[2].dot.className = "tdot warn";
});

// ---------------------------------------------------------------- boot
if (q.has("mock")) console.info("?mock=1 is retired: the console always shows the real Library.");
if (q.get("screen")) S.set({ target: q.get("screen") });
document.body.dataset.mode = "browse";
browse.mount(sections.browse);
nowbar.mount($("nowbar"), { openScreen: (id) => { S.set({ target: id }); setMode("onscreen"); } });
keys.init({ setMode });
renderer.start();
room.start();
drawTarget(); drawConn();

const att = q.get("attach");
const route0 = att ? { mode: "hood" } : parseHash();
if (att) {
  S.set({ mode: "hood" });
  setMode("hood").then(() => hood.attachFromQuery(att, parseInt(q.get("target_ms") || "300", 10)));
} else if (route0.mode !== "browse") setMode(route0.mode);
else { try { history.replaceState(null, "", location.hash && location.hash.startsWith("#browse") ? location.hash : "#browse"); } catch (_) {} }

(async () => {
  const [okLib] = await Promise.all([loadLibrary(), loadRatings()]);
  libRefresh(); ratingsRefresh(); loadLab();
  if (!okLib) return;
  if (route0.key && route0.mode === "browse") {
    try { history.replaceState(null, "", "#browse"); } catch (_) {}
    const it = lib.byKey.get(route0.key);
    if (it) {
      if (S.get().kind !== it.kind) S.set({ kind: it.kind });
      await Promise.resolve();
      browse.select(it.key, { open: true });
      if (route0.review && browse.isDesktop()) setTimeout(() => browse.openReview(), 50);
    }
  }
  migrateFavs();
})();
