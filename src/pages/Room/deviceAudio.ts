// PC22's piano played on this device with a buffer the page holds itself (owner, 23 Oct: "alignment between midi and
// 'this device' audio is utterly terrible ... when I hit 'pause' it pauses immediately ... rethink how to ensure
// sync"). An <audio> element on an MP3 stream buffers as much as the browser likes (seconds on an iPad) and says
// little about what it is playing; here the sound comes as raw PCM over a WebSocket (GET …/piano/live.ws, show_service
// piano_live.py), each 21 ms piece stamped with the PC22 sound time it carries, and an AudioWorklet plays it:
//   - a small, fixed buffer: playback starts once TARGET_S is held, and anything beyond TARGET_S + SLACK_S is
//     skipped, so the delay behind the room stays put (about the server's 0.35 s plus TARGET_S plus the output's own)
//   - exact: the worklet says which stamped frame it is playing, so the page knows the sound time being heard now
//     (heardT), how far behind PC22 that is (lag, given to deviceLag.ts for every view that draws the music as it
//     sounds) and, from the server's piece map, the place in the piece being heard (heardPiece: the score's light)
//   - hush(): silence at once (the buffer dropped, what arrives for a moment after ignored: notes already on their
//     way, the room's tail), for Pause; unhush() for the next Play
// PC22's clock is learned from the stamps (each message's host_now: the largest host_now - local time seen is the
// closest to the truth, the network only ever adds). Reconnects by itself while on (1 s, doubling to 8 s).
//
//   startDeviceAudio(url)  in a tap (the AudioContext needs one): url is …/show/api/v1/piano/live.ws?instrument=…
//   stopDeviceAudio()      off: the socket closed, the context suspended
//   useDeviceAudio()       {status, lag} for a component; deviceAudioSnap() outside React

import { useSyncExternalStore } from "react";
import { setDeviceLag } from "./deviceLag";

const SR = 48000;
/** Held before playing (and after a gap): enough for Wi-Fi's jitter. */
const TARGET_S = 0.15;
/** Held beyond TARGET_S + this: the oldest is skipped, so the delay can't grow. */
const SLACK_S = 0.25;
/** After hush(), audio carrying sound times up to this long after the tap is not played (notes in flight, the tail). */
const HUSH_S = 1.5;
const REPORT_MS = 100;
const RETRY_FIRST_MS = 1000;
const RETRY_CAP_MS = 8000;
const MAPS_KEPT = 20;

export type DeviceAudioStatus = "off" | "connecting" | "buffering" | "playing" | "quiet" | "failed";

export interface DeviceAudioSnap {
  status: DeviceAudioStatus;
  /** Seconds behind PC22 (what's heard now vs PC22's sound time now), while playing. */
  lag: number | null;
  /** The instrument the server is streaming. */
  instrument: string | null;
}

interface PieceMap {
  piece: string;
  pos_s: number | null;
  at: number;
  speed: number;
  state: string;
}

const WORKLET = `
class Player extends AudioWorkletProcessor {
  constructor(o) {
    super();
    const p = o.processorOptions;
    this.ratio = p.sr / sampleRate;      // stream frames per output frame
    this.target = p.target * p.sr;
    this.slack = p.slack * p.sr;
    this.q = [];                         // {t, d: Int16Array, off}
    this.held = 0;                       // frames queued
    this.frac = 0;
    this.playing = false;
    this.muteT = -Infinity;
    this.gain = 1;
    this.lastT = null;
    this.since = 0;
    this.port.onmessage = (e) => {
      const m = e.data;
      if (m.chunk) {
        const t = m.t;
        if (t + m.chunk.length / 2 / p.sr <= this.muteT) return;
        this.q.push({ t, d: m.chunk, off: 0 });
        this.held += m.chunk.length / 2;
        while (this.held > this.target + this.slack && this.q.length > 1) {   // too much: skip the oldest
          const c = this.q.shift();
          this.held -= c.d.length / 2 - c.off;
        }
      } else if (m.hush !== undefined) {
        this.muteT = m.hush;
        this.q = [];
        this.held = 0;
        this.playing = false;
      } else if (m.unhush) {
        this.muteT = -Infinity;
      }
    };
  }
  process(_in, outputs) {
    const out = outputs[0];
    const L = out[0], R = out[1] || out[0];
    const n = L.length;
    if (!this.playing && this.held >= this.target) this.playing = true;
    let t = null;
    for (let i = 0; i < n; i++) {
      if (!this.playing || !this.q.length) {
        L[i] = 0; R[i] = 0;
        continue;
      }
      let c = this.q[0];
      if (t === null) t = c.t + c.off / ${SR};
      const a = c.off;
      L[i] = (c.d[2 * a] / 32768) * this.gain;
      R[i] = (c.d[2 * a + 1] / 32768) * this.gain;
      this.frac += this.ratio;
      while (this.frac >= 1) {
        this.frac -= 1;
        c.off++;
        this.held--;
        if (c.off * 2 >= c.d.length) {
          this.q.shift();
          if (!this.q.length) { this.playing = false; break; }   // ran dry: gather TARGET again
          c = this.q[0];
        }
      }
    }
    this.since += n;
    if (this.since >= sampleRate * ${REPORT_MS} / 1000) {
      this.since = 0;
      this.port.postMessage({ t, at: currentTime, playing: this.playing, held: this.held / ${SR} });
    }
    return true;
  }
}
registerProcessor("piano-pcm-player", Player);
`;

