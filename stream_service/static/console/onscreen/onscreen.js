// On screen: what the room's screens are doing, and driving them.  A switcher grouped by room (choosing one sets the
// target, it never acts on a screen), the screen card with its state in Room's words, then either Go live (art) or
// the live controls; Test music below.  Nothing acts on a screen until a named button is pressed.
import { h, put, visibleInterval } from "../kit.js";
import { S } from "../store.js";
import * as room from "../data/room.js";
import * as catalog from "../data/catalog.js";
import { lib } from "../data/library.js";
import { labelForSpec } from "../data/query.js";

import { chip, statusRow } from "../ui/controls.js";
import { renderArt, steps as goSteps, stepLines, whyLine } from "./golive.js";
import { roomErrorWords, stuckAdvice } from "../words.js";
import { renderLive } from "./live.js";
import { mountMusic } from "./music.js";
import { thumbFor, busyNote } from "./push.js";
import { refresh as refreshLook } from "./look.js";
import { refresh as refreshFx } from "./fx.js";

const isDesktop = () => window.matchMedia("(min-width: 1100px)").matches;
const label = (s) => labelForSpec(s, lib.items, lib.pidx);
let root, switcher, cardHost, musicHost, banner, picker;
let cardMode = null, cardTarget = null, liveCtl = null, head = null;

export function mount(host, { openPicker }) {
  picker = openPicker;
  root = host;
  switcher = h("div", {});
  cardHost = h("div", {});
  musicHost = h("div", {});
  banner = h("div", {});
  put(root, h("div", { class: "page" }, banner, h("div", { class: "os-grid" }, h("div", { class: "os-sw" }, switcher), h("div", { class: "os-card" }, cardHost), h("div", { class: "os-music" }, musicHost))));
  mountMusic(musicHost, { visible: () => S.get().mode === "onscreen", onInputs: (i) => liveCtl?.dirStatus?.(i) });
  S.on(["roomStatus", "roomLink", "target", "libVersion"], () => draw());
  window.matchMedia("(min-width: 1100px)").addEventListener?.("change", () => { drawSwitcher(); });
  catalog.ready().then(() => { cardMode = null; draw(); });
  visibleInterval(() => {
    const t = S.get().target;
    if (room.isReady(room.screenOf(t))) { refreshLook(t); refreshFx(t); }
  }, 5000, () => S.get().mode === "onscreen");
  draw();
}
export function shown() {
  const t = S.get().target;
  cardMode = null;
  draw();
  if (room.isReady(room.screenOf(t))) { refreshLook(t); refreshFx(t); }
}

function draw() {
  drawBanner();
  drawSwitcher();
  drawCard();
}
function drawBanner() {
  const s = S.get();
  if (s.roomLink === "missing") put(banner, statusRow("warn", "Can't reach the room service from this address. Open the Library from the Room page.", { tid: "room-unreachable" }));
  else if (!s.roomStatus && s.roomLink !== "up") put(banner, statusRow("warn", s.roomLink === "connecting" ? "Connecting to the room service…" : "The room service isn't answering: going live won't work until it's back.", { tid: s.roomLink === "connecting" ? null : "room-unreachable" }));
  else if (s.roomLink === "down") put(banner, statusRow("warn", "The room service isn't answering: showing the last known state.", { tid: "room-unreachable" }));
  else put(banner);
}

function drawSwitcher() {
  const groups = room.groups();
  const t = S.get().target;
  const sig = JSON.stringify([isDesktop(), t, groups.map((g) => g.screens.map((id) => { const x = room.summary(id, label); return [id, x.text, x.tone, thumbFor(id)]; }))]);
  if (sig === switcher._sig) return;
  switcher._sig = sig;
  if (!groups.length) { put(switcher); return; }
  if (isDesktop()) {
    put(switcher, h("h2", { class: "sec-h" }, "Screens"), groups.map((g) => h("div", {},
      h("h3", { class: "group-h" }, g.name, g.real ? h("span", { class: "sub" }, " · real TVs, people see these") : null),
      g.screens.map((id) => {
        const sum = room.summary(id, label);
        const src = thumbFor(id);
        const img = src ? h("img", { src, alt: "" }) : null;
        img?.addEventListener("error", () => img.remove());
        const b = h("button", { type: "button", class: "screen-row", tid: `screen-switch-${id}`, "aria-current": String(id === t) },
          h("span", { class: "sthumb" }, img),
          h("span", { class: "stxt" }, h("span", { class: "sname" }, room.nameOf(id)), h("span", { class: `sstate tone-${sum.tone}` }, h("span", { class: "dot" }), h("span", {}, sum.text))));
        b.addEventListener("click", () => S.set({ target: id }));
        return b;
      }))));
  } else {
    put(switcher, h("div", { class: "switcher", role: "group", "aria-label": "Screen" }, groups.map((g) => h("div", { class: "grp" },
      h("span", { class: "glab" }, g.name),
      g.screens.map((id) => {
        const sum = room.summary(id, label);
        const c = chip(room.nameOf(id).replace(/ screen$/i, ""), { pressed: id === t, tid: `screen-switch-${id}`, onClick: () => S.set({ target: id }) });
        c.prepend(h("span", { class: `dot tone-${sum.tone}` }));
        return c;
      })))));
  }
}

