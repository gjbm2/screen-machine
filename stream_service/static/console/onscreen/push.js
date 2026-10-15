// "Show on a screen…": put a Library item on a room screen for 2 / 10 / 30 minutes, then room puts back what was there
// (PUT /api/v1/screens/{id} {desired, for_s}; room owns the clock, so a closed tab never leaves a TV live).  A scene
// plays as itself; an effect rides on what the screen shows (or a moving core scene); a transition loops two core
// scenes through it while this page is open.  Real TVs are named rows in a sheet grouped by room, with what each is
// doing; a busy screen's button says "Replace on …".  No dialogs, holds or tokens.
import { h, put, mmss, deepEqual } from "../kit.js";
import { S } from "../store.js";
import * as room from "../data/room.js";
import * as catalog from "../data/catalog.js";
import { lib } from "../data/library.js";
import { labelForSpec, itemForSpec } from "../data/query.js";
import { onEvent } from "../data/renderer.js";
import { openSheet } from "../ui/sheet.js";
import { button, chip, select, busy } from "../ui/controls.js";
import { toast, toastErr } from "../ui/toast.js";
import { errorWords, desiredScene, friendly, pluginErrorWords } from "../words.js";

const SCENE_A = { source: "warp_stack" }, SCENE_B = { source: "fractal_zoom" };
const HOLD_S = 4;
export const DURS = [[120, "2 min"], [600, "10 min"], [1800, "30 min"]];
export const pushes = new Map();     // screen -> {screen, label, kind, spec, item, prev, ours, loop, pushedAt, liveAt, warn}

// ---------------------------------------------------------------- labels
export function sceneLabel(spec) {
  if (spec == null) return "";
  if (typeof spec === "object" && Array.isArray(spec.effects) && spec.effects.length) {
    const fx = spec.effects.map((e) => labelForSpec(e, lib.items, lib.pidx, "effect")).join(" + ");
    const { effects, ...rest } = spec;
    return `${fx} on ${labelForSpec(rest.preset || rest.source ? rest : spec, lib.items, lib.pidx, "scene")}`;
  }
  return labelForSpec(spec, lib.items, lib.pidx, "scene");
}
/** What a screen's desired value shows, in words ("Circuits", "Burn (looping)"). */
export function desiredLabel(id, desired) {
  const p = pushes.get(id);
  if (p && p.ours && deepEqual(stripLoop(p.ours), stripLoop(desired))) return p.kind === "transition" ? `${p.label} (looping)` : p.label;
  const sc = desiredScene(desired);
  return sc ? sceneLabel(sc) : "";
}
const stripLoop = (d) => (d && typeof d === "object" && d.live ? { ...d, live: { ...d.live, scene: null } } : d);
export const thumbFor = (id) => {
  const sc = room.screenOf(id);
  if (!sc) return null;
  if (room.isLive(sc) || (sc.desired && sc.desired !== "art")) {
    const it = itemForSpec(desiredScene(sc.desired), lib.items, lib.pidx);
    return it?.thumb || null;
  }
  return room.artUrl(id);
};

// ---------------------------------------------------------------- the busy rule
export function busyNote(id) {
  const sc = room.screenOf(id);
  if (!sc) return null;
  const left = Number.isFinite(sc.desired_expires_in_s) ? sc.desired_expires_in_s : null;
  if (left != null) {
    const what = desiredLabel(id, sc.desired) || pushes.get(id)?.label;
    return what ? `Busy: ${what} from the Library, ${mmss(left)} left` : `Busy: a timed change, ${mmss(left)} left`;
  }
  if (room.isLive(sc) || (sc.desired && typeof sc.desired === "object")) {
    const mode = S.get().roomStatus?.mode?.current;
    const what = desiredLabel(id, sc.desired);
    return `Busy: live${mode ? ` in ${friendly(mode)} mode` : ""}${what ? ` · ${what}` : ""}`;
  }
  return null;
}

// ---------------------------------------------------------------- what to put on the screen
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
async function effectSpec(it, spec) {
  await catalog.ready();
  const props = catalog.propsFor("effect", it.plugin);
  if (!("amount" in props)) return spec;
  const obj = typeof spec === "string" ? { preset: spec } : { ...spec };
  obj.params = { amount: 1, ...(obj.params || {}) };
  return obj;
}