let snap: DeviceAudioSnap = { status: "off", lag: null, instrument: null };
const listeners = new Set<() => void>();
let ctx: AudioContext | null = null;
let node: AudioWorkletNode | null = null;
let ws: WebSocket | null = null;
let url: string | null = null;
let on = false;
let retryMs = RETRY_FIRST_MS;
let retryTimer: number | undefined;
let offset: number | null = null; // PC22's clock minus ours (seconds), the largest seen lately
let offsetAt = 0;
let report: { t: number | null; at: number; playing: boolean } | null = null;
let quiet = false;
const maps: PieceMap[] = [];

function set(p: Partial<DeviceAudioSnap>) {
  const next = { ...snap, ...p };
  if (next.status === snap.status && next.lag === snap.lag && next.instrument === snap.instrument) return;
  snap = next;
  listeners.forEach((l) => l());
}

const nowS = () => performance.now() / 1000;

/** PC22's CLOCK_MONOTONIC now, as far as this page can tell, or null before the first message. */
export function hostNow(): number | null {
  return offset === null ? null : nowS() + offset;
}

/** The PC22 sound time being heard now (output latency included), or null while nothing plays. */
export function heardT(): number | null {
  if (!ctx || !report || !report.playing || report.t === null) return null;
  const outLat = (ctx.outputLatency || 0) + (ctx.baseLatency || 0);
  return report.t + (ctx.currentTime - report.at) - outLat;
}

/** The piece and the place in it (its own seconds) being heard now, from the server's maps; null when unknown. */
export function heardPiece(): { piece: string; pos: number; playing: boolean } | null {
  const t = heardT();
  if (t === null || !maps.length) return null;
  let m = maps[0];
  for (const x of maps) if (x.at <= t) m = x;
  if (m.pos_s === null) return null;
  const playing = m.state === "playing";
  return { piece: m.piece, pos: Math.max(0, m.pos_s + (playing ? (t - m.at) * m.speed : 0)), playing };
}

/** Silence now (Pause): what's held is dropped and what arrives for HUSH_S after is ignored. */
export function hushDeviceAudio() {
  const h = hostNow();
  node?.port.postMessage({ hush: h === null ? Number.POSITIVE_INFINITY : h + HUSH_S });
}

/** The next Play: what arrives is played again. */
export function unhushDeviceAudio() {
  node?.port.postMessage({ unhush: true });
}

export const deviceAudioSnap = (): DeviceAudioSnap => snap;

function subscribe(l: () => void) {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
}

/** Called on every change, outside React (listenStream.ts); returns the unsubscribe. */
export const subscribeDeviceAudio = subscribe;

/** {status, lag, instrument} for a component. */
export function useDeviceAudio(): DeviceAudioSnap {
  return useSyncExternalStore(subscribe, deviceAudioSnap, deviceAudioSnap);
}

async function ensureContext(): Promise<AudioContext> {
  if (!ctx) {
    try {
      // iOS: keep playing with the ring/silent switch on (Safari 16.4+)
      const s = (navigator as unknown as { audioSession?: { type: string } }).audioSession;
      if (s) s.type = "playback";
    } catch {
      /* older browser */
    }
    const AC = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    ctx = new AC({ sampleRate: SR, latencyHint: "interactive" });
    const blob = URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" }));
    await ctx.audioWorklet.addModule(blob);
    node = new AudioWorkletNode(ctx, "piano-pcm-player", {
      numberOfInputs: 0,
      outputChannelCount: [2],
      processorOptions: { sr: SR, target: TARGET_S, slack: SLACK_S },
    });
    node.port.onmessage = (e) => {
      const m = e.data as { t: number | null; at: number; playing: boolean };
      report = m;
      if (!on) return;
      if (!m.playing) {
        if (snap.status === "playing" || snap.status === "quiet") set({ status: quiet ? "quiet" : "buffering" });
        return;
      }
      set({ status: quiet ? "quiet" : "playing" });
    };
    node.connect(ctx.destination);
  }
  if (ctx.state !== "running") await ctx.resume();
  return ctx;
}

