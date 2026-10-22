// Stream Latency Lab — display client.
//
// Modes (URL ?mode=):
//   mse       fMP4 fragments -> MediaSource SourceBuffer -> <video>; timing from requestVideoFrameCallback
//   webcodecs EncodedVideoChunk -> VideoDecoder -> canvas.drawImage; presentation time estimated from rAF
//   wctrack   VideoDecoder -> MediaStreamTrackGenerator -> <video srcObject>; timing from rVFC + rAF grid
//
// Other params: hud=0, target=<frames ahead for MSE>, barcode=<check every N frames, 0=off>,
//   rc=0 (MSE: no speed-up/slow-down to hold `target`; keep playbackRate 1.0 and only jump when > 1 s behind —
//   with 60 fps content on a 60 Hz display any rate > 1 forces Chrome to drop frames),
//   hw=auto|prefer-hardware|prefer-software|no-preference, live=1 (MSE: also set duration=Infinity; cosmetic),
//   draw=immediate|raf (webcodecs canvas), desync=1 (webcodecs: desynchronized canvas; the on-glass estimate
//   assumes a normal canvas), nowclock=1 (server-synced clock overlay — it goes through the same compositor
//   and TV path as the video, so overlay − burned-in clock = capture → composite only, NOT glass-to-glass;
//   use clock.html on a separate screen for that), ws=<url> (default: same origin /ws)
//
// Every timestamp sent to the server is converted to the server clock here (ClockSync.toServer at report
// time); the server does no offset arithmetic.

import { ClockSync, fmtUtc, nowEpoch, toEpoch } from "./sync.js";

// URL name -> P key + parser. set-params from the server goes through the same parsers.
const parseBool = (v, dflt) => {
  if (v === true || v === 1 || v === "1" || v === "true") return true;
  if (v === false || v === 0 || v === "0" || v === "false") return false;
  return dflt;
};
const parseNum = (parse, dflt) => (v) => { const n = parse(v); return Number.isFinite(n) ? n : dflt; };
const DEFAULT_WS = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;
const parseWs = (v) => { // only a ws:/wss: URL; anything else would leave the kiosk unable to connect
  try { const u = new URL(String(v)); if (u.protocol === "ws:" || u.protocol === "wss:") return u.href; } catch (_) {}
  return DEFAULT_WS;
};
const PARAMS = {
  mode: ["mode", (v) => (v == null ? "mse" : String(v))],
  hud: ["hud", (v) => parseBool(v, true)],
  target: ["target", parseNum((v) => parseFloat(v), 2)],
  rc: ["rateControl", (v) => parseBool(v, true)],
  barcode: ["barcodeEvery", parseNum((v) => parseInt(v, 10), 30)],
  hw: ["hw", (v) => (v == null ? "auto" : String(v))],
  live: ["live", (v) => parseBool(v, false)], // MediaSource.duration=Infinity; NOTE: does NOT make Chrome treat
  // the stream as live — liveness (low-delay rendering) comes from the init segment (mvhd duration 0, no mehd).
  draw: ["draw", (v) => (v == null ? "immediate" : String(v))],
  desync: ["desync", (v) => parseBool(v, false)],
  nowclock: ["nowclock", (v) => parseBool(v, false)],
  ws: ["ws", (v) => (v == null ? DEFAULT_WS : parseWs(v))],
};
const qs = new URLSearchParams(location.search);
const P = {};
for (const [name, [key, parse]] of Object.entries(PARAMS)) P[key] = parse(qs.get(name));

const FLAG_KEY = 1, FLAG_EST = 8, FLAG_GAP = 16, FLAG_OVERDRAWN = 32, FLAG_NEVER = 64;
const RECORD_TIMEOUT_MS = 2000;  // a frame not presented by then is reported with FLAG_NEVER
const MAX_DECODE_QUEUE = 2;      // WebCodecs: above this for BACKLOG_GRACE_MS, drop to the next keyframe
const BACKLOG_GRACE_MS = 250;    // a burst (frames held up by a main-thread or network pause) drains within
                                 // this; a decoder slower than real time stays above MAX_DECODE_QUEUE
const SYNC_HOLD_MS = 8000;       // after a (re)connect, hold report items up to this long for a skew fit
const stage = document.getElementById("stage");
const hudEl = document.getElementById("hud");
const nowEl = document.getElementById("nowclock");

function applyVisibility() {
  hudEl.classList.toggle("hidden", !P.hud);
  nowEl.style.display = P.nowclock ? "block" : "none";
}
applyVisibility();

const sync = new ClockSync();
let ws = null;
let cfg = null;
let player = null;
let stopPings = null;
let lastCaps = null;

// ---------------------------------------------------------------------------
// per-frame bookkeeping + reporting
// Records are created only when a frame arrives (onBinary). Every later callback (decoder output, rAF,
// rVFC, updateend) looks the record up with frames.get() and ignores ids that were already reported, so a
// late callback can never re-create a record without recv.
const frames = new Map(); // fid -> {t0, recv, ready, present, expected, flags, tCapture, tSend}
let pendingEvents = [];
let pendingBarcodes = [];
const recent = []; // finalized rows for the HUD (client-side latency view, server clock)

function newRec(fid, tRecv) {
  const r = { t0: tRecv, flags: 0 };
  frames.set(fid, r);
  return r;
}

// Events are rate-limited per name (one per second); repeats are counted and summarized as `suppressed`
// so a failure that fires per frame cannot flood the server console.
const EVENT_MIN_GAP_MS = 1000;
const evState = new Map(); // name -> {last, suppressed, detail}
function event(name, detail = {}) {
  const t = nowEpoch();
  const st = evState.get(name);
  if (st && t - st.last < EVENT_MIN_GAP_MS) { st.suppressed++; st.detail = detail; return; }
  const ev = { event: name, ...detail, t };
  if (st?.suppressed) ev.suppressed = st.suppressed;
  evState.set(name, { last: t, suppressed: 0, detail: null });
  pendingEvents.push(ev);
  if (pendingEvents.length > 500) pendingEvents.shift();
  console.log("[lab]", name, detail);
}