export async function push(id, it, spec, forS = 120) {
  const st = await room.status();
  if (!st.ok) { toastErr(errorWords(st, { target: "the room service" })); return false; }
  S.set({ roomStatus: st.data });
  const sc = st.data.screens?.[id];
  if (!sc) { toastErr(`There's no screen called ${id}`); return false; }
  const cur = sc.desired;
  const was = pushes.get(id);
  stopLoop(was);
  if (it.kind === "effect") spec = await effectSpec(it, spec);
  const desired = liveSpec(cur, it.kind, spec);
  const onScene = desiredScene(cur) ? stripFx(desiredScene(cur)) : SCENE_A;
  const label = it.kind === "effect" ? `${it._specLabel || it.title} on ${sceneLabel(onScene) || "Warp stack"}` : (it._specLabel || it.title);
  // known before the PUT, so the Now bar names it from the first status room sends (which can beat the PUT's answer)
  const p = { screen: id, label, kind: it.kind, spec, item: it, sent: false,
              prev: was ? was.prev : cur, ours: desired, loop: null, pushedAt: Date.now(), liveAt: null, warn: null };
  pushes.set(id, p);
  const r = await room.putDesired(id, desired, forS);
  if (!r.ok) {
    if (pushes.get(id) === p) { if (was) pushes.set(id, was); else pushes.delete(id); }
    toastErr(errorWords(r, { screen: room.nameOf(id), target: "the room service" }));
    return false;
  }
  p.ours = r.data?.desired ?? desired;
  p.sent = true;
  if (it.kind === "transition") startLoop(p, cur, spec);
  toast(`${p.label} → ${room.nameOf(id)} for ${Math.round(forS / 60)} min`);
  S.set({ pushVersion: (S.get().pushVersion || 0) + 1 });
  return true;
}
const stripFx = (s) => (typeof s === "object" && s ? (({ effects, ...r }) => r)(s) : s);

function startLoop(p, cur, spec) {
  const dur = Number(typeof spec === "object" ? spec.params?.duration_s : NaN) || Number(p.item?.duration_s) || 1.5;
  let side = 1;
  p.loop = setInterval(async () => {
    if (pushes.get(p.screen) !== p) return stopLoop(p);
    const sc = room.screenOf(p.screen);
    const left = sc?.desired_expires_in_s;
    if (!Number.isFinite(left) || left <= 2) return stopLoop(p);
    const d = liveSpec(cur, "transition", spec, side);
    const r = await room.putDesired(p.screen, d, left);
    if (r.ok) { p.ours = r.data?.desired ?? d; side ^= 1; }
  }, (dur + HOLD_S) * 1000);
}
function stopLoop(p) { if (p?.loop) { clearInterval(p.loop); p.loop = null; } }

export async function more(id) {
  const st = await room.status();
  const sc = st.ok ? st.data.screens?.[id] : room.screenOf(id);
  if (!sc) return;
  const left = Number.isFinite(sc.desired_expires_in_s) ? sc.desired_expires_in_s : 0;
  const r = await room.putDesired(id, sc.desired, left + 120);
  if (!r.ok) { toastErr(errorWords(r, { screen: room.nameOf(id), target: "the room service" })); return; }
  const p = pushes.get(id);
  if (p) p.ours = r.data?.desired ?? sc.desired;
  toast(`${room.nameOf(id)}: 2 more minutes`);
}

export async function putBack(id) {
  const p = pushes.get(id);
  stopLoop(p);
  const st = await room.status();
  const sc = st.ok ? st.data.screens?.[id] : room.screenOf(id);
  pushes.delete(id);
  if (!sc) return;
  const back = sc.desired_expires_to != null ? sc.desired_expires_to : p?.prev != null ? p.prev : "art";
  const r = await room.putDesired(id, back);
  if (!r.ok) { toastErr(errorWords(r, { screen: room.nameOf(id), target: "the room service" })); return; }
  toast(back === "art" ? `${room.nameOf(id)} is back on the art` : `${room.nameOf(id)} is back to what it was showing`);
  S.set({ pushVersion: (S.get().pushVersion || 0) + 1 });
}

export async function backToArt(id) {
  const p = pushes.get(id);
  stopLoop(p);
  pushes.delete(id);
  const r = await room.putDesired(id, "art");
  if (!r.ok) { toastErr(errorWords(r, { screen: room.nameOf(id), target: "the room service" })); return false; }
  toast(`${room.nameOf(id)} is going back to the art`);
  return true;
}

