// LivePlayer: reference MSE player for stream_service media sockets (LIVE_V1 §1.4 + the stream rules of §2.2).
//
//   const p = new LivePlayer({url, targetBufferMs: 300, mount: element, onEvent: (name, detail) => {}});
//   p.start();  p.onFirstFrame(cb);  p.watchHold(hold_pts, cb);  p.stats();  p.stop();
//
// Rules implemented (display page rules 4-8 that concern the stream):
//  * MSE <video>, muted, no CSS effects; init segment then fragments; play() at playbackRate 1.0 once at least
//    targetBufferMs is buffered.  playbackRate is never changed.
//  * Edge jump: if the buffer has run more than 2x targetBufferMs ahead for 2 s, jump to
//    buffered.end - targetBufferMs and count an edge jump.
//  * Gap skip: after a server drop-to-keyframe the buffered timeline has a hole Chrome will not play across;
//    if playback is stuck outside the buffered ranges and newer data exists, jump to it (counted separately).
//  * Stall: no new presented frame for 2 s while playing -> 'stall' event.
//  * Hold watch: the first presented frame with mediaTime >= hold_pts - 0.5/fps fires the exit callback.
//  * Teardown: close the socket, abort the SourceBuffer if open, removeAttribute('src'), load(), remove.
//  * Stats ({"type":"stats", presented, dropped, buffered_ms, edge_jumps}) go back to the server about once a second.
// Ported from experiments/stream-latency/static/client.js (MsePlayer) without its live-edge rate control.

export class LivePlayer {
  constructor({ url, targetBufferMs = 300, mount = document.body, onEvent = () => {}, sendStats = true,
                stallMs = 2000, edgeFactor = 2, edgeHoldMs = 2000 }) {
    this.url = url;
    this.target = targetBufferMs / 1000;
    this.mount = mount;
    this.onEvent = onEvent;
    this.sendStats = sendStats;
    this.stallMs = stallMs;
    this.edgeFactor = edgeFactor;
    this.edgeHoldMs = edgeHoldMs;
    this.cfg = null;
    this.ws = null;
    this.video = null;
    this.ms = null;
    this.sb = null;
    this.queue = [];            // {data, keyTime}
    this.inflight = null;
    this.waitingKey = true;
    this.keyTimes = [];
    this.started = false;       // play() called
    this.destroyed = false;
    this.dead = false;
    this.presented = 0;         // rVFC callbacks (presented frames seen by the page)
    this.lastPresentedFrames = null;
    this.skippedPresent = 0;    // gaps in md.presentedFrames between callbacks
    this.lastPresentAt = null;
    this.mediaTime = null;
    this.edgeJumps = 0;
    this.gapSkips = 0;
    this.stalls = 0;
    this.stalled = false;
    this.aheadSince = null;
    this.framesRx = 0;
    this.bytesRx = 0;
    this.firstFrameCbs = [];
    this.hold = null;           // {pts, cb}
    this.eos = false;
    this.timers = [];
  }

  // ---- lifecycle ---------------------------------------------------------------------
  start() {
    const v = (this.video = document.createElement("video"));
    v.muted = true;
    v.playsInline = true;
    v.disablePictureInPicture = true;
    v.style.cssText = "position:absolute;inset:0;width:100%;height:100%;object-fit:contain;background:#000";
    this.mount.appendChild(v);
    v.addEventListener("waiting", () => this.watch());
    v.addEventListener("error", () => this.fail("video-error", { code: v.error?.code, msg: v.error?.message }));
    v.requestVideoFrameCallback((n, md) => this.onVideoFrame(n, md));
    const ws = (this.ws = new WebSocket(this.url));
    ws.binaryType = "arraybuffer";
    ws.onmessage = (e) => this.onMessage(e.data);
    ws.onclose = (e) => this.event("ws-close", { code: e.code, reason: e.reason });
    ws.onerror = () => this.event("ws-error", {});
    this.timers.push(setInterval(() => this.watch(), 250));
    if (this.sendStats) this.timers.push(setInterval(() => this.reportStats(), 1000));
  }

  stop() {
    if (this.destroyed) return;
    this.destroyed = true;
    this.dead = true;
    for (const t of this.timers) clearInterval(t);
    try { this.ws?.close(); } catch (_) {}
    try { if (this.ms?.readyState === "open" && this.sb) this.sb.abort(); } catch (_) {}
    const v = this.video;
    if (v) {
      try { v.pause(); } catch (_) {}
      v.removeAttribute("src");
      v.load();
      v.remove();
    }
    if (this.objectUrl) URL.revokeObjectURL(this.objectUrl);
    this.event("teardown", {});
  }

  onFirstFrame(cb) {
    if (this.presented > 0) cb(this.mediaTime); else this.firstFrameCbs.push(cb);
  }