function flushSuppressed(now) {
  for (const [name, st] of evState) {
    if (!st.suppressed || now - st.last < EVENT_MIN_GAP_MS) continue;
    pendingEvents.push({ event: name, ...st.detail, t: now, suppressed: st.suppressed });
    st.last = now; st.suppressed = 0; st.detail = null;
  }
}

function send(obj) {
  if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}

function flushReports() {
  const now = nowEpoch();
  flushSuppressed(now);
  if (!ws || ws.readyState !== WebSocket.OPEN || !cfg) return;
  const S = (t) => { const v = sync.toServer(t); return v == null ? null : +v.toFixed(3); }; // null until locked
  const items = [];
  // Hold records while they cannot be converted well: no lock at all, or (first seconds of a connection)
  // only the offset-only fallback, which drifts ~20 ms/s against a 2 %-skewed server clock. Once the skew
  // fit is valid, the held records are converted with it. After SYNC_HOLD_MS report with what there is.
  const hold = !sync.ready || (!sync.fitted && now - sync.since < SYNC_HOLD_MS);
  for (const [fid, r] of frames) {
    if (hold) {
      if (now - r.t0 > 10000) frames.delete(fid);
      continue;
    }
    const done = r.expected != null || (r.flags & FLAG_OVERDRAWN) || now - r.t0 > RECORD_TIMEOUT_MS;
    if (!done) continue;
    if (r.expected == null && !(r.flags & FLAG_OVERDRAWN)) r.flags |= FLAG_NEVER;
    const row = [fid, S(r.recv), S(r.ready), S(r.present), S(r.expected), r.flags];
    items.push(row);
    if (row[4] != null && r.tCapture != null) {
      recent.push({ t: now, total: row[4] - r.tCapture, net: row[1] - r.tSend,
                    client: r.ready != null ? r.ready - r.recv : null,
                    present: r.expected - (r.ready ?? r.recv) });
    }
    frames.delete(fid);
  }
  while (recent.length && now - recent[0].t > 5000) recent.shift();
  // event times go out on the server clock too: without a lock (start of a connection, new epoch) hold
  // events briefly, then send them with t = null rather than not at all (barcodes carry no times)
  const sendEvents = sync.ready || (pendingEvents.length > 0 && now - pendingEvents[0].t > 2000);
  const events = sendEvents ? pendingEvents.map((ev) => ({ ...ev, t: S(ev.t) })) : [];
  if (!items.length && !events.length && !pendingBarcodes.length) return;
  send({
    type: "report", session: cfg.session, epoch: sync.epoch ?? cfg.epoch ?? null, clock: sync.stats(),
    params: { mode: P.mode, target: P.target, draw: P.draw, hw: P.hw },
    mse: player?.mseStats ? player.mseStats() : null,
    items, events, barcodes: pendingBarcodes,
  });
  if (sendEvents) pendingEvents = [];
  pendingBarcodes = [];
}
setInterval(flushReports, 250);

// ---------------------------------------------------------------------------
// recovery: ask the server for a fresh start (it re-sends config + init and waits for a keyframe;
// the config rebuilds the player). Rate-limited so a persistent failure cannot loop fast.
const recovery = { last: -Infinity, timer: null, count: 0 };
function requestRecovery(reason) {
  if (recovery.timer) return;
  const wait = Math.max(0, recovery.last + 2000 - performance.now());
  recovery.timer = setTimeout(() => {
    recovery.timer = null;
    recovery.last = performance.now();
    if (ws?.readyState !== WebSocket.OPEN) return; // the reconnect sends hello anyway
    recovery.count++;
    event("recover", { reason, n: recovery.count });
    sendHello();
  }, wait);
}
function cancelRecovery() {
  clearTimeout(recovery.timer);
  recovery.timer = null;
}

// ---------------------------------------------------------------------------
// barcode read-back: proves which frame is actually on screen
function crc8(v) {
  let crc = 0;
  for (let i = 3; i >= 0; i--) {
    crc ^= (v >>> (i * 8)) & 0xff;
    for (let b = 0; b < 8; b++) crc = crc & 0x80 ? ((crc << 1) ^ 0x07) & 0xff : (crc << 1) & 0xff;
  }
  return crc;
}

class BarcodeReader {
  constructor(geom) {
    this.g = geom;
    this.canvas = document.createElement("canvas");
    this.canvas.width = geom.count * 4;
    this.canvas.height = 4;
    this.ctx = this.canvas.getContext("2d", { willReadFrequently: false });
    this.ctx.imageSmoothingEnabled = true;
  }

  read(source) {
    const { block, count } = this.g;
    const t0 = performance.now();
    this.ctx.drawImage(source, 0, 0, block * count, block, 0, 0, count * 4, 4);
    const px = this.ctx.getImageData(0, 0, count * 4, 4).data;
    const lum = (i) => px[(1 * count * 4 + i * 4 + 2) * 4 + 1]; // green channel, row 1, centre of block i
    const black = lum(0), white = lum(1);
    const thr = (black + white) / 2;
    let hi = 0, lo = 0;
    for (let i = 0; i < 40; i++) {
      const bit = lum(i + 2) > thr ? 1 : 0;
      if (i < 32) hi = (hi * 2 + bit) >>> 0; else lo = (lo << 1) | bit;
    }
    const ok = white - black > 60 && crc8(hi) === lo;
    return { id: ok ? hi : null, contrast: white - black, cost: performance.now() - t0 };
  }
}