// ---------------------------------------------------------------- truth: did the screen show what we asked for?
const lastErr = new Map();   // session -> plugin_error event
onEvent("session.plugin_error", (e) => { lastErr.set(e.session, e); });
let lookAt = 0;
room.onRoom("status", async (st) => {
  for (const [id, p] of [...pushes]) {
    const sc = st.screens?.[id];
    if (p.sent === false) continue;                                  // its PUT hasn't been answered yet
    if (!sc || !Number.isFinite(sc.desired_expires_in_s)) { stopLoop(p); pushes.delete(id); continue; }
    if (room.isReady(sc) && !p.liveAt) p.liveAt = Date.now();     // ready, not just layer live: look answers 409 mid-cut
    if (p.liveAt && !room.isReady(sc)) continue;
    if (!p.liveAt || p.kind !== "scene" || Date.now() - p.liveAt > 15000 || Date.now() - lookAt < 1000) continue;
    if (Date.now() - p.liveAt < 3000) continue;
    lookAt = Date.now();
    const r = await room.look(id);
    if (!r.ok) continue;
    const want = typeof p.spec === "string" ? p.spec : p.spec?.preset || p.spec?.source;
    const now = r.data?.current_scene || r.data?.scene;
    const names = new Set([want, p.item?.plugin, ...(p.item?.presets || []).map((x) => x.name)]);
    if (now && !names.has(typeof now === "string" ? now : now?.preset || now?.source)) {
      const err = lastErr.get(sc.observed?.session);
      const why = err && err.name === p.item?.plugin ? `${p.label} was ${pluginErrorWords(err).replace(/ \(.*\)$/, "")}` : "the renderer switched it";
      p.warn = `Asked for ${p.label}, showing ${sceneLabel(now)} (${why})`;
      S.set({ pushVersion: (S.get().pushVersion || 0) + 1 });
    }
  }
});
// ---------------------------------------------------------------- the sheet
/**
 * openShowOn(it, {spec, specLabel, options}) — options: [[value, label, spec]] for the "As" picker (variations,
 * saved looks, "With your changes"); spec / value: the current choice.
 */
