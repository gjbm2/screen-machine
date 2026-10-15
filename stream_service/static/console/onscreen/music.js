// Test music: room's fake show (GET / POST /api/v1/dmx/fake), the stand-in for the lighting desk until Maestro is
// set up: start / stop, tempo (slider, − +, quick chips, ramp), song, breakdown, next section.  Plus "The screen
// hears…": the target screen's show-bus reading in words (room GET …/inputs, 2 Hz while visible and live) and a
// small beat lamp.
import { h, put, visibleInterval } from "../kit.js";
import { S } from "../store.js";
import * as room from "../data/room.js";
import { button, chip, select, switchEl, unroll, slider, busy } from "../ui/controls.js";
import { toastErr } from "../ui/toast.js";
import { errorWords, inputsWords, beatWords } from "../words.js";

const QUICK = [90, 110, 124, 128, 140, 174];
export const M = { view: null, err: null, hidden: false, inputs: null, inputsAt: 0 };
const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : "");

export function mountMusic(host, { visible = () => true, onInputs = () => {} } = {}) {
  const state = h("span", { tid: "music-state", class: "muted" }, "Loading…");
  const startBtn = button("Start", { kind: "primary", icon: "play", tid: "music-start" });
  const stopBtn = button("Stop", { kind: "outline", icon: "stop", tid: "music-stop" });
  const note = h("p", { class: "hint", hidden: true });
  stopBtn.hidden = true; startBtn.disabled = true;           // until room says whether it is playing
  const bpm = slider("Tempo (bpm)", { tid: "music-bpm", min: 40, max: 200, step: 1, value: 124, fmt: (v) => `${Math.round(v)} bpm`, onCommit: (v) => send({ bpm: Math.round(v) }, `${Math.round(v)} bpm`) });
  const minus = button(null, { kind: "outline", icon: "minus", aria: "Slower", cls: "btn-icon", onClick: () => nudge(-1) });
  const plus = button(null, { kind: "outline", icon: "plus", aria: "Faster", cls: "btn-icon", onClick: () => nudge(1) });
  const quick = h("div", { class: "chips" }, QUICK.map((b) => chip(String(b), { tid: `music-bpm-${b}`, onClick: () => { bpm.set(b); send({ bpm: b }, `${b} bpm`); } })));
  const song = select([["pop", "Pop"]], "pop", (v) => { if (M.view?.running) send({ song: v }, v); }, { tid: "music-song", aria: "Song" });
  const brk = switchEl("Breakdown", false, (on) => send({ pulses: !on }, on ? "breakdown" : "beat back"), { tid: "music-breakdown", sub: "Stops the beats; the follower should let go, then lock on again" });
  const nextBtn = button("Next section", { kind: "outline", icon: "skip", tid: "music-next", onClick: () => send({ next_section: true }, "next section") });
  const rto = h("input", { type: "number", class: "input", min: "40", max: "200", value: "90", tid: "music-ramp-to", "aria-label": "Ramp to bpm" });
  const rover = h("input", { type: "number", class: "input", min: "1", max: "600", value: "16", tid: "music-ramp-over", "aria-label": "Over seconds" });
  const rampBtn = button("Ramp", { kind: "outline", tid: "music-ramp", onClick: () => {
    const to = parseFloat(rto.value), over = parseFloat(rover.value);
    if (!(to >= 40 && to <= 200) || !(over > 0 && over <= 600)) { toastErr("Ramp: 40–200 bpm over 1–600 s"); return; }
    send({ ramp: { to_bpm: to, over_s: over } }, `ramp to ${to}`);
  } });
  const lamp = h("span", { class: "lamp", tid: "beat-lamp", "aria-hidden": "true" });
  const hears = h("span", { tid: "music-hears" }, "–");
  const ctlU = unroll("Tempo and song", { hint: "" });
  put(ctlU.body, h("div", { class: "stack" },
    h("div", { class: "row" }, minus, h("div", { class: "grow" }, bpm), plus), quick,
    h("label", { class: "field" }, h("span", { class: "lab" }, "Song (the next start, or the next bar while playing)"), song), brk, h("div", {}, nextBtn),
    h("div", { class: "row" }, h("span", {}, "Ramp to"), rto, h("span", {}, "bpm over"), rover, h("span", {}, "s"), rampBtn)));
  const card = h("div", { class: "card", tid: "music-card" },
    h("div", { class: "row" }, h("h2", { class: "grow", style: { margin: 0 } }, h("span", { class: "icon", html: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>' }), " Test music"), startBtn, stopBtn),
    h("p", { style: { margin: "6px 0 0" } }, state), note,
    h("p", { class: "row", style: { margin: "8px 0 0", fontSize: "15px" } }, h("span", { class: "muted" }, "The screen hears:"), hears, lamp),
    ctlU);
  put(host, card);

  async function send(body, what) {
    const r = await room.postFake(body);
    if (r.ok) { M.view = r.data; M.err = null; draw(); return true; }
    M.err = r; draw();
    if (r.error !== "artnet_live" && r.error !== "dmx_disabled") toastErr(errorWords(r, { what: "Test music", target: "the room service" }));
    return false;
  }
  function nudge(d) {
    const v = Math.max(40, Math.min(200, Math.round(Number(bpm.input.value) + d)));
    bpm.set(v);
    if (M.view?.running) send({ bpm: v }, `${v} bpm`);
  }
  startBtn.addEventListener("click", async () => {
    busy(startBtn, true, "Starting…");
    await send({ action: "start", song: song.value, bpm: Math.round(Number(bpm.input.value)) }, "start");
    busy(startBtn, false); draw();
  });
  stopBtn.addEventListener("click", async () => { busy(stopBtn, true, "Stopping…"); await send({ action: "stop" }, "stop"); busy(stopBtn, false); draw(); });

  let seeded = false;
  function draw() {
    const v = M.view;
    if (!v) return;
    if (v.available === false) { card.hidden = true; M.hidden = true; return; }
    card.hidden = false; M.hidden = false;
    if (!startBtn.querySelector(".spin")) startBtn.disabled = false;
    if (!stopBtn.querySelector(".spin")) stopBtn.disabled = false;
    const s = v.song || {};
    const songs = s.songs || ["ambient", "jazz", "pop", "techno"];
    if (song.options.length !== songs.length) put(song, songs.map((x) => h("option", { value: x }, cap(x))));
    if (document.activeElement !== song) song.value = s.pending_song?.song || s.song || (v.last?.[0]) || "pop";
    const [lo, hi] = s.bpm_range || [40, 200];
    bpm.input.min = lo; bpm.input.max = hi;
    if (!seeded || v.running) { bpm.set(Math.round(s.target_bpm ?? s.bpm ?? 124)); seeded = true; }
    brk.sw.set(v.running && s.pulses === false);
    startBtn.hidden = !!v.running; stopBtn.hidden = !v.running;
    state.textContent = v.running ? `Playing ${s.song} · ${Math.round(s.bpm)} bpm${s.ramp ? ` → ${s.ramp.to_bpm} bpm` : ""}${s.section ? ` · ${s.section}` : ""}` : "Not playing";
    state.className = v.running ? "" : "muted";
    ctlU.setHint(v.running ? `${cap(s.song)} · ${Math.round(s.bpm)} bpm` : "");
    const yielded = M.err?.error === "artnet_live" || (!v.running && String(v.stopped || "").startsWith("real Art-Net"));
    note.hidden = !yielded && !(M.err?.error === "dmx_disabled");
    note.textContent = M.err?.error === "dmx_disabled" ? errorWords(M.err) : yielded ? "The real lighting desk is sending, so test music is off" : "";
  }

  async function poll() {
    const r = await room.fake();
    if (r.ok) { M.view = r.data; draw(); }
    else if (r.status === 404 || r.data?.available === false) { M.view = { available: false }; draw(); }
    else drawDown(r);
  }
  // room unreachable (or erroring): one plain line, no Start / Stop that can't work
  function drawDown(r) {
    card.hidden = false; M.hidden = false;
    state.textContent = r.status === 0 || r.status >= 500 ? "Not available: the room service isn't answering" : `Not available: ${errorWords(r, { target: "the room service" })}`;
    state.className = "muted";
    startBtn.hidden = false; stopBtn.hidden = true;
    if (!startBtn.querySelector(".spin")) startBtn.disabled = true;
    ctlU.setHint("");
  }
  let last = 0;
  const stop1 = visibleInterval(async () => {
    const every = M.view?.running ? 1000 : 3000;
    if (Date.now() - last < every - 50) return;
    last = Date.now();
    await poll();
  }, 500, visible);
  poll();

  // the screen hears (2 Hz while On screen is visible and the target is live)
  const stop2 = visibleInterval(async () => {
    const t = S.get().target;
    if (!room.isReady(room.screenOf(t))) { hears.textContent = "nothing: the screen isn't live"; M.inputs = null; return; }
    const r = await room.inputs(t);
    if (!r.ok) { hears.textContent = errorWords(r, { screen: room.nameOf(t), target: "the room service" }); M.inputs = null; return; }
    M.inputs = r.data; M.inputsAt = performance.now();
    hears.textContent = r.data.online ? beatWords(r.data.derived) : inputsWords(r.data);
    onInputs(r.data);
  }, 500, visible);
  let raf = 0;
  const frame = () => {
    raf = requestAnimationFrame(frame);
    if (!lamp.isConnected) return;
    paintLamp(lamp, M.inputs, M.inputsAt);
  };
  raf = requestAnimationFrame(frame);
  return { destroy() { stop1(); stop2(); cancelAnimationFrame(raf); }, poll };
}

// beat lamp: the follower's predicted beats (beat_phase at now + lead), accent flash; a dim info dot for the raw pulse
export function paintLamp(lamp, i, at) {
  const d = i?.derived;
  if (d && d.beat_locked && d.bpm) {
    const period = 60000 / d.bpm;
    const lead = i.lead?.total_ms ?? 0;
    const ph = (((d.beat_phase - lead / period + (performance.now() - at) / period) % 1) + 1) % 1;
    const glow = Math.exp((-ph * period) / 120);
    lamp.style.background = `rgba(21, 117, 109, ${0.18 + 0.82 * glow})`;
    lamp.style.boxShadow = `0 0 ${Math.round(14 * glow)}px rgba(21,117,109,${glow})`;
  } else if (i?.values) {
    lamp.style.background = `rgba(14, 165, 233, ${0.15 + 0.6 * (i.values.pulse ?? 0)})`;
    lamp.style.boxShadow = "none";
  } else { lamp.style.background = ""; lamp.style.boxShadow = "none"; }
}
