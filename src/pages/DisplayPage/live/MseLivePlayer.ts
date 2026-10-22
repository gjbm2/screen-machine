// One live stream: media WebSocket (LIVE_V1 §1.4) -> MediaSource/SourceBuffer -> <video>.
// Created and driven imperatively: no React state per frame.
//
//   server -> page: {"type":"config",…}  [0x01][init]  [0x02][key:u8][frame_id:u32 BE][moof+mdat]…  {"type":"eos"}
//   page -> server: {"type":"stats", presented, dropped, buffered_ms, edge_jumps} about once a second
//
// Player rules (LIVE_V1 §2.2 rules 4, 5, 8; DCL §2.5 "Player"):
//   * play() at playbackRate 1.0 once >= target_buffer_ms is buffered; playbackRate is never changed;
//   * latency is shed only by a hard jump: buffer > 2x target for 2 s -> currentTime = end - target;
//   * a hole in the timeline (server drop-to-keyframe) is skipped by seeking to the next range;
//   * teardown frees the decoder: close socket, abort SourceBuffer, removeAttribute('src'), load(), remove.
import { mimeFor, mseSupported } from "../control/caps";
import type { LiveAttach } from "../control/protocol";
import { mediaSocketUrl } from "../secureUrls";

const MSG_INIT = 0x01;
const MSG_FRAME = 0x02;
const WATCH_MS = 250;
const STATS_MS = 1000;
// Latency (owner, 17 Oct: sound to glass under 0.5 s; the lab ran 4K60 HEVC on a ~67-110 ms buffer): jump back to the
// live edge as soon as the buffer runs more than EDGE_MARGIN_S over its target for EDGE_HOLD_MS. Still a hard jump,
// never a playbackRate change. edge_jumps and stalls go out in the stats, so a stall-prone link (powerline) shows.
const EDGE_HOLD_MS = 900;
const EDGE_MARGIN_S = 0.07;
const NO_PROGRESS_MS = 3000;
const EVICT_KEEP_S = 10;

export interface MediaConfig {
  type: "config";
  codec: string;
  codec_string?: string;
  width: number;
  height: number;
  fps: number;
  timescale?: number;
  frame_duration?: number;
}

export interface PlayerEvents {
  /** Every presented frame (requestVideoFrameCallback). Cuts happen inside this callback. */
  onFrame(now: DOMHighResTimeStamp, meta: VideoFrameCallbackMetadata): void;
  /** play() was called: the stream is warm and the next presented frame may be cut to. */
  onStarted(): void;
  /** Every watch tick (250 ms) while the player exists: the stall rule runs here (no extra timer). */
  onTick(now: number): void;
  /** Unrecoverable: MSE/decoder error, unsupported codec, media socket refused. */
  onFatal(reason: string, detail?: unknown): void;
  /** The media socket closed or sent eos (the stall rule decides what that means once live). */
  onEnded(reason: "eos" | "closed", detail?: unknown): void;
  log(msg: string, detail?: unknown): void;
}

interface Chunk {
  data: Uint8Array;
  init: boolean;
  keyTime: number | null;
  hasFrame: boolean;
}

export class MseLivePlayer {
  readonly video: HTMLVideoElement;
  private ws: WebSocket | null = null;
  private ms: MediaSource | null = null;
  private sb: SourceBuffer | null = null;
  private objectUrl: string | null = null;
  private config: MediaConfig | null = null;
  private queue: Chunk[] = [];
  private inflight: Chunk[] | null = null;
  private waitingKey = true;
  private keyTimes: number[] = [];
  private watchTimer: ReturnType<typeof setInterval> | null = null;
  private statsTimer: ReturnType<typeof setInterval> | null = null;
  private rvfcHandle: number | null = null;
  private destroyed = false;
  private failed = false;
  private aheadSince: number | null = null;
  private lastAppendAt: number | null = null;
  private lastGrowthAt = performance.now();
  private lastEnd = -1;

