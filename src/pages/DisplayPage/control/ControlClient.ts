// One control WebSocket per page (LIVE_V1 §2.2 rule 1).
//   * envelope v1; `hello` is sent on every (re)connect;
//   * liveness: no inbound frame for 6 s => dead: drop it and reconnect;
//   * reconnect: full-jitter backoff random(0, min(10 s, 0.5 s·2^n)); n resets after 30 s connected;
//     at once on the browser's `online` event, or on close code 4008 (server backlog/rate) if that
//     connection had been up for 5 s (otherwise backoff: never a tight loop, rule 11);
//   * `hb {}` whenever nothing has been sent for 2 s.
import { envelope, parseEnvelope, type Envelope, type Hello } from "./protocol";

const DEAD_AFTER_MS = 6_000;
const HB_IDLE_MS = 2_000;
const BACKOFF_BASE_MS = 500;
const BACKOFF_CAP_MS = 10_000;
const RESET_BACKOFF_AFTER_MS = 30_000;
const TICK_MS = 500;
/** A 4008 close reconnects at once only if the socket had been open this long. */
const IMMEDIATE_AFTER_UP_MS = 5_000;

export interface ControlClientOptions {
  url: string;
  hello: () => Hello;
  onMessage: (msg: Envelope) => void;
  onStatus: (status: "connecting" | "up" | "down") => void;
  log?: (msg: string, detail?: unknown) => void;
}

export class ControlClient {
  private ws: WebSocket | null = null;
  private stopped = true;
  private attempt = 0;
  private openedAt = 0;
  private lastInbound = 0;
  private lastOutbound = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private tickTimer: ReturnType<typeof setInterval> | null = null;
  /** Number of (re)connections after the first; reported in kiosk.state. */
  reconnects = 0;
  private connects = 0;

  constructor(private opts: ControlClientOptions) {}

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
    window.addEventListener("online", this.onOnline);
    this.tickTimer = setInterval(this.tick, TICK_MS);
    this.connect();
  }

  /** Close for good (1000 "page unmounted" by default; 1001 "reloading" before a timed reload). */
  stop(code = 1000, reason = "page unmounted"): void {
    this.stopped = true;
    window.removeEventListener("online", this.onOnline);
    if (this.tickTimer) clearInterval(this.tickTimer);
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.tickTimer = this.reconnectTimer = null;
    this.drop(code, reason, false);
  }

  get isOpen(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  /** Send one message; returns false (and drops it) when the socket is not open. */
  send<T>(type: string, data: T): boolean {
    const ws = this.ws;
    if (!ws || ws.readyState !== WebSocket.OPEN) return false;
    try {
      ws.send(envelope(type, data));
      this.lastOutbound = performance.now();
      return true;
    } catch (e) {
      this.opts.log?.("send failed", String(e));
      return false;
    }
  }

  private connect(): void {
    if (this.stopped) return;
    this.reconnectTimer = null;
    this.opts.onStatus("connecting");
    let ws: WebSocket;
    try {
      ws = new WebSocket(this.opts.url);
    } catch (e) {
      this.opts.log?.("bad control url", String(e));
      this.scheduleReconnect();
      return;
    }
    this.ws = ws;
    // A connect that never opens counts against the same 6 s liveness window.
    this.lastInbound = performance.now();
    ws.onopen = () => {
      if (this.ws !== ws) return;
      this.openedAt = this.lastInbound = performance.now();
      if (this.connects++ > 0) this.reconnects++;
      this.opts.onStatus("up");
      this.send("hello", this.opts.hello());
    };
    ws.onmessage = (ev) => {
      if (this.ws !== ws) return;
      this.lastInbound = performance.now();
      const msg = parseEnvelope(ev.data);
      if (msg) this.opts.onMessage(msg);
    };
    ws.onclose = (ev) => {
      if (this.ws !== ws) return;
      this.opts.log?.("control socket closed", { code: ev.code, reason: ev.reason });
      this.ws = null;
      const upFor = this.openedAt ? performance.now() - this.openedAt : 0;
      this.onDown(ev.code === 4008 && upFor >= IMMEDIATE_AFTER_UP_MS);
    };
    ws.onerror = () => {
      /* onclose follows */
    };
  }

  /** Detach and close the current socket without waiting for its close event. */
  private drop(code: number, reason: string, reconnect: boolean, immediately = false): void {
    const ws = this.ws;
    this.ws = null;
    if (ws) {
      ws.onopen = ws.onmessage = ws.onclose = ws.onerror = null;
      try {
        ws.close(code, reason);
      } catch {
        /* already closing */
      }
    }
    if (reconnect) this.onDown(immediately);
  }

  private onDown(immediately: boolean): void {
    if (this.openedAt && performance.now() - this.openedAt >= RESET_BACKOFF_AFTER_MS) this.attempt = 0;
    this.openedAt = 0;
    this.opts.onStatus("down");
    if (immediately) {
      if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
      this.reconnectTimer = setTimeout(() => this.connect(), 0);
    } else {
      this.scheduleReconnect();
    }
  }

  private scheduleReconnect(): void {
    if (this.stopped || this.reconnectTimer) return;
    const cap = Math.min(BACKOFF_CAP_MS, BACKOFF_BASE_MS * 2 ** this.attempt);
    this.attempt = Math.min(this.attempt + 1, 16);
    const delay = Math.random() * cap; // full jitter
    this.reconnectTimer = setTimeout(() => this.connect(), delay);
  }

  private onOnline = (): void => {
    if (this.stopped || this.isOpen) return;
    this.drop(1000, "online", true, true);
  };

  private tick = (): void => {
    if (this.stopped || !this.ws) return;
    const now = performance.now();
    if (now - this.lastInbound > DEAD_AFTER_MS) {
      this.opts.log?.("control socket dead (no inbound frame for 6 s)");
      this.drop(4000, "dead", true);
      return;
    }
    if (this.isOpen && now - this.lastOutbound >= HB_IDLE_MS) this.send("hb", {});
  };
}