// ---------------------------------------------------------------------------
// display-rate estimate (rAF cadence) — used by the canvas and wctrack paths to estimate on-glass time.
// rAF timestamps are BeginFrame times, so deltas are whole refreshes; main-thread load only adds skipped
// refreshes, never shortens one. A low percentile tracks the panel rate; the median would follow load.
const vsync = { last: null, deltas: [], interval: 1000 / 60 };
function rafLoop(ts) {
  if (vsync.last != null) {
    const d = ts - vsync.last;
    if (d > 2) vsync.deltas.push(d);
    if (vsync.deltas.length > 120) vsync.deltas.shift();
    if (vsync.deltas.length) {
      const s = [...vsync.deltas].sort((a, b) => a - b);
      vsync.interval = s[Math.floor((s.length - 1) * 0.1)];
    }
  }
  vsync.last = ts;
  if (P.nowclock && sync.ready) nowEl.textContent = fmtUtc(sync.serverNow());
  if (player && player.onRaf) player.onRaf(ts);
  requestAnimationFrame(rafLoop);
}
requestAnimationFrame(rafLoop);

// first BeginFrame at or after `t`, on the grid of BeginFrame time `now` (both DOMHighResTimeStamps)
function beginFrameAtOrAfter(t, now) {
  if (t >= now) return now;
  return now - Math.floor((now - t) / vsync.interval) * vsync.interval;
}

// ---------------------------------------------------------------------------
class MsePlayer {
  constructor(cfg) {
    this.cfg = cfg;
    this.type = `video/mp4; codecs="${cfg.codecString}"`;
    this.video = document.createElement("video");
    this.video.muted = true;
    this.video.playsInline = true;
    this.video.disablePictureInPicture = true;
    stage.replaceChildren(this.video);
    this.queue = [];          // {data, fids, keyTime, init}
    this.inflight = null;
    this.started = false;
    this.dead = false;        // fatal: drop everything until the player is rebuilt
    this.destroyed = false;
    this.waitingKey = true;   // only a keyframe can start a decodable run (start / after a drop)
    this.keyTimes = [];       // media times (s) of appended keyframes, ascending — safe eviction points
    this.lastPresented = null;
    this.stalls = 0;
    this.gapSkips = 0;
    this.jumps = 0;
    this.lastJump = -Infinity;
    this.lastAppend = null;   // performance.now() of the last media append / of buffered.end growth
    this.lastGrowth = performance.now();
    this.lastEnd = -1;
    this.rate = 1;
    this.ahead = null;
    this.barcode = new BarcodeReader(cfg.barcode);
    this.vfCount = 0;
    if (!MediaSource.isTypeSupported(this.type)) {
      this.dead = true;
      event("mse-type-unsupported", { type: this.type });
      return;
    }
    this.ms = new MediaSource();
    this.url = URL.createObjectURL(this.ms);
    this.video.src = this.url;
    this.ms.addEventListener("sourceopen", () => this.onOpen(), { once: true });
    this.video.addEventListener("waiting", () => {
      if (this.dead) return;
      this.stalls++;
      event("mse-waiting", { ct: this.video.currentTime, stalls: this.stalls });
      this.watch();
    });
    this.video.addEventListener("error", () =>
      this.fail("video-error", { code: this.video.error?.code, msg: this.video.error?.message }));
    this.video.requestVideoFrameCallback((n, md) => this.onVideoFrame(n, md));
    // Stall watchdog: rVFC never fires while playback is stuck, so gap handling cannot live there.
    this.watchTimer = setInterval(() => this.watch(), 250);
  }

  onOpen() {
    if (this.dead) return;
    try {
      this.sb = this.ms.addSourceBuffer(this.type);
      this.sb.mode = "segments";
      if (P.live) { try { this.ms.duration = Infinity; } catch (e) { event("duration-infinity-failed", { e: String(e) }); } }
    } catch (e) {
      this.dead = true;  // nothing can ever be appended: stop queueing
      this.queue = [];
      event("addSourceBuffer-failed", { e: String(e), type: this.type });
      return;
    }
    this.sb.addEventListener("updateend", () => this.onUpdateEnd());
    this.sb.addEventListener("error", () => this.fail("sourcebuffer-error", {}));
    this.pump();
  }

  // a fatal media error: stop, report once, and ask the server for config + init + keyframe
  fail(name, detail) {
    if (this.dead || this.destroyed) return;
    this.dead = true;
    this.queue = [];
    event(name, { ...detail, fatal: true });
    requestRecovery(name);
  }

  init(bytes) {
    if (this.dead) return;
    this.queue.unshift({ data: bytes, fids: [], keyTime: null, init: true });
    this.pump();
  }

  frame(fid, key, frag) {
    if (this.dead) return;
    if (this.waitingKey && !key) return;
    this.waitingKey = false;
    this.queue.push({ data: frag, fids: [fid], keyTime: key ? fid / this.cfg.fps : null, init: false });
    // bounded backlog (SourceBuffer not open yet, or appends not keeping up): drop to the next keyframe
    if (this.queue.length > 2 * this.cfg.fps) {
      const n = this.queue.length;
      this.queue = this.queue.filter((b) => b.init);
      this.waitingKey = true;
      event("mse-queue-overflow", { dropped: n - this.queue.length });
      return;
    }
    this.pump();
  }

  pump() {
    if (this.dead || !this.sb || this.sb.updating || !this.queue.length) return;
    if (this.ms.readyState !== "open") { this.fail("mediasource-not-open", { state: this.ms.readyState }); return; }
    const batch = this.queue.splice(0, this.queue.length);
    const total = batch.reduce((n, b) => n + b.data.byteLength, 0);
    const buf = new Uint8Array(total);
    let o = 0;
    for (const b of batch) { buf.set(b.data, o); o += b.data.byteLength; }
    this.inflight = batch;
    try {
      this.sb.appendBuffer(buf);
    } catch (e) {
      this.inflight = null;
      if (e.name === "QuotaExceededError" && !this.video.error) {
        // retry after evicting played data; if there is nothing safe to evict, drop to the next keyframe
        this.queue.unshift(...batch);
        if (!this.evict(true)) {
          const n = this.queue.length;
          this.queue = this.queue.filter((b) => b.init);
          this.waitingKey = true;
          event("mse-quota-drop", { dropped: n - this.queue.length });
        }
        return;
      }
      this.fail("appendBuffer-failed", { e: String(e) });
    }
  }