function wsUrl(u: string): string {
  const x = new URL(u, window.location.href);
  x.protocol = x.protocol === "https:" ? "wss:" : "ws:";
  return x.toString();
}

function connect() {
  if (!on || !url) return;
  window.clearTimeout(retryTimer);
  set({ status: snap.status === "playing" ? "buffering" : "connecting" });
  const sock = new WebSocket(wsUrl(url));
  sock.binaryType = "arraybuffer";
  ws = sock;
  sock.onmessage = (e) => {
    if (ws !== sock) return;
    if (typeof e.data === "string") {
      let m: Record<string, unknown>;
      try {
        m = JSON.parse(e.data);
      } catch {
        return;
      }
      if (typeof m.host_now === "number") learnClock(m.host_now);
      const hello = m.hello as { instrument?: string; host_now?: number } | undefined;
      if (hello) {
        retryMs = RETRY_FIRST_MS;
        if (typeof hello.host_now === "number") learnClock(hello.host_now);
        set({ instrument: hello.instrument ?? null });
      }
      const map = m.map as PieceMap | undefined;
      if (map && typeof map.at === "number") {
        maps.push(map);
        if (maps.length > MAPS_KEPT) maps.shift();
      }
      if (typeof m.quiet === "boolean") {
        quiet = m.quiet;
        if (quiet && snap.status !== "playing") set({ status: "quiet" });
      }
      return;
    }
    const buf = e.data as ArrayBuffer;
    if (buf.byteLength <= 16) return;
    const head = new DataView(buf, 0, 16);
    const t = head.getFloat64(0, true);
    learnClock(head.getFloat64(8, true));
    const chunk = new Int16Array(buf.slice(16));
    node?.port.postMessage({ chunk, t }, [chunk.buffer]);
    if (snap.status === "connecting") set({ status: "buffering" });
  };
  sock.onclose = () => {
    if (ws !== sock) return;
    ws = null;
    if (!on) return;
    set({ status: "connecting" });
    retryTimer = window.setTimeout(connect, retryMs);
    retryMs = Math.min(RETRY_CAP_MS, retryMs * 2);
  };
  sock.onerror = () => sock.close();
}

function learnClock(host: number) {
  const est = host - nowS();
  const now = nowS();
  // the largest estimate is the least delayed; let it relax slowly (clock drift, a stale best) after 20 s
  if (offset === null || est > offset || now - offsetAt > 20) {
    offset = est;
    offsetAt = now;
  }
}

let lagTimer: number | undefined;
function trackLag() {
  window.clearInterval(lagTimer);
  lagTimer = window.setInterval(() => {
    const h = hostNow();
    const t = heardT();
    const lag = h !== null && t !== null ? Math.max(0, h - t) : null;
    setDeviceLag(lag);
    set({ lag: lag === null ? null : Math.round(lag * 100) / 100 });
  }, 250);
}

/** Start (in a tap): play the piano from ``streamUrl`` (…/piano/live.ws?instrument=&device=&name=). */
export async function startDeviceAudio(streamUrl: string): Promise<void> {
  const again = on && url === streamUrl && ws !== null;
  url = streamUrl;
  on = true;
  try {
    await ensureContext();
  } catch {
    set({ status: "failed" });
    return;
  }
  if (again) return;
  ws?.close();
  ws = null;
  maps.length = 0;
  retryMs = RETRY_FIRST_MS;
  connect();
  trackLag();
}

/** Off: the socket closed, the sound silent at once. */
export function stopDeviceAudio() {
  on = false;
  window.clearTimeout(retryTimer);
  window.clearInterval(lagTimer);
  const s = ws;
  ws = null;
  s?.close();
  node?.port.postMessage({ hush: Number.POSITIVE_INFINITY });
  node?.port.postMessage({ unhush: true });
  void ctx?.suspend().catch(() => undefined);
  report = null;
  maps.length = 0;
  setDeviceLag(null);
  set({ status: "off", lag: null });
}

/** The context is still allowed to play (a phone may suspend it: then it needs a tap). */
export function deviceAudioNeedsTap(): boolean {
  return on && !!ctx && ctx.state !== "running";
}