  // cb(mediaTime) on the first presented frame with mediaTime >= hold_pts - 0.5/fps (LIVE_V1 §2.2 rule 6)
  watchHold(pts, cb) { this.hold = { pts, cb }; }

  // ---- socket ------------------------------------------------------------------------
  onMessage(data) {
    if (this.dead) return;
    if (typeof data === "string") {
      let m;
      try { m = JSON.parse(data); } catch (_) { return; }
      if (m.type === "config") this.onConfig(m);
      else if (m.type === "eos") { this.eos = true; this.event("eos", {}); }
      return;
    }
    const b = new Uint8Array(data);
    if (b[0] === 0x01) {               // [0x01][init]
      this.queue.unshift({ data: b.subarray(1), keyTime: null, init: true });
      this.pump();
    } else if (b[0] === 0x02) {        // [0x02][key][frame_id u32 BE][moof+mdat]
      const key = b[1] === 1;
      const fid = ((b[2] << 24) >>> 0) + (b[3] << 16) + (b[4] << 8) + b[5];
      this.framesRx++;
      this.bytesRx += b.byteLength;
      if (this.waitingKey && !key) return;
      this.waitingKey = false;
      this.queue.push({ data: b.subarray(6), keyTime: key ? fid / this.cfg.fps : null });
      if (this.queue.length > 2 * this.cfg.fps) {   // appends not keeping up: drop to the next keyframe
        this.queue = this.queue.filter((q) => q.init);
        this.waitingKey = true;
        this.event("queue-overflow", {});
        return;
      }
      this.pump();
    }
  }

  onConfig(cfg) {
    if (this.cfg) return;              // one config per socket
    this.cfg = cfg;
    const type = `video/mp4; codecs="${cfg.codec_string}"`;
    this.type = type;
    if (!window.MediaSource || !MediaSource.isTypeSupported(type)) {
      this.fail("type-unsupported", { type });
      return;
    }
    this.ms = new MediaSource();
    this.objectUrl = URL.createObjectURL(this.ms);
    this.video.src = this.objectUrl;
    this.ms.addEventListener("sourceopen", () => {
      try {
        this.sb = this.ms.addSourceBuffer(type);
        this.sb.mode = "segments";
      } catch (e) {
        this.fail("addSourceBuffer-failed", { e: String(e) });
        return;
      }
      this.sb.addEventListener("updateend", () => this.onUpdateEnd());
      this.sb.addEventListener("error", () => this.fail("sourcebuffer-error", {}));
      this.pump();
    }, { once: true });
    this.event("config", cfg);
  }

  // ---- MSE ---------------------------------------------------------------------------
  pump() {
    if (this.dead || !this.sb || this.sb.updating || !this.queue.length) return;
    // Steady state: one queued fragment, appended as the socket message's view (no copy; LIVE_V1 §2.2
    // rule 11).  Only when appends have fallen behind are the queued fragments joined into one append.
    const batch = this.queue.splice(0, this.queue.length);
    let buf;
    if (batch.length === 1) {
      buf = batch[0].data;
    } else {
      buf = new Uint8Array(batch.reduce((n, q) => n + q.data.byteLength, 0));
      let o = 0;
      for (const q of batch) { buf.set(q.data, o); o += q.data.byteLength; }
    }
    this.inflight = batch;
    try {
      this.sb.appendBuffer(buf);
    } catch (e) {
      this.inflight = null;
      if (e.name === "QuotaExceededError") {
        this.queue.unshift(...batch);
        if (!this.evict(true)) {
          this.queue = this.queue.filter((q) => q.init);
          this.waitingKey = true;
          this.event("quota-drop", {});
        }
        return;
      }
      this.fail("append-failed", { e: String(e) });
    }
  }

  onUpdateEnd() {
    if (this.dead) return;
    if (this.inflight) {
      for (const q of this.inflight) if (q.keyTime != null) this.keyTimes.push(q.keyTime);
      if (this.keyTimes.length > 600) this.keyTimes.splice(0, this.keyTimes.length - 600);
      this.inflight = null;
    }
    this.maybeStart();
    this.evict(false);
    this.pump();
  }

  bufferedEnd() {
    const b = this.sb?.buffered;
    return b && b.length ? b.end(b.length - 1) : null;
  }

  maybeStart() {
    if (this.started || !this.sb) return;
    const b = this.sb.buffered;
    if (!b.length) return;
    const i = b.length - 1;
    if (b.end(i) - b.start(i) + 1e-6 < this.target) return;   // wait for target_buffer_ms
    this.started = true;
    this.video.currentTime = Math.max(b.start(i), b.end(i) - this.target);
    this.video.playbackRate = 1.0;
    this.video.play().catch((e) => this.event("play-failed", { e: String(e) }));
    this.event("play", { at: this.video.currentTime });
  }