  onUpdateEnd() {
    if (this.dead) return;
    if (this.inflight) { // an append finished (updateend also follows remove())
      const t = nowEpoch();
      for (const b of this.inflight) {
        for (const fid of b.fids) { const r = frames.get(fid); if (r) r.ready = t; }
        if (b.keyTime != null) this.keyTimes.push(b.keyTime);
      }
      if (this.inflight.some((b) => b.fids.length)) this.lastAppend = performance.now();
      if (this.keyTimes.length > 4000) this.keyTimes.splice(0, this.keyTimes.length - 4000);
      this.inflight = null;
    }
    const b = this.sb.buffered;
    const end = b.length ? b.end(b.length - 1) : -1;
    if (end > this.lastEnd) { this.lastEnd = end; this.lastGrowth = performance.now(); }
    if (!this.started && b.length) {
      this.started = true;
      this.video.currentTime = b.start(b.length - 1);
      this.video.play().catch((e) => event("play-failed", { e: String(e) }));
    }
    this.evict(false);
    this.pump();
  }

  // Remove played data — but only up to a keyframe. MSE's coded-frame removal extends a remove() to the
  // next random-access point at/after its end, so remove(0, ct - 2) would also take the GOP being played
  // (and, with a long GOP, the whole look-ahead). Cut half a frame before the keyframe that starts the
  // GOP containing ct - 1 s, so the extension stops at that keyframe. Returns true if a remove started.
  evict(force) {
    if (!this.sb || this.sb.updating) return false;
    const b = this.sb.buffered;
    if (!b.length) return false;
    const ct = this.video.currentTime;
    if (!force && b.start(0) >= ct - 10) return false;
    let k = null;
    for (const t of this.keyTimes) { if (t <= ct - 1) k = t; else break; }
    const end = k == null ? null : k - 0.5 / this.cfg.fps;
    if (end == null || end <= b.start(0)) return false;
    try { this.sb.remove(0, end); } catch (_) { return false; /* retry next time */ }
    this.keyTimes = this.keyTimes.filter((t) => t >= k);
    return true;
  }

  // seek to the live edge of the newest buffered range
  jumpToLive(reason, detail) {
    const v = this.video, b = this.sb?.buffered;
    if (!b || !b.length || v.seeking || performance.now() - this.lastJump < 500) return;
    const i = b.length - 1;
    const to = Math.max(b.start(i), b.end(i) - (P.target + 1) / this.cfg.fps);
    const from = v.currentTime;
    if (to <= from) return;
    this.lastJump = performance.now();
    if (reason === "mse-gap-skip") this.gapSkips++; else this.jumps++;
    v.currentTime = to;
    event(reason, { from: +from.toFixed(3), to: +to.toFixed(3), ranges: b.length, ...detail });
  }

  // Watchdog (250 ms + every 'waiting'): Chrome does not play across a hole in the buffered timeline
  // (e.g. after a server drop-to-keyframe), and rVFC — where the live-edge controller runs — never fires
  // while stuck. If playback is stalled or outside every range and newer data exists, skip to it.
  watch() {
    if (this.dead || this.destroyed || !this.sb) return;
    // appends keep succeeding but the buffer never grows: the parser is stuck (e.g. a bad box size makes it
    // wait for bytes that never come) — no error event fires, so treat it as fatal here
    if (this.lastAppend != null && this.lastAppend - this.lastGrowth > 3000) {
      this.fail("mse-no-progress", { end: this.lastEnd });
      return;
    }
    if (!this.started) return;
    const v = this.video, b = this.sb.buffered, ct = v.currentTime;
    if (!b.length || v.seeking) return;
    let inside = false, later = false;
    for (let i = 0; i < b.length; i++) {
      if (ct >= b.start(i) && ct < b.end(i)) inside = true;
      if (b.start(i) > ct) later = true;
    }
    if (later && (!inside || v.readyState < HTMLMediaElement.HAVE_FUTURE_DATA)) {
      this.jumpToLive("mse-gap-skip", { readyState: v.readyState });
      return;
    }
    // far behind and no rVFC to notice it (e.g. frames not being presented): same jump as onVideoFrame
    const ahead = (b.end(b.length - 1) - ct) * this.cfg.fps - 1;
    if (ahead > this.cfg.fps) this.jumpToLive("mse-jump", { ahead: +ahead.toFixed(1) });
  }

  onVideoFrame(now, md) {
    if (this.dead || this.destroyed) return;
    const fps = this.cfg.fps;
    const fid = Math.round(md.mediaTime * fps);
    const r = frames.get(fid);
    if (r) {
      r.present = toEpoch(md.presentationTime);
      r.expected = toEpoch(md.expectedDisplayTime);
      if (this.lastPresented != null && md.presentedFrames - this.lastPresented > 1) r.flags |= FLAG_GAP;
    }
    this.lastPresented = md.presentedFrames;
    // barcode check: is the frame on screen really frame `fid`?
    if (P.barcodeEvery > 0 && ++this.vfCount % P.barcodeEvery === 0) {
      const res = this.barcode.read(this.video);
      pendingBarcodes.push({ frame: fid, read: res.id, ok: res.id === fid, contrast: res.contrast, cost: +res.cost.toFixed(2) });
    }
    // live-edge controller: keep `target` frames buffered ahead of the presented frame
    const b = this.sb?.buffered;
    if (b && b.length) {
      const end = b.end(b.length - 1);
      const ahead = (end - md.mediaTime) * fps - 1;
      this.ahead = ahead;
      if (ahead > fps) { // > 1 s behind: jump (costs a decode from the last keyframe)
        this.jumpToLive("mse-jump", { ahead: +ahead.toFixed(1) });
      } else {
        // NOTE: `ahead` is whole frames, so there is a one-frame dead zone at err = 0. A continuous error
        // (+ age of the newest frame) was tried: rVFC callback-phase jitter (±½ frame) kept playbackRate
        // off 1.0 and Chrome dropped ~3x more frames (headless, 30 fps on 60 Hz). Kept integer.
        const err = ahead - P.target;
        const rate = P.rateControl ? Math.min(1.5, Math.max(0.9, 1 + 0.06 * err)) : 1;
        if (Math.abs(rate - this.rate) > 0.01) { this.video.playbackRate = rate; this.rate = rate; }
      }
    }
    this.video.requestVideoFrameCallback((n, m) => this.onVideoFrame(n, m));
  }