  /** play() has been called (the entry cut may happen on the next presented frame). */
  started = false;
  presented = 0;
  lastPresentedAt: number | null = null;
  lastMediaTime: number | null = null;
  stalls = 0;
  edgeJumps = 0;
  gapSkips = 0;
  framesReceived = 0;
  bytesReceived = 0;

  constructor(
    container: HTMLElement,
    readonly attach: LiveAttach,
    private events: PlayerEvents,
    private role?: string,
  ) {
    const v = document.createElement("video");
    v.muted = true;
    v.defaultMuted = true;
    v.playsInline = true;
    v.autoplay = false;
    v.controls = false;
    v.disablePictureInPicture = true;
    v.setAttribute("disableRemotePlayback", "");
    v.setAttribute("data-sm-live", attach.session);
    // Full viewport, 1:1 at 3840x2160, and no CSS effects of any kind (no opacity/filter/transform/transition).
    Object.assign(v.style, {
      position: "absolute",
      inset: "0",
      width: "100%",
      height: "100%",
      objectFit: "contain",
      backgroundColor: "black",
    });
    this.video = v;
    v.addEventListener("waiting", this.onWaiting);
    v.addEventListener("error", this.onVideoError);
    container.appendChild(v);
  }

  get fps(): number {
    return this.config?.fps || this.attach.fps || 60;
  }

  private get targetS(): number {
    return Math.max(0, this.attach.target_buffer_ms || 0) / 1000;
  }

  start(): void {
    if (typeof this.video.requestVideoFrameCallback !== "function") {
      // The cuts and the stall rule are driven by rVFC; without it the stream could never be cut to.
      this.fail("no-rvfc");
      return;
    }
    let ws: WebSocket;
    try {
      ws = new WebSocket(mediaSocketUrl(this.attach.url));
    } catch (e) {
      // (Not String(e): the message would contain the URL and its per-session key.)
      this.fail("media-url", (e as Error)?.name ?? "error");
      return;
    }
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    ws.onmessage = (ev) => this.onMessage(ev.data);
    ws.onclose = (ev) => {
      if (this.ws !== ws || this.destroyed) return;
      this.ws = null;
      if (ev.code === 4403) this.fail("media-forbidden", { code: ev.code });
      else this.events.onEnded("closed", { code: ev.code, reason: ev.reason });
    };
    ws.onerror = () => {
      /* onclose follows */
    };
    this.rvfcHandle = this.video.requestVideoFrameCallback(this.onVideoFrame);
    this.watchTimer = setInterval(this.watch, WATCH_MS);
    this.statsTimer = setInterval(this.sendStats, STATS_MS);
  }

  /** Buffered media ahead of the playhead, in ms. */
  bufferedMs(): number {
    const b = this.sb?.buffered;
    if (!b || !b.length) return 0;
    return Math.max(0, (b.end(b.length - 1) - this.video.currentTime) * 1000);
  }

  dropped(): number {
    return this.video.getVideoPlaybackQuality?.().droppedVideoFrames ?? 0;
  }

  /** No frame presented for a while (LiveController's stall nudge): jump to the live edge of what is buffered and
   *  resume playing. Never fails and never tears down. */
  kick(): void {
    if (this.destroyed || this.failed || !this.sb || !this.started) return;
    const v = this.video;
    const b = this.sb.buffered;
    if (b.length && !v.seeking) {
      const end = b.end(b.length - 1);
      if (end - v.currentTime > 0.05) {
        v.currentTime = Math.max(b.start(b.length - 1), end - this.targetS);
        this.edgeJumps++;
        this.events.log("live: stall nudge to the edge", { to: v.currentTime });
      }
    }
    void v.play().catch(() => undefined);
  }