export function openShowOn(it, { options = [], value = null } = {}) {
  const st = S.get();
  const groups = room.groups();
  // no room at this address (404: opened without the app) vs room down (it was there, or should be): different words
  let missing = false, down = false;
  const linkState = () => {
    const s2 = S.get();
    missing = s2.roomLink === "missing";
    down = !missing && s2.roomLink !== "up" && !(s2.roomLink === "connecting" && !s2.roomStatus);
    return missing || (!s2.roomStatus && s2.roomLink !== "up");      // nothing to show a screen on
  };
  let blocked = linkState();
  let chosen = groups.flatMap((g) => g.screens).includes(st.target) ? st.target : groups[0]?.screens[0] || st.target;
  let dur = st.dur;
  let asVal = value ?? options[0]?.[0] ?? null;
  const rowEls = new Map();
  const goBtn = button("", { kind: "primary", big: true, tid: "showon-go", cls: "btn-block" });
  const why = h("p", { class: "why", hidden: true });
  const durRow = h("div", { class: "row", role: "radiogroup", "aria-label": "For how long" }, h("span", { class: "lab", style: { minWidth: "40px" } }, "For"),
    DURS.map(([s, l]) => chip(l, { pressed: s === dur, tid: `showon-dur-${s}`, onClick: () => { dur = s; S.set({ dur }); renderDur(); renderGo(); } })));
  const renderDur = () => { for (const c of durRow.querySelectorAll(".chip")) { const on = c.dataset.testid === `showon-dur-${dur}`; c.setAttribute("aria-pressed", String(on)); c.querySelector(".icon")?.remove(); if (on) c.prepend(iconCheck()); } };
  const asSel = options.length > 1 ? select(options.map(([v, l]) => [v, l]), asVal, (v) => { asVal = v; }, { tid: "showon-variant", aria: "Show it as" }) : null;
  const asRow = options.length ? h("div", { class: "row" }, h("span", { class: "lab", style: { minWidth: "40px" } }, "As"),
    asSel ? h("div", { class: "grow" }, asSel) : h("span", { tid: "showon-variant" }, options[0][1])) : null;
  const note = h("p", { class: "hint" }, it.kind === "effect" ? "Effects ride on what the screen shows." : it.kind === "transition" ? "Transitions loop two scenes, while this page is open." : "It goes back by itself when the time is up.");
  const body = h("div", {});
  const rowsHost = h("div", { role: "radiogroup", "aria-label": "Screens" });
  body.append(rowsHost);
  const banner = h("div", {});
  body.prepend(banner);
  function renderBanner() {
    blocked = linkState();
    const text = missing ? "There's no room service at this address. Open the Library from the Room page."
      : down ? (S.get().roomStatus ? "The room service isn't answering: these are the last known states, and Show on won't work until it's back."
                                   : "The room service isn't answering: Show on won't work until it's back.")
      : !S.get().roomStatus ? "Connecting to the room service…" : "";
    const sig = `${missing}|${down}|${text}`;
    if (banner._sig === sig) return;
    banner._sig = sig;
    put(banner, text ? h("div", { class: `status tone-${missing || down ? "warn" : "mute"}`, tid: missing || down ? "room-unreachable" : null, style: { marginBottom: "8px" } },
      h("span", { class: "dot", style: { marginTop: "7px" } }), h("div", { class: "grow" }, text)) : null);
  }

  function renderRows() {
    put(rowsHost);
    rowEls.clear();
    let n = 0;
    for (const g of room.groups()) {
      const sec = h("div", { tid: "showon-group", dataset: { room: g.id } },
        h("h3", { class: "group-h" }, g.name, g.real ? h("span", { class: "sub" }, " · real TVs, people see these") : null));
      for (const id of g.screens) {
        n++;
        const sc = room.screenOf(id);
        const sum = room.summary(id, sceneLabel);
        const b = busyNote(id);
        const t = thumbFor(id);
        const img = t ? h("img", { src: t, alt: "", loading: "lazy" }) : null;
        img?.addEventListener("error", () => img.remove());
        const row = h("button", { type: "button", class: "screen-row", role: "radio", "aria-checked": String(id === chosen), tid: `showon-row-${id}`,
                                  dataset: { busy: b ? "true" : "false", key: String(n) } },
          h("span", { class: "radio", "aria-hidden": "true" }), h("span", { class: "sthumb" }, img),
          h("span", { class: "stxt" }, h("span", { class: "sname" }, room.nameOf(id)),
            h("span", { class: `sstate tone-${sum.tone}` }, h("span", { class: "dot" }), h("span", {}, sum.text)),
            b ? h("span", { class: "sbusy", tid: `showon-busy-${id}` }, `${b}. Showing this replaces it for the time you pick, then it goes back.`) : null));
        row.addEventListener("click", () => { chosen = id; for (const [k, r] of rowEls) r.setAttribute("aria-checked", String(k === id)); renderGo(); });
        rowEls.set(id, row);
        sec.append(row);
      }
      rowsHost.append(sec);
    }
    if (!n) rowsHost.append(h("p", { class: "hint" }, missing ? "No screens: this page was opened outside the Screen Machine app." : down ? "No screens: the room service isn't answering." : "Loading the screens…"));
  }
  function renderGo() {
    const name = room.nameOf(chosen);
    const b = busyNote(chosen);
    const mins = DURS.find(([s]) => s === dur)?.[1] || "2 min";
    goBtn.querySelector(".label").textContent = rowEls.size ? `${b ? "Replace on" : "Show on"} ${name} for ${mins}` : `Show on a screen for ${mins}`;
    goBtn.disabled = blocked || !rowEls.size;
    why.textContent = missing ? "This page was opened outside the Screen Machine app, so it can't reach the screens: use the Library link on the Room page." : blocked && down ? "Show on needs the room service, and it isn't answering." : "";
    why.hidden = !why.textContent;
  }
  body.append(h("div", { class: "stack", style: { marginTop: "16px" } }, durRow, asRow, note));
  const cancel = button("Cancel", { kind: "quiet", tid: "showon-cancel" });
  const sheet = openSheet({ id: "showon", title: `Show "${it.title}" on…`, body, tid: "showon-sheet",
    foot: h("div", { class: "stack", style: { gap: "8px" } }, goBtn, why, h("div", { class: "row" }, h("span", { class: "spacer" }), cancel)), focus: false });
  cancel.addEventListener("click", () => sheet.close());
  goBtn.addEventListener("click", async () => {
    const opt = options.find(([v]) => v === asVal);
    const spec = opt ? opt[2] : it.spec;
    busy(goBtn, true, "Showing…");
    const ok = await push(chosen, { ...it, _specLabel: opt?.[3] || it.title }, spec, dur);
    busy(goBtn, false);
    if (ok) { S.set({ target: chosen }); sheet.close(); }
  });
  sheet.el.addEventListener("keydown", (e) => {
    if (e.target.tagName === "SELECT" || e.target.tagName === "INPUT") return;
    if (/^[1-9]$/.test(e.key)) {
      const id = [...rowEls.keys()][Number(e.key) - 1];
      if (id) { rowEls.get(id).click(); rowEls.get(id).focus(); e.preventDefault(); }
    } else if (e.key === "Enter" && !e.target.closest("button")) { e.preventDefault(); if (!goBtn.disabled) goBtn.click(); }
  });
  renderBanner(); renderRows(); renderGo();
  const off = room.onRoom("status", () => { renderBanner(); renderRows(); renderGo(); });
  const offLink = S.on(["roomLink"], () => { renderBanner(); renderRows(); renderGo(); });
  const tick = setInterval(() => { if (!sheet.isOpen()) { clearInterval(tick); off(); offLink(); return; } for (const id of rowEls.keys()) { const b = busyNote(id); const el = rowEls.get(id).querySelector(".sbusy"); if (el && b) el.firstChild.textContent = `${b}. Showing this replaces it for the time you pick, then it goes back.`; } }, 1000);
  requestAnimationFrame(() => (rowEls.get(chosen) || goBtn).focus({ preventScroll: true }));
  return sheet;
}
function iconCheck() { const s = document.createElement("span"); s.className = "icon"; s.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>'; return s; }
