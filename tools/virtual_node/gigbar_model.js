// gigbar_model.js: the virtual GigBAR's shared half (docs/GIGBAR_RIG.md §7), loaded by the board (gigbar.html) and
// the 3D view (gigbar3d.html) as a classic script before their own. It holds the units' set-up (as on their menus and
// DIP switches, kept in this browser), the node's DMX output (GET dmx, one event per DMX refresh: the only input),
// what each channel means (the Chauvet and Antari manuals), and the parts' physical state: pan, tilt and the wheels
// travel at estimated speeds (MECH: not in the manuals), the derby and laser motors turn, the hazer heats and hazes.
// Both views read the same state, so they can't disagree.
"use strict";

// ---- set-up: as on each unit's own menu or DIP switch (kept in this browser) -----------------------------------------
const DEFAULTS = {
  bars: [
    { name: "Bar A", address: 1, mode: 52, panRange: 540, tiltRange: 180, rev: [false, false, false, false] },
    { name: "Bar B", address: 101, mode: 52, panRange: 540, tiltRange: 180, rev: [false, false, false, false] },
  ],
  hazer: { address: 201, skipWarmup: false },
};
let CFG = load();
function load() {
  try { const c = JSON.parse(localStorage.getItem("vgb.cfg")); if (c && c.bars && c.hazer) return c; } catch (e) {}
  return JSON.parse(JSON.stringify(DEFAULTS));
}
function save() { localStorage.setItem("vgb.cfg", JSON.stringify(CFG)); }

// Part offsets in a bar's personality (manual Rev. 10: 52CH; Rev. 8: 50CH, a one-channel laser).
const LAYOUT = {
  52: { par: [0, 7], derby: [14, 20], flash: 26, laser: 31, laserCh: 3, spot: [34, 43], size: 52 },
  50: { par: [0, 7], derby: [14, 20], flash: 26, laser: 31, laserCh: 1, spot: [32, 41], size: 50 },
};
// Mechanical speeds: NOT in the manuals. Estimates for a budget LED mover and its effects.
const MECH = { panDps: 220, tiltDps: 150, accel: 900, wheelSlotsPerS: 9, goboSlotsPerS: 8, derbyRps: 1.2,
               laserRps: 0.8, warmupS: 270 };

// ---- the node's DMX output --------------------------------------------------------------------------------------------
let DMX = new Uint8Array(512), dmxAt = 0, nodeLost = true, nodeAge = null, linkOk = false;
(function connect() {
  const es = new EventSource("dmx");
  es.onopen = () => { linkOk = true; };
  es.onerror = () => { linkOk = false; };
  es.onmessage = (ev) => {
    const m = JSON.parse(ev.data), bin = atob(m.d), u = new Uint8Array(512);
    for (let i = 0; i < 512; i++) u[i] = bin.charCodeAt(i);
    DMX = u; dmxAt = performance.now(); nodeLost = m.lost; nodeAge = m.age_s; linkOk = true;
  };
})();
const ch = (a) => (a >= 1 && a <= 512 ? DMX[a - 1] : 0);         // 1-based DMX address

async function pollStatus() {
  let st = null;
  try { st = await (await fetch("status", { cache: "no-store" })).json(); } catch (e) {}
  const link = document.getElementById("link"), art = document.getElementById("artnet");
  const fresh = linkOk && performance.now() - dmxAt < 1500;
  link.textContent = fresh && st ? `node ${st.node.udp} · universe ${st.node.universe}` : "no DMX from the node";
  link.className = "pill " + (fresh ? "ok" : "bad");
  if (!st) return;
  const a = st.artnet, live = a.age_s !== null && a.age_s < (st.node.loss_s || 4);
  art.textContent = a.age_s === null ? "Art-Net: none yet"
    : live ? `Art-Net: ${a.pps}/s · ${a.length} ch · lost packets ${a.seq_jumps}${a.merging ? " · TWO SOURCES (merging)" : ""}`
           : `Art-Net: none for ${a.age_s.toFixed(0)} s · output ${st.node.failsafe === "hold" ? "holding" : st.node.failsafe}`;
  art.className = "pill " + (a.age_s === null ? "dim" : live ? (a.merging ? "warn" : "ok") : "bad");
  const src = Object.entries(a.sources).map(([ip, s]) => `${ip} (${s.age_s.toFixed(1)} s ago)`).join(", ");
  const other = Object.keys(a.other_universes).length ? ` · also heard universes ${Object.keys(a.other_universes).join(", ")}` : "";
  document.getElementById("src").textContent = (src ? "from " + src : "") + ` · polls answered ${a.replies}` + other;
}
setInterval(pollStatus, 1000); pollStatus();