  mseStats() {
    return { ahead: this.ahead, rate: +this.rate.toFixed(3), stalls: this.stalls };
  }

  status() {
    const q = this.video.getVideoPlaybackQuality?.();
    return `ahead ${this.ahead == null ? "-" : this.ahead.toFixed(1)}f  rate ${this.rate.toFixed(2)}  stalls ${this.stalls}` +
      `  gaps ${this.gapSkips}  jumps ${this.jumps}${this.dead ? "  DEAD" : ""}` +
      (q ? `\ndropped ${q.droppedVideoFrames}/${q.totalVideoFrames}` : "");
  }

  destroy() {
    this.destroyed = true;
    this.dead = true;
    clearInterval(this.watchTimer);
    try { this.video.pause(); } catch (_) {}
    this.video.removeAttribute("src");
    this.video.load();
    if (this.url) URL.revokeObjectURL(this.url);
    this.video.remove();
  }
}

// ---------------------------------------------------------------------------
class WebCodecsBase {
  constructor(cfg) {
    this.cfg = cfg;
    this.waitingKey = true;
    this.dropReason = null;  // why we are waiting for a keyframe: "backlog" drops are counted
    this.decoded = 0;
    this.errors = 0;
    this.rebuilds = 0;
    this.failStreak = 0;     // errors since the last successful output (backoff)
    this.hwInitFailures = 0; // decoders that failed before producing any output under prefer-hardware
    this.outputs = 0;        // outputs of the current decoder instance
    this.lastRebuild = -Infinity;
    this.rebuildTimer = null;
    this.backlogDrops = 0;
    this.backlogEpisodes = 0;
    this.backlogSince = null; // performance.now() since when the queue has been above MAX_DECODE_QUEUE
    this.destroyed = false;
    this.barcode = new BarcodeReader(cfg.barcode);
    this.vfCount = 0;
    const desc = cfg.description ? Uint8Array.from(cfg.description.match(/../g).map((h) => parseInt(h, 16))) : undefined;
    this.decoderConfig = {
      codec: cfg.codecString, codedWidth: cfg.width, codedHeight: cfg.height, optimizeForLatency: true,
    };
    if (desc) this.decoderConfig.description = desc;
    this.accel = "?";
    this.decoder = null;
    if (typeof VideoDecoder === "undefined") {
      event("webcodecs-unavailable", { secureContext: window.isSecureContext });
      return;
    }
    this.setup();
  }

  // hw=auto: ask for hardware first; if Chrome has no platform decoder for this config,
  // fall back to software and say so (that is the single most important fact on the N95).
  async setup() {
    let hw = P.hw;
    if (hw === "auto") {
      let ok = false;
      try { ok = (await VideoDecoder.isConfigSupported({ ...this.decoderConfig, hardwareAcceleration: "prefer-hardware" })).supported; }
      catch (_) { ok = false; }
      hw = ok ? "prefer-hardware" : "no-preference";
      if (!ok) event("webcodecs-software-fallback", { codec: this.cfg.codecString });
    }
    if (this.destroyed) return;
    this.decoderConfig.hardwareAcceleration = hw;
    this.accel = hw;
    send({ type: "info", info: { accel: hw } });
    this.newDecoder();
  }

  // A VideoDecoder is closed before its error callback runs, so recovering means a new instance.
  newDecoder() {
    if (this.destroyed) return;
    try { if (this.decoder && this.decoder.state !== "closed") this.decoder.close(); } catch (_) {}
    this.waitingKey = true;
    this.dropReason = null;
    this.backlogSince = null;
    this.outputs = 0;
    const dec = new VideoDecoder({
      output: (vf) => (dec === this.decoder ? this.onOutput(vf) : vf.close()),
      error: (e) => { if (dec === this.decoder) this.onDecoderError(e); },
    });
    this.decoder = dec;
    try { dec.configure(this.decoderConfig); }
    catch (e) { event("decoder-configure-failed", { e: String(e) }); }
  }

  onDecoderError(e) {
    this.errors++;
    this.failStreak++;
    this.waitingKey = true;
    this.dropReason = "error";
    event("decoder-error", { e: String(e), hw: this.decoderConfig.hardwareAcceleration, n: this.errors });
    // A decoder that dies before its first output under prefer-hardware is most likely a platform decoder
    // that isConfigSupported() advertised but cannot initialise (likely on the N95 with VA-API): after two,
    // fall back to software (hw=auto only — an explicit hw= request is kept and keeps failing visibly).
    if (this.decoderConfig.hardwareAcceleration === "prefer-hardware" && this.outputs === 0 &&
        ++this.hwInitFailures >= 2 && P.hw === "auto") {
      this.decoderConfig.hardwareAcceleration = "prefer-software";
      this.accel = "prefer-software (hw failed)";
      event("webcodecs-hw-runtime-fallback", { failures: this.hwInitFailures });
      send({ type: "info", info: { accel: this.accel } });
    }
    // rebuild: at most one per second, backing off to 10 s while it keeps failing
    if (this.rebuildTimer || this.destroyed) return;
    const gap = Math.min(10000, 1000 * 2 ** Math.max(0, this.failStreak - 1));
    const wait = Math.max(0, this.lastRebuild + gap - performance.now());
    this.rebuildTimer = setTimeout(() => {
      this.rebuildTimer = null;
      this.lastRebuild = performance.now();
      this.rebuilds++;
      this.newDecoder();
    }, wait);
  }

  // extra sink-side backpressure (wctrack)
  sinkBacklogged() { return false; }