  // remove played data, only up to a keyframe (MSE extends removals to the next random-access point)
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
    try { this.sb.remove(0, end); } catch (_) { return false; }
    this.keyTimes = this.keyTimes.filter((t) => t >= k);
    return true;
  }

  jumpTo(to, reason) {
    const v = this.video;
    if (v.seeking || to <= v.currentTime) return;
    const from = v.currentTime;
    v.currentTime = to;
    if (reason === "edge-jump") this.edgeJumps++; else this.gapSkips++;
    this.event(reason, { from: +from.toFixed(3), to: +to.toFixed(3) });
  }

  // 250 ms timer + 'waiting': gap skip, edge jump, stall
  watch() {
    if (this.dead || !this.sb || !this.started) return;
    const v = this.video, b = this.sb.buffered, ct = v.currentTime, now = performance.now();
    if (b.length && !v.seeking) {
      let inside = false, later = false;
      for (let i = 0; i < b.length; i++) {
        if (ct >= b.start(i) && ct < b.end(i)) inside = true;
        if (b.start(i) > ct) later = true;
      }
      if (later && (!inside || v.readyState < HTMLMediaElement.HAVE_FUTURE_DATA)) {
        const i = b.length - 1;
        this.jumpTo(Math.max(b.start(i), b.end(i) - this.target), "gap-skip");
      } else {
        const ahead = b.end(b.length - 1) - ct;
        if (ahead > this.edgeFactor * this.target) {
          if (this.aheadSince == null) this.aheadSince = now;
          else if (now - this.aheadSince >= this.edgeHoldMs) {
            this.aheadSince = null;
            this.jumpTo(b.end(b.length - 1) - this.target, "edge-jump");
          }
        } else this.aheadSince = null;
      }
    }
    if (this.lastPresentAt != null && now - this.lastPresentAt > this.stallMs && !this.eos) {
      if (!this.stalled) {
        this.stalled = true;
        this.stalls++;
        this.event("stall", { ms: Math.round(now - this.lastPresentAt) });
      }
    }
  }

  onVideoFrame(now, md) {
    if (this.destroyed) return;
    this.presented++;
    if (this.lastPresentedFrames != null && md.presentedFrames - this.lastPresentedFrames > 1)
      this.skippedPresent += md.presentedFrames - this.lastPresentedFrames - 1;
    this.lastPresentedFrames = md.presentedFrames;
    this.lastPresentAt = performance.now();
    this.stalled = false;
    this.mediaTime = md.mediaTime;
    if (this.presented === 1) {
      const cbs = this.firstFrameCbs.splice(0);
      for (const cb of cbs) cb(md.mediaTime);
      this.event("first-frame", { mediaTime: md.mediaTime });
    }
    if (this.hold && this.cfg && md.mediaTime >= this.hold.pts - 0.5 / this.cfg.fps) {
      const h = this.hold;
      this.hold = null;
      h.cb(md.mediaTime);
    }
    this.video.requestVideoFrameCallback((n, m) => this.onVideoFrame(n, m));
  }

  // ---- reporting -----------------------------------------------------------------------
  stats() {
    const q = this.video?.getVideoPlaybackQuality?.();
    const end = this.bufferedEnd();
    return {
      state: this.dead ? "dead" : this.started ? "playing" : this.cfg ? "buffering" : "connecting",
      fps: this.cfg?.fps ?? null, codec: this.cfg?.codec_string ?? null,
      presented: this.presented, skipped_present: this.skippedPresent,
      total: q?.totalVideoFrames ?? null, dropped: q?.droppedVideoFrames ?? null,
      buffered_ms: end != null && this.video ? Math.round((end - this.video.currentTime) * 1000) : null,
      edge_jumps: this.edgeJumps, gap_skips: this.gapSkips, stalls: this.stalls,
      media_time: this.mediaTime, frames_rx: this.framesRx, bytes_rx: this.bytesRx,
      playback_rate: this.video?.playbackRate ?? null, eos: this.eos,
    };
  }

  reportStats() {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN || !this.started) return;
    const s = this.stats();
    this.ws.send(JSON.stringify({ type: "stats", presented: s.presented, dropped: s.dropped ?? 0,
                                  buffered_ms: s.buffered_ms ?? 0, edge_jumps: s.edge_jumps, stalls: s.stalls }));
  }

  fail(name, detail) {
    if (this.dead) return;
    this.dead = true;
    this.queue = [];
    this.event(name, { ...detail, fatal: true });
  }

  event(name, detail) {
    try { this.onEvent(name, detail); } catch (_) {}
  }
}