// ---- channel meanings (the manuals) ------------------------------------------------------------------------------------
const lerp = (a, b, t) => a + (b - a) * t;
const inv = (v, lo, hi) => (v - lo) / Math.max(1, hi - lo);
const pct = (v) => `${Math.round(v / 2.55)}%`;
function barStrobe(v) {          // 000-250 strobe slow to fast, 251-255 to sound (0-5 read as no strobe: §4)
  if (v <= 5) return { mode: "open" };
  if (v <= 250) return { mode: "strobe", hz: lerp(1, 30, inv(v, 6, 250)) };
  return { mode: "sound" };
}
function spotShutter(v) {        // 0-3 closed, 4-7 open, 8-76 strobe, 77-145 pulse, 146-215 random, 216-255 open
  if (v <= 3) return { mode: "closed" };
  if (v <= 7 || v >= 216) return { mode: "open" };
  if (v <= 76) return { mode: "strobe", hz: lerp(1, 30, inv(v, 8, 76)) };
  if (v <= 145) return { mode: "pulse", hz: lerp(1, 30, inv(v, 77, 145)) };
  return { mode: "random", hz: lerp(1, 30, inv(v, 146, 215)) };
}
function shutterWords(s) {
  return s.mode === "open" ? "steady" : s.mode === "closed" ? "shutter closed" : s.mode === "sound" ? "strobing to sound"
       : `${s.mode === "strobe" ? "strobing" : s.mode === "pulse" ? "pulsing" : "random strobe"} ${s.hz.toFixed(0)} Hz`;
}
function shutterGain(s, t, seed) {
  switch (s.mode) {
    case "closed": return 0;
    case "open": return 1;
    case "strobe": return ((t * s.hz) % 1) < 0.18 ? 1 : 0;
    case "pulse": return 1 - ((t * s.hz) % 1);
    case "random": { const k = Math.floor(t * s.hz * 1.3); return hash(k * 7 + seed) < 0.45 && ((t * s.hz * 1.3) % 1) < 0.25 ? 1 : 0; }
    case "sound": return ((t * 2.1) % 1) < 0.12 ? 1 : 0;
  }
  return 1;
}
function hash(n) { const x = Math.sin(n * 127.1 + 311.7) * 43758.5453; return x - Math.floor(x); }
function motor(v, rps) {         // 0 stop, 1-127 CW slow to fast, 128 stop, 129-255 CCW slow to fast
  if (v === 0 || v === 128) return 0;
  return v < 128 ? lerp(0.03, rps, inv(v, 1, 127)) : -lerp(0.03, rps, inv(v, 129, 255));
}
function motorWords(rps) {
  if (!rps) return "not turning";
  const a = Math.abs(rps), sp = a < 0.25 ? "slowly" : a < 0.7 ? "at medium speed" : "fast";
  return `turning ${rps > 0 ? "clockwise" : "anticlockwise"} ${sp}`;
}
const COLOUR_SLOTS = [["white", "#ffffff"], ["red", "#ff2020"], ["orange", "#ff8a1a"], ["yellow", "#ffe81a"],
  ["green", "#22ff44"], ["blue", "#2a4bff"], ["CTO (warm)", "#ffc58f"], ["cyan", "#1ae8ff"], ["magenta", "#ff2aff"],
  ["lime", "#bfff1a"]];