  init() { /* config arrives via the JSON config message */ }

  frame(fid, key, frag, moofLen) {
    const dec = this.decoder;
    if (!dec || dec.state !== "configured") return;
    if (this.waitingKey && !key) {
      if (this.dropReason === "backlog") this.backlogDrops++;
      return;
    }
    // Backpressure: a decoder slower than real time must not build a backlog (latency would grow for the
    // whole run and the row would measure the backlog, not the pipeline). Above MAX_DECODE_QUEUE for
    // BACKLOG_GRACE_MS drop this chunk and everything up to the next keyframe; count it so the run shows
    // it cannot keep up. (Without the grace, every 200 ms main-thread/Wi-Fi pause cost a whole GOP.)
    const q = dec.decodeQueueSize;
    const over = q > MAX_DECODE_QUEUE || this.sinkBacklogged();
    const now = performance.now();
    if (!over) this.backlogSince = null;
    else if (this.backlogSince == null) this.backlogSince = now;
    if (over && now - this.backlogSince > BACKLOG_GRACE_MS) {
      this.backlogSince = null;
      if (this.dropReason !== "backlog") {
        this.backlogEpisodes++;
        event("decode-backlog-drop", { q, episodes: this.backlogEpisodes, dropped: this.backlogDrops + 1 });
      }
      this.backlogDrops++;
      this.waitingKey = true;
      this.dropReason = "backlog";
      return;
    }
    this.waitingKey = false;
    this.dropReason = null;
    const data = frag.subarray(moofLen + 8);
    try {
      dec.decode(new EncodedVideoChunk({
        type: key ? "key" : "delta", timestamp: Math.round((fid * 1e6) / this.cfg.fps),
        duration: Math.round(1e6 / this.cfg.fps), data,
      }));
    } catch (e) {
      this.waitingKey = true;
      event("decode-call-failed", { e: String(e) });
    }
  }

  // bookkeeping common to every decoder output; returns the frame id
  outputDone(vf) {
    this.outputs++;
    this.failStreak = 0;
    this.decoded++;
    const fid = this.fidOf(vf);
    const r = frames.get(fid);
    if (r) r.ready = nowEpoch();
    return fid;
  }

  fidOf(vf) { return Math.round((vf.timestamp * this.cfg.fps) / 1e6); }

  maybeBarcode(source, fid) {
    if (P.barcodeEvery > 0 && ++this.vfCount % P.barcodeEvery === 0) {
      const res = this.barcode.read(source);
      pendingBarcodes.push({ frame: fid, read: res.id, ok: res.id === fid, contrast: res.contrast, cost: +res.cost.toFixed(2) });
    }
  }

  wcStatus() {
    return `decoded ${this.decoded}  q ${this.decoder?.decodeQueueSize ?? "-"}  err ${this.errors}  rebuilt ${this.rebuilds}` +
      `  ${this.accel}\nbacklog drops ${this.backlogDrops} (${this.backlogEpisodes}x)`;
  }

  destroy() {
    this.destroyed = true;
    clearTimeout(this.rebuildTimer);
    try { if (this.decoder && this.decoder.state !== "closed") this.decoder.close(); } catch (_) {}
  }
}

// Canvas path. Presentation semantics (shared with the server, flags 8/32):
//  - canvas content drawn before or during rAF k is committed with rendering update k and composited by
//    the BeginFrame whose time is the rAF timestamp ts_k; estimated on-glass time = ts_k + 1 refresh —
//    the same convention as rVFC's expectedDisplayTime (deadline_min = frame_time + interval) on the
//    video paths (flag 8: an estimate);
//  - only the LAST frame drawn before a tick is composited; earlier ones were overdrawn (flag 32, not
//    presented). In draw=raf a decoded frame replaced before its tick is overdrawn the same way.
// Assumes a normal (synchronized) canvas: with desync=1 draws can reach the screen without a commit.
class WebCodecsCanvasPlayer extends WebCodecsBase {
  constructor(cfg) {
    super(cfg);
    this.canvas = document.createElement("canvas");
    this.canvas.width = cfg.width;
    this.canvas.height = cfg.height;
    this.ctx = this.canvas.getContext("2d", { alpha: false, desynchronized: P.desync });
    stage.replaceChildren(this.canvas);
    this.pending = null;   // newest decoded-but-not-drawn frame (raf mode)
    this.drawnLast = null; // fid drawn since the last rAF tick (immediate mode)
    this.overdrawn = 0;
  }

  overdraw(fid) {
    this.overdrawn++;
    const r = frames.get(fid);
    if (r) r.flags |= FLAG_OVERDRAWN;
  }

  onOutput(vf) {
    const fid = this.outputDone(vf);
    if (P.draw === "raf") {
      if (this.pending) { this.pending.vf.close(); this.overdraw(this.pending.fid); }
      this.pending = { vf, fid };
      return;
    }
    this.ctx.drawImage(vf, 0, 0, this.canvas.width, this.canvas.height);
    vf.close();
    if (this.drawnLast != null) this.overdraw(this.drawnLast); // replaced before it was composited
    this.drawnLast = fid;
  }

  onRaf(ts) {
    let fid;
    if (P.draw === "raf") {
      if (!this.pending) return;
      const { vf } = this.pending;
      fid = this.pending.fid;
      this.pending = null;
      this.ctx.drawImage(vf, 0, 0, this.canvas.width, this.canvas.height);
      vf.close();
    } else {
      if (this.drawnLast == null) return;
      fid = this.drawnLast;
      this.drawnLast = null;
    }
    const r = frames.get(fid);
    if (r) {
      r.present = toEpoch(ts);
      r.expected = r.present + vsync.interval;
      r.flags |= FLAG_EST;
    }
    // read the barcode back from the canvas (what gets composited), not from the decoder's VideoFrame
    this.maybeBarcode(this.canvas, fid);
  }

  status() {
    return this.wcStatus() + `  overdrawn ${this.overdrawn}\nvsync ${vsync.interval.toFixed(2)} ms (est. on-glass)`;
  }