  /** Teardown (LIVE_V1 §2.2 rule 8). Idempotent. */
  destroy(): void {
    if (this.destroyed) return;
    this.destroyed = true;
    if (this.watchTimer) clearInterval(this.watchTimer);
    if (this.statsTimer) clearInterval(this.statsTimer);
    if (this.rvfcHandle !== null) {
      try {
        this.video.cancelVideoFrameCallback(this.rvfcHandle);
      } catch {
        /* unsupported */
      }
    }
    const ws = this.ws;
    this.ws = null;
    if (ws) {
      ws.onmessage = ws.onclose = ws.onerror = null;
      try {
        ws.close(1000, "teardown");
      } catch {
        /* already closed */
      }
    }
    this.queue = [];
    this.inflight = null;
    const v = this.video;
    v.removeEventListener("waiting", this.onWaiting);
    v.removeEventListener("error", this.onVideoError);
    try {
      if (this.ms && this.ms.readyState === "open" && this.sb) this.sb.abort();
    } catch {
      /* not open any more */
    }
    try {
      v.pause();
    } catch {
      /* ignore */
    }
    v.removeAttribute("src");
    v.load(); // releases the decoder (VA-API on the kiosk)
    if (this.objectUrl) URL.revokeObjectURL(this.objectUrl);
    this.objectUrl = null;
    this.sb = null;
    this.ms = null;
    v.remove();
  }

  // -- media socket --------------------------------------------------------------------------------

  private onMessage(data: unknown): void {
    if (this.destroyed || this.failed) return;
    if (typeof data === "string") {
      let m: { type?: string } & Partial<MediaConfig>;
      try {
        m = JSON.parse(data);
      } catch {
        return;
      }
      if (m.type === "config") this.onConfig(m as MediaConfig);
      else if (m.type === "eos") this.events.onEnded("eos");
      return;
    }
    if (!(data instanceof ArrayBuffer) || data.byteLength < 1) return;
    const u8 = new Uint8Array(data);
    this.bytesReceived += data.byteLength;
    if (u8[0] === MSG_INIT) {
      // A (re)sent init segment goes ahead of any queued fragment; the next fragment must be a keyframe.
      this.queue = this.queue.filter((c) => c.init);
      this.queue.push({ data: u8.subarray(1), init: true, keyTime: null, hasFrame: false });
      this.waitingKey = true;
      this.pump();
    } else if (u8[0] === MSG_FRAME && data.byteLength > 6) {
      const key = u8[1] !== 0;
      const fid = ((u8[2] << 24) | (u8[3] << 16) | (u8[4] << 8) | u8[5]) >>> 0; // u32 BE, no DataView per frame
      this.framesReceived++;
      if (this.waitingKey && !key) return;
      this.waitingKey = false;
      this.queue.push({ data: u8.subarray(6), init: false, keyTime: key ? fid / this.fps : null, hasFrame: true });
      if (this.queue.length > 2 * this.fps) {
        // Appends are not keeping up: drop to the next keyframe (the server does the same at 8 frames).
        const n = this.queue.length;
        this.queue = this.queue.filter((c) => c.init);
        this.waitingKey = true;
        this.events.log("live: queue overflow, dropping to the next keyframe", { dropped: n - this.queue.length });
        return;
      }
      this.pump();
    }
  }

  private onConfig(cfg: MediaConfig): void {
    if (this.config) {
      if (cfg.codec_string && cfg.codec_string !== this.config.codec_string) this.fail("config-changed", cfg);
      return;
    }
    this.config = cfg;
    const mime = mimeFor(cfg.codec || this.attach.codec, cfg.codec_string);
    if (!mseSupported(mime)) {
      this.fail("unsupported-codec", mime);
      return;
    }
    const ms = new MediaSource();
    this.ms = ms;
    this.objectUrl = URL.createObjectURL(ms);
    ms.addEventListener(
      "sourceopen",
      () => {
        if (this.destroyed || this.ms !== ms) return;
        try {
          const sb = ms.addSourceBuffer(mime);
          sb.mode = "segments";
          sb.addEventListener("updateend", this.onUpdateEnd);
          sb.addEventListener("error", () => this.fail("sourcebuffer-error"));
          this.sb = sb;
        } catch (e) {
          this.fail("addSourceBuffer-failed", String(e));
          return;
        }
        this.pump();
      },
      { once: true },
    );
    this.video.src = this.objectUrl;
  }