const COLOUR_RANGES = [[0, 6], [7, 13], [14, 20], [21, 27], [28, 34], [35, 41], [42, 48], [49, 55], [56, 62], [63, 64]];
function colourWheel(v) {
  for (let i = 0; i < COLOUR_RANGES.length; i++) if (v <= COLOUR_RANGES[i][1]) return { pos: i };
  if (v <= 189) return { pos: inv(v, 65, 189) * 10, index: true };
  if (v <= 221) return { spin: lerp(MECH.wheelSlotsPerS, 0.3, inv(v, 190, 221)) };
  if (v <= 223) return { spin: 0 };
  return { spin: -lerp(0.3, MECH.wheelSlotsPerS, inv(v, 224, 255)) };
}
function goboWheel(v) {
  if (v <= 53) return { slot: Math.floor(v / 6) };
  if (v <= 63) return { slot: 9 };
  if (v <= 117) { const k = Math.floor((v - 64) / 6); return { slot: 9 - k, shake: lerp(1, 8, ((v - 64) % 6) / 5) }; }
  if (v <= 127) return { slot: 0 };
  if (v <= 189) return { spin: lerp(0.2, MECH.goboSlotsPerS, inv(v, 128, 189)) };
  if (v <= 193) return { spin: 0 };
  return { spin: -lerp(0.2, MECH.goboSlotsPerS, inv(v, 194, 255)) };
}
const LASER_V2 = [[5, null], [41, "red"], [77, "green"], [113, "blue"], [149, "red + green"], [185, "red + blue"],
                  [221, "green + blue"], [255, "red + green + blue"]];
const LASER_V1 = [[0, null], [36, "image colour"], [71, "red"], [73, null], [107, "green"], [110, null], [143, "blue"],
                  [147, null], [179, "red + green"], [184, null], [215, "red + blue"], [221, null], [255, "green + blue"]];
const pick = (table, v) => table.find(([hi]) => v <= hi)[1];
const LASER_RGB = { red: [255, 30, 30], green: [30, 255, 60], blue: [60, 80, 255] };

// colour of an RGBAW+UV par, and its name
function mixPar(r, g, b, am, w, uv) {
  const c = [r + am + w + uv * 0.35, g + am * 0.62 + w * 0.95, b + w * 0.88 + uv * 0.95];
  const m = Math.max(1, ...c);
  return { rgb: c.map((x) => (x / m) * 255), level: Math.min(1, m / 255) };
}
function colourName([r, g, b]) {
  const mx = Math.max(r, g, b), mn = Math.min(r, g, b);
  if (mx < 1) return "off";
  const s = (mx - mn) / mx;
  let h = 0;
  if (mx !== mn) {
    if (mx === r) h = ((g - b) / (mx - mn)) % 6; else if (mx === g) h = (b - r) / (mx - mn) + 2; else h = (r - g) / (mx - mn) + 4;
    h = (h * 60 + 360) % 360;
  }
  if (s < 0.18) return "white";
  if (s < 0.42 && h >= 15 && h < 60) return "warm white";
  if (s < 0.65 && (h < 15 || h >= 330)) return "pink";             // red with white or amber in it
  if (s < 0.65 && h >= 15 && h < 45) return "peach";
  const names = [[12, "red"], [28, "orange"], [45, "amber"], [65, "yellow"], [95, "lime"], [150, "green"], [190, "cyan"],
                 [250, "blue"], [280, "violet"], [320, "magenta"], [345, "pink"], [360, "red"]];
  return names.find(([hi]) => h < hi)[1];
}
const css = (rgb, a = 1) => `rgba(${rgb[0] | 0},${rgb[1] | 0},${rgb[2] | 0},${a})`;
const hexRgb = (h) => [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];

// ---- the moving parts' physical state --------------------------------------------------------------------------------
const SIM = CFG.bars.map(() => ({ spots: [0, 1].map(() => ({ pan: 0, tilt: 0, vp: 0, vt: 0, wheel: 0, gobo: 0 })),
                                  derbyRot: [0, 0], laserRot: 0 }));