  destroy() {
    super.destroy();
    this.pending?.vf.close();
    this.pending = null;
  }
}

class WebCodecsTrackPlayer extends WebCodecsBase {
  constructor(cfg) {
    super(cfg);
    this.video = document.createElement("video");
    this.video.muted = true;
    this.video.playsInline = true;
    stage.replaceChildren(this.video);
    this.pendingWrites = 0;
    if (typeof MediaStreamTrackGenerator === "undefined") {
      event("mstg-unavailable");
      return;
    }
    this.gen = new MediaStreamTrackGenerator({ kind: "video" });
    this.writer = this.gen.writable.getWriter();
    this.video.srcObject = new MediaStream([this.gen]);
    this.video.play().catch((e) => event("play-failed", { e: String(e) }));
    this.lastPresented = null;
    this.mediaTimeMode = null; // learn whether rVFC mediaTime carries our timestamps
    this.video.requestVideoFrameCallback((n, md) => this.onVideoFrame(n, md));
  }

  sinkBacklogged() { return this.pendingWrites > MAX_DECODE_QUEUE; }

  onOutput(vf) {
    this.outputDone(vf);
    if (!this.writer) { vf.close(); return; }
    this.pendingWrites++;
    this.writer.write(vf).then(() => { this.pendingWrites--; },
      (e) => { this.pendingWrites--; event("mstg-write-failed", { e: String(e) }); });
  }

  onVideoFrame(now, md) {
    if (this.destroyed) return;
    // Map the presented frame back to a frame id: prefer mediaTime (if it carries the
    // VideoFrame timestamp); fall back to reading the barcode on every frame.
    let fid = Math.round(md.mediaTime * this.cfg.fps);
    if (this.mediaTimeMode !== "ok") {
      const res = this.barcode.read(this.video);
      if (res.id != null) {
        if (res.id === fid) this.mediaTimeMode = "ok";
        else { this.mediaTimeMode = "barcode"; fid = res.id; }
      } else if (this.mediaTimeMode === "barcode") {
        fid = null;
      }
    }
    if (fid != null) {
      const r = frames.get(fid);
      if (r) {
        // On the MediaStreamTrackGenerator path Chrome's presentationTime is the frame's arrival at the
        // compositor and expectedDisplayTime is the previous BeginFrame's deadline — not tied to this frame
        // and often earlier than its arrival. Estimate instead with the convention of the other modes: the
        // first BeginFrame at/after arrival (on the grid of `now`, itself a BeginFrame time) + 1 refresh.
        r.present = toEpoch(md.presentationTime);
        r.expected = toEpoch(beginFrameAtOrAfter(md.presentationTime, now)) + vsync.interval;
        r.flags |= FLAG_EST;
        if (this.lastPresented != null && md.presentedFrames - this.lastPresented > 1) r.flags |= FLAG_GAP;
      }
      if (this.mediaTimeMode === "ok") this.maybeBarcode(this.video, fid);
    }
    this.lastPresented = md.presentedFrames;
    this.video.requestVideoFrameCallback((n, m) => this.onVideoFrame(n, m));
  }

  status() {
    return this.wcStatus() + `  writes ${this.pendingWrites}  map ${this.mediaTimeMode ?? "?"}`;
  }

  destroy() {
    super.destroy();
    try { this.writer?.close(); } catch (_) {}
    this.video.srcObject = null;
    this.video.remove();
  }
}

const PLAYERS = { mse: MsePlayer, webcodecs: WebCodecsCanvasPlayer, wctrack: WebCodecsTrackPlayer };

// ---------------------------------------------------------------------------
async function capabilities(cfg) {
  const out = { mode: P.mode, secureContext: window.isSecureContext };
  const type = `video/mp4; codecs="${cfg.codecString}"`;
  out.mseTypeSupported = typeof MediaSource !== "undefined" && MediaSource.isTypeSupported(type);
  try {
    const mc = await navigator.mediaCapabilities.decodingInfo({
      type: "media-source",
      video: { contentType: type, width: cfg.width, height: cfg.height, bitrate: 8e6, framerate: cfg.fps },
    });
    out.mediaCapabilities = { supported: mc.supported, smooth: mc.smooth, powerEfficient: mc.powerEfficient };
  } catch (e) { out.mediaCapabilities = String(e); }
  if (typeof VideoDecoder !== "undefined") {
    const base = { codec: cfg.codecString, codedWidth: cfg.width, codedHeight: cfg.height };
    if (cfg.description) base.description = Uint8Array.from(cfg.description.match(/../g).map((h) => parseInt(h, 16)));
    for (const hw of ["prefer-hardware", "prefer-software"]) {
      try {
        const r = await VideoDecoder.isConfigSupported({ ...base, hardwareAcceleration: hw });
        out[`webcodecs_${hw}`] = r.supported;
      } catch (e) { out[`webcodecs_${hw}`] = String(e); }
    }
  } else out.webcodecs = "unavailable";
  return out;
}

function envInfo() {
  return {
    ua: navigator.userAgent, cores: navigator.hardwareConcurrency, dpr: devicePixelRatio,
    screen: `${screen.width}x${screen.height}`, viewport: `${innerWidth}x${innerHeight}`,
    secureContext: window.isSecureContext, params: P,
    hasRVFC: "requestVideoFrameCallback" in HTMLVideoElement.prototype,
    hasMSTG: typeof MediaStreamTrackGenerator !== "undefined",
    hasVideoDecoder: typeof VideoDecoder !== "undefined",
  };
}

function sendHello() { // the server answers with config + init and waits for a keyframe
  send({ type: "hello", role: "viewer", mode: P.mode, info: envInfo() });
}

function rebuild() {
  cancelRecovery();
  if (player) { player.destroy(); player = null; }
  frames.clear();
  if (!cfg) return;
  const Cls = PLAYERS[P.mode] || MsePlayer;
  player = new Cls(cfg);
  capabilities(cfg).then((caps) => {
    send({ type: "info", info: { caps } });
    lastCaps = caps;
  });
}