  // -- appends -------------------------------------------------------------------------------------

  private pump(): void {
    const sb = this.sb;
    if (this.destroyed || this.failed || !sb || sb.updating || !this.queue.length || this.inflight) return;
    if (this.ms?.readyState !== "open") {
      this.fail("mediasource-not-open", this.ms?.readyState);
      return;
    }
    // Steady state: one queued fragment, appended as the socket message's own view (no copy; LIVE_V1
    // §2.2 rule 11). Only when appends have fallen behind are the queued fragments joined into one.
    const batch = this.queue.splice(0, this.queue.length);
    let buf: Uint8Array;
    if (batch.length === 1) {
      buf = batch[0].data;
    } else {
      buf = new Uint8Array(batch.reduce((n, c) => n + c.data.byteLength, 0));
      let o = 0;
      for (const c of batch) {
        buf.set(c.data, o);
        o += c.data.byteLength;
      }
    }
    this.inflight = batch;
    try {
      sb.appendBuffer(buf);
    } catch (e) {
      this.inflight = null;
      if ((e as DOMException)?.name === "QuotaExceededError" && !this.video.error) {
        this.queue.unshift(...batch);
        if (!this.evict(true)) {
          this.queue = this.queue.filter((c) => c.init);
          this.waitingKey = true;
          this.events.log("live: quota exceeded, dropping to the next keyframe");
        }
        return;
      }
      this.fail("appendBuffer-failed", String(e));
    }
  }

  private onUpdateEnd = (): void => {
    if (this.destroyed || this.failed || !this.sb) return;
    const now = performance.now();
    if (this.inflight) {
      for (const c of this.inflight) if (c.keyTime !== null) this.keyTimes.push(c.keyTime);
      if (this.inflight.some((c) => c.hasFrame)) this.lastAppendAt = now;
      if (this.keyTimes.length > 1000) this.keyTimes.splice(0, this.keyTimes.length - 1000);
      this.inflight = null;
    }
    const b = this.sb.buffered;
    const end = b.length ? b.end(b.length - 1) : -1;
    if (end > this.lastEnd) {
      this.lastEnd = end;
      this.lastGrowthAt = now;
    }
    if (!this.started && b.length) {
      const i = b.length - 1;
      if (b.end(i) - b.start(i) >= this.targetS) this.begin(Math.max(b.start(i), b.end(i) - this.targetS));
    }
    this.evict(false);
    this.pump();
  };

  private begin(at: number): void {
    const v = this.video;
    this.started = true;
    v.playbackRate = 1.0;
    v.currentTime = at;
    this.events.onStarted();
    v.play().catch((e) => {
      if (!this.destroyed) this.fail("play-failed", String(e));
    });
  }

  /**
   * Remove played data, cutting just before a keyframe: MSE extends a remove() to the next random-access
   * point, so cutting elsewhere would also take the GOP being played (lab finding, client.js evict()).
   */
  private evict(force: boolean): boolean {
    const sb = this.sb;
    if (!sb || sb.updating) return false;
    const b = sb.buffered;
    if (!b.length) return false;
    const ct = this.video.currentTime;
    if (!force && b.start(0) >= ct - EVICT_KEEP_S) return false;
    let k: number | null = null;
    for (const t of this.keyTimes) {
      if (t <= ct - 1) k = t;
      else break;
    }
    if (k === null) return false;
    const end = k - 0.5 / this.fps;
    if (end <= b.start(0)) return false;
    try {
      sb.remove(0, end);
    } catch {
      return false;
    }
    this.keyTimes = this.keyTimes.filter((t) => t >= k!);
    return true;
  }