const HAZE = { firstDmx: null, density: 0, lastSeen: null, out: 0, fan: 0.2, ready: false };
function wrapDelta(from, to, n) { let d = (to - from) % n; if (d > n / 2) d -= n; if (d < -n / 2) d += n; return d; }
function slew(cur, vel, target, vmax, dt) {
  const err = target - cur;
  const want = Math.max(-vmax, Math.min(vmax, err * 6));
  const dv = Math.max(-MECH.accel * dt, Math.min(MECH.accel * dt, want - vel));
  vel += dv; cur += vel * dt;
  if (Math.abs(target - cur) < 0.2 && Math.abs(vel) < 5) { cur = target; vel = 0; }
  return [cur, vel];
}
function stepSim(dt, t) {
  CFG.bars.forEach((bar, bi) => {
    const L = LAYOUT[bar.mode], S = SIM[bi], a0 = bar.address;
    [0, 1].forEach((k) => {
      const base = a0 + L.spot[k], s = S.spots[k];
      let pt = ((ch(base) * 256 + ch(base + 1)) / 65535 - 0.5) * bar.panRange;
      let tt = ((ch(base + 2) * 256 + ch(base + 3)) / 65535 - 0.5) * bar.tiltRange;
      if (bar.rev[k * 2]) pt = -pt;
      if (bar.rev[k * 2 + 1]) tt = -tt;
      s.targetPan = pt; s.targetTilt = tt;
      const sp = 1 - (ch(base + 4) / 255) * 0.96;               // pan/tilt speed: 0 fastest (assumed, §4)
      [s.pan, s.vp] = slew(s.pan, s.vp, pt, MECH.panDps * sp, dt);
      [s.tilt, s.vt] = slew(s.tilt, s.vt, tt, MECH.tiltDps * sp, dt);
      const cw = colourWheel(ch(base + 5));
      s.wheelSpin = cw.spin;
      if (cw.spin !== undefined) s.wheel = (s.wheel + cw.spin * dt + 100) % 10;
      else { const d = wrapDelta(s.wheel, cw.pos, 10), m = MECH.wheelSlotsPerS * dt;
             s.wheel = (s.wheel + Math.max(-m, Math.min(m, d)) + 10) % 10; }
      const gw = goboWheel(ch(base + 6));
      s.goboSpin = gw.spin; s.shake = gw.shake || 0;
      if (gw.spin !== undefined) s.gobo = (s.gobo + gw.spin * dt + 100) % 10;
      else { const d = wrapDelta(s.gobo, gw.slot, 10), m = MECH.goboSlotsPerS * dt;
             s.gobo = (s.gobo + Math.max(-m, Math.min(m, d)) + 10) % 10; }
    });
    [0, 1].forEach((k) => { S.derbyRot[k] += motor(ch(a0 + L.derby[k] + 5), MECH.derbyRps) * dt * Math.PI * 2; });
    if (L.laserCh === 3) S.laserRot += motor(ch(a0 + L.laser + 2), MECH.laserRps) * dt * Math.PI * 2;
  });
  const h = CFG.hazer, haveDmx = linkOk && performance.now() - dmxAt < 1500;
  if (haveDmx) { if (HAZE.firstDmx === null) HAZE.firstDmx = t; HAZE.lastSeen = t; }
  else if (HAZE.lastSeen !== null && t - HAZE.lastSeen > 3) HAZE.firstDmx = null;
  HAZE.ready = HAZE.firstDmx !== null && (h.skipWarmup || t - HAZE.firstDmx >= MECH.warmupS);
  const hv = ch(h.address), fv = ch(h.address + 1);
  HAZE.out = HAZE.ready && hv >= 5 ? lerp(0.01, 1, inv(hv, 5, 255)) : 0;
  HAZE.fan = fv <= 4 ? 0.2 : lerp(0.21, 1, inv(fv, 5, 255));
  HAZE.density = Math.min(1, HAZE.density + HAZE.out * (0.02 + 0.03 * HAZE.fan) * dt) * Math.exp(-dt / 150);
}