function drawCard() {
  const t = S.get().target;
  const sc = room.screenOf(t);
  if (!sc) {
    if (cardMode !== "none") { liveCtl?.destroy(); liveCtl = null; put(cardHost, S.get().roomStatus ? h("div", { class: "card", tid: "screen-card" }, h("p", {}, `There's no screen called ${t} in the room.`)) : null); cardMode = "none"; }
    return;
  }
  const sum = room.summary(t, label);
  const mode = sum.live ? (room.isReady(sc) ? "live" : "busy") : sum.busy || sum.problem ? "busy" : "art";
  if (mode !== cardMode || t !== cardTarget || !head) {
    liveCtl?.destroy(); liveCtl = null;
    cardMode = mode; cardTarget = t;
    head = h("div", { class: "head" });
    const body = h("div", {});
    const card = h("div", { class: "card screen-card", tid: "screen-card" }, head, body);
    put(cardHost, card);
    if (mode === "art") renderArt(body, t, { openPicker: picker });
    else if (mode === "live") liveCtl = renderLive(body, t, { openPicker: picker });
    else {
      put(body, h("div", { class: "steps", style: { marginTop: "12px" }, tid: "golive-steps" }, busyRow(t, sc, sum)),
        sc.desired !== "art" ? h("div", { class: "acts" }, backBtn(t)) : null);
    }
  }
  const src = thumbFor(t);
  const b = busyNote(t);
  const hsig = JSON.stringify([t, src, sum.text, sum.tone, b, Number.isFinite(sc.desired_expires_in_s)]);
  if (head._sig !== hsig) {
  head._sig = hsig;
  const img = src ? h("img", { src, alt: "" }) : null;
  img?.addEventListener("error", () => img.remove());
  put(head, h("span", { class: "sthumb" }, img), h("div", { style: { minWidth: 0 } }, h("h2", {}, room.nameOf(t)),
    h("div", { class: `state tone-${sum.tone}`, tid: "screen-state", "aria-live": "polite" }, h("span", { class: "dot" }), h("span", {}, sum.problem ? "Problem" : sum.text)),   // the card below has the details
    room.isReal(t) ? h("div", { class: "meta" }, "A real TV: people see this one") : null,
    b && Number.isFinite(sc.desired_expires_in_s) ? h("div", { class: "meta warn-ink" }, b) : null));
  }
  if (mode === "busy") {
    const el = cardHost.querySelector("[data-testid=golive-steps]");
    if (el) { const row = busyRow(t, sc, sum); if (el._sig !== row._sig) { el._sig = row._sig; put(el, row); } }
  }
}
// The busy card's one status row: this page's go-live progress while it lasts, else room's state; either way room's
// reason once a try has failed, and what to try when it's stuck or failed.
function busyRow(t, sc, sum) {
  const st = goSteps();
  const mine = st && st.target === t && !st.done && st.text;
  const name = room.nameOf(t);
  let text, sub, tone = sum.tone === "bad" ? "bad" : "warn";
  if (sum.problem) {
    const why = roomErrorWords(sum.detail) || sum.detail;
    // the card is the details "Problem — tap for details" points at: say what went wrong
    text = `${sc.desired !== "art" ? "Couldn't go live" : "There's a problem"}${why ? `: ${why}` : ""}`; sub = stuckAdvice(sc, name); tone = "bad";
  } else if (mine && st.stuck) {
    // the reason is already in the text: the second line says the room is still trying, and what to try
    const again = Number.isFinite(sum.retryIn) && sum.retryIn > 0 ? `, next in ${Math.round(sum.retryIn)} s` : "";
    const keeps = sum.tries > 1 ? `The room has tried ${sum.tries} times and keeps trying${again}. ` : sum.why ? `The room keeps trying${again}. ` : "";
    text = st.text; sub = `${keeps}${st.sub}`; tone = "bad";
  } else if (mine) { text = st.text; sub = st.sub || whyLine(sum); }
  else { text = `${sum.text}${sum.hint ? ` ${sum.hint}` : ""}`; sub = whyLine(sum); }
  const row = statusRow(tone, stepLines({ text, sub }), { icon: tone === "bad" ? "alert" : null });
  row._sig = JSON.stringify([tone, text, sub]);
  return row;
}
function backBtn(t) {
  const b = h("button", { type: "button", class: "btn btn-danger btn-big", tid: "backtoart-btn" }, h("span", { class: "label" }, "Back to art"));
  b.addEventListener("click", async () => { b.disabled = true; const { backToArt } = await import("./push.js"); await backToArt(t); b.disabled = false; });
  return b;
}
