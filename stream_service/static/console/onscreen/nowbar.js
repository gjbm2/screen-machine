// The Now bar: every screen with a timed push (from anywhere), plus the target screen while it is live or busy.
// Timed rows: time left, "+2 min", "Put back".  An untimed live row: "Back to art".  Driven by room's status and a
// 1 s clock; the row text opens On screen with that screen.  Announces phase changes only.
import { h, put, mmss } from "../kit.js";
import { S } from "../store.js";
import * as room from "../data/room.js";
import { pushes, more, putBack, backToArt, desiredLabel } from "./push.js";
import { button, busy } from "../ui/controls.js";
import { placeToasts } from "../ui/toast.js";

let el = null, live = null, lastPhases = "";
export function mount(host, { openScreen }) {
  el = host;
  el.className = "nowbar";
  el.dataset.testid = "nowbar";
  live = h("div", { class: "sr", "aria-live": "polite" });
  document.body.append(live);
  const render = () => draw(openScreen);
  S.on(["roomStatus", "target", "pushVersion", "libVersion"], render);
  window.addEventListener("sm-layers", () => requestAnimationFrame(render));   // it moves above / below the Viewer
  setInterval(render, 1000);
  render();
}

function rowsFor() {
  const st = S.get().roomStatus;
  if (!st?.screens) return [];
  const ids = [];
  for (const [id, sc] of Object.entries(st.screens)) if (Number.isFinite(sc?.desired_expires_in_s)) ids.push(id);
  const t = S.get().target, tsc = st.screens[t];
  if (tsc && !ids.includes(t) && room.isBusy(tsc)) ids.unshift(t);
  else if (ids.includes(t)) { ids.splice(ids.indexOf(t), 1); ids.unshift(t); }
  return ids;
}

const cache = new Map();     // id -> {struct, row, dot, what, time, note}
function draw(openScreen) {
  const ids = rowsFor();
  el.hidden = !ids.length;
  document.body.classList.toggle("has-nowbar", !!ids.length);
  if (!ids.length) { put(el); cache.clear(); placeToasts(); return; }
  const shown = ids.slice(0, 2);
  const phases = [];
  const rows = shown.map((id) => {
    const sc = room.screenOf(id);
    const sum = room.summary(id, () => desiredLabel(id, sc.desired) || "");
    const timed = Number.isFinite(sc.desired_expires_in_s);
    const p = pushes.get(id);
    const what = timed ? (desiredLabel(id, sc.desired) || p?.label || "a Library item") : "";
    // just pushed and room hasn't started the cut yet: say what is happening, not the art it is leaving
    const starting = timed && !sum.live && !sum.busy && !sum.problem && p && Date.now() - p.pushedAt < 15000;
    phases.push(`${room.nameOf(id)}: ${sum.live ? "Live" : sum.text}`);
    const stop = !timed && (sum.live || sc.desired !== "art");
    const noteText = p?.warn || (p?.kind === "transition" && timed ? "Looping while this page is open" : "");
    const struct = `${timed}|${stop}`;
    let c = cache.get(id);
    if (!c || c.struct !== struct) {
      const dot = h("span", { class: "dot" });
      const whatEl = h("span", { class: "nwhat" });
      const note = h("span", { class: "nnote" });
      const time = h("span", { class: "ntime" });
      const txt = h("button", { type: "button", class: "ntext", onclick: () => openScreen(id), "aria-label": `Open ${room.nameOf(id)} on the On screen page` },
        h("span", { class: "l1" }, dot, h("span", { class: "nname" }, room.nameOf(id).replace(/ screen$/i, "")), whatEl), note);
      const acts = [];
      if (timed) {
        const m = button("+2 min", { kind: "quiet", tid: `nowbar-more-${id}`, aria: `Two more minutes on ${room.nameOf(id)}` });
        m.addEventListener("click", async () => { busy(m, true); await more(id); busy(m, false); });
        const b = button("Put back", { kind: "danger", tid: `nowbar-putback-${id}`, aria: `Put ${room.nameOf(id)} back now` });
        b.addEventListener("click", async () => { busy(b, true); await putBack(id); busy(b, false); });
        acts.push(time, m, b);
      } else if (stop) {
        const b = button("Back to art", { kind: "danger", tid: `nowbar-stop-${id}`, aria: `${room.nameOf(id)}: back to art` });
        b.addEventListener("click", async () => { busy(b, true); await backToArt(id); busy(b, false); });
        acts.push(b);
      }
      c = { struct, row: h("div", { class: "nrow", tid: `nowbar-row-${id}` }, txt, ...acts), dot, what: whatEl, time, note, stopBtn: stop ? acts[0] : null };
      cache.set(id, c);
    }
    c.dot.style.background = `var(--${sum.tone}-dot)`;
    c.what.textContent = timed ? `${what} · ${sum.live ? "Live" : starting ? "Switching to live…" : sum.text}` : sum.text;
    c.time.textContent = timed ? mmss(sc.desired_expires_in_s) : "";
    c.note.textContent = noteText;
    c.note.hidden = !noteText;
    c.note.style.color = p?.warn ? "" : "var(--ink-2)";
    c.row.classList.toggle("warn", !!p?.warn);
    if (c.stopBtn && !c.stopBtn.querySelector(".spin")) c.stopBtn.disabled = sc.desired === "art";
    return c.row;
  });
  for (const k of [...cache.keys()]) if (!shown.includes(k)) cache.delete(k);
  const more2 = ids.length > 2 ? h("div", { class: "more-n" }, `+${ids.length - 2} more on the On screen page`) : null;
  if (rows.some((r, i) => el.children[i] !== r) || el.children.length !== rows.length + (more2 ? 1 : 0) || more2) put(el, rows, more2);
  const key = phases.join(" · ");
  if (key !== lastPhases) { lastPhases = key; live.textContent = key; }
  placeToasts();
  document.documentElement.style.setProperty("--nowbar-top", `${Math.round(window.innerHeight - el.getBoundingClientRect().top + 8)}px`);
}