function applyParams(params) {
  const oldWs = P.ws;
  for (const [k, v] of Object.entries(params)) {
    // accept the URL name (barcode) or the internal key (barcodeEvery)
    const name = Object.hasOwn(PARAMS, k) ? k : Object.keys(PARAMS).find((n) => PARAMS[n][0] === k);
    if (!name) { qs.set(k, String(v)); continue; }
    const [key, parse] = PARAMS[name];
    P[key] = parse(v);
    qs.set(name, typeof P[key] === "boolean" ? (P[key] ? "1" : "0") : String(P[key]));
  }
  history.replaceState(null, "", `${location.pathname}?${qs}`);
  applyVisibility();
  if (P.ws !== oldWs) { ws?.close(); return; } // reconnect to the new URL (sends hello)
  sendHello(); // server re-sends config+init -> rebuild() with the new params
}

function onJson(msg) {
  switch (msg.type) {
    case "pong": sync.pong(msg); break;
    case "config": sync.setEpoch(msg.epoch); cfg = msg; rebuild(); break;
    case "set-params": applyParams(msg.params || {}); break;
    default: break;
  }
}

function onBinary(buf, tRecv) {
  const u8 = new Uint8Array(buf);
  if (u8[0] === 1) { player?.init(u8.subarray(1)); return; }
  if (u8[0] !== 2 || !player) return;
  const dv = new DataView(buf);
  const key = (dv.getUint8(1) & 1) === 1;
  const fid = dv.getUint32(2, true);
  const r = newRec(fid, tRecv);
  r.recv = tRecv;
  r.tCapture = dv.getFloat64(6, true);
  r.tSend = dv.getFloat64(22, true);
  if (key) r.flags |= FLAG_KEY;
  const moofLen = dv.getUint32(30, true);
  player.frame(fid, key, u8.subarray(34), moofLen);
}

// A ws= override (URL or set-params) that never connects would strand the kiosk where no set-params can
// reach it: after WS_FALLBACK_AFTER failed attempts in a row, go back to the page's own origin.
const WS_FALLBACK_AFTER = 10;
let wsFailures = 0;
function setWsParam(url) {
  P.ws = url;
  if (url === DEFAULT_WS) qs.delete("ws"); else qs.set("ws", url);
  history.replaceState(null, "", `${location.pathname}?${qs}`);
}
function connect() {
  if (wsFailures >= WS_FALLBACK_AFTER && P.ws !== DEFAULT_WS) {
    event("ws-fallback", { from: P.ws, to: DEFAULT_WS, failures: wsFailures });
    setWsParam(DEFAULT_WS);
  }
  try {
    ws = new WebSocket(P.ws);
  } catch (e) {
    wsFailures++;
    console.error("[lab] bad ws url", P.ws, String(e));
    setWsParam(DEFAULT_WS);
    setTimeout(connect, 1000);
    return;
  }
  ws.binaryType = "arraybuffer";
  ws.onopen = () => {
    wsFailures = 0;
    stopPings = sync.start(ws); // new connection: clock samples start from scratch
    sendHello();
  };
  ws.onmessage = (ev) => {
    const t = nowEpoch();
    if (typeof ev.data === "string") onJson(JSON.parse(ev.data));
    else onBinary(ev.data, t);
  };
  ws.onclose = () => {
    if (!stopPings) wsFailures++; // closed without ever opening
    stopPings?.();
    stopPings = null;
    cancelRecovery();
    if (player) { player.destroy(); player = null; }
    cfg = null;
    setTimeout(connect, 1000);
  };
}
connect();

// debug handle for beelink/cdp_probe.py eval / local tests (read-only use)
window.lab = { P, sync, frames, vsync, recovery, get player() { return player; }, get cfg() { return cfg; } };

// ---------------------------------------------------------------------------
function pctl(arr, p) {
  const a = arr.filter((v) => v != null);
  if (!a.length) return null;
  const s = a.sort((x, y) => x - y);
  return s[Math.min(s.length - 1, Math.floor((s.length - 1) * p))];
}
const f1 = (v) => (v == null ? "  -  " : v.toFixed(1).padStart(5));

setInterval(() => {
  if (!P.hud) return;
  const tot = recent.map((r) => r.total), net = recent.map((r) => r.net);
  const cli = recent.map((r) => r.client), pre = recent.map((r) => r.present);
  const p50 = pctl(tot, 0.5);
  const cls = p50 == null ? "dim" : p50 < 80 ? "ok" : p50 < 150 ? "warn" : "bad";
  const caps = lastCaps
    ? `mse ${lastCaps.mseTypeSupported ? "y" : "n"} powerEff ${lastCaps.mediaCapabilities?.powerEfficient}` +
      `  wc-hw ${lastCaps["webcodecs_prefer-hardware"]}`
    : "";
  // "total" is capture -> Chrome's expected (or, flag 8, estimated) on-glass time: no X copy, scanout or TV lag
  hudEl.innerHTML =
    `<b>${P.mode}</b>  ${cfg ? `${cfg.codecString} ${cfg.width}x${cfg.height}@${cfg.fps}` : "(no stream)"}\n` +
    `<span class="dim">${caps}</span>\n` +
    `<span class="${cls}">total→exp  p50 ${f1(p50)}  p95 ${f1(pctl(tot, 0.95))} ms</span>\n` +
    `net    p50 ${f1(pctl(net, 0.5))}  client ${f1(pctl(cli, 0.5))}  →exp ${f1(pctl(pre, 0.5))}\n` +
    `clock  rtt ${f1(sync.rtt)} (last ${f1(sync.lastRtt)})  skew ${sync.fitted ? `${sync.ppm.toFixed(0)} ppm` : "fitting…"}` +
    `  resid ${f1(sync.resid)}  n ${sync.n}\n` +
    `frames/5s ${recent.length}  recoveries ${recovery.count}\n` +
    (player?.status ? player.status() : "");
}, 500);