  // -- playback ------------------------------------------------------------------------------------

  private onVideoFrame = (now: DOMHighResTimeStamp, meta: VideoFrameCallbackMetadata): void => {
    if (this.destroyed) return;
    this.rvfcHandle = this.video.requestVideoFrameCallback(this.onVideoFrame);
    this.presented = meta.presentedFrames;
    this.lastPresentedAt = performance.now();
    this.lastMediaTime = meta.mediaTime;
    try {
      this.events.onFrame(now, meta);
    } catch (e) {
      this.events.log("live: frame handler threw", String(e));
    }
  };

  /** Gap skip, edge jump, stuck-parser detection and the stall tick. rVFC never fires while stuck, so this runs on a timer. */
  private watch = (): void => {
    if (this.destroyed) return;
    const now = performance.now();
    try {
      this.events.onTick(now);
    } catch (e) {
      this.events.log("live: tick handler threw", String(e));
    }
    if (this.destroyed || this.failed || !this.sb) return;
    if (this.lastAppendAt !== null && this.lastAppendAt - this.lastGrowthAt > NO_PROGRESS_MS) {
      this.fail("mse-no-progress", { end: this.lastEnd });
      return;
    }
    if (!this.started) return;
    const v = this.video;
    const b = this.sb.buffered;
    if (!b.length || v.seeking) return;
    const ct = v.currentTime;
    let inside = false;
    let next: number | null = null;
    for (let i = 0; i < b.length; i++) {
      if (ct >= b.start(i) && ct < b.end(i)) inside = true;
      if (b.start(i) > ct && next === null) next = b.start(i);
    }
    if (next !== null && (!inside || v.readyState < HTMLMediaElement.HAVE_FUTURE_DATA)) {
      // Chrome does not play across a hole: skip to the next range.
      const last = b.length - 1;
      v.currentTime = Math.max(next, b.end(last) - this.targetS);
      this.gapSkips++;
      this.aheadSince = null;
      this.events.log("live: gap skip", { from: ct, to: v.currentTime });
      return;
    }
    const end = b.end(b.length - 1);
    const ahead = end - ct;
    if (this.targetS > 0 && ahead > this.targetS + EDGE_MARGIN_S) {
      if (this.aheadSince === null) this.aheadSince = now;
      else if (now - this.aheadSince >= EDGE_HOLD_MS) {
        v.currentTime = end - this.targetS; // hard jump to the live edge; never a speed-up
        this.edgeJumps++;
        this.aheadSince = null;
        this.events.log("live: edge jump", { from: ct, to: v.currentTime, ahead_ms: Math.round(ahead * 1000) });
      }
    } else {
      this.aheadSince = null;
    }
  };

  private sendStats = (): void => {
    const ws = this.ws;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    try {
      ws.send(
        JSON.stringify({
          type: "stats",
          presented: this.presented,
          dropped: this.dropped(),
          buffered_ms: Math.round(this.bufferedMs()),
          edge_jumps: this.edgeJumps,
          stalls: this.stalls,
          ...(this.role ? { role: this.role } : {}),
        }),
      );
    } catch {
      /* closing */
    }
  };

  private onWaiting = (): void => {
    // Count stalls once frames are flowing (the start-up `waiting` before the first frame is not one).
    if (this.started && this.lastPresentedAt !== null && !this.destroyed) this.stalls++;
  };

  private onVideoError = (): void => {
    const err = this.video.error;
    if (!this.destroyed && err) this.fail("video-error", { code: err.code, message: err.message });
  };

  private fail(reason: string, detail?: unknown): void {
    if (this.failed || this.destroyed) return;
    this.failed = true;
    this.queue = [];
    this.events.log(`live: fatal ${reason}`, detail);
    this.events.onFatal(reason, detail);
  }
}
