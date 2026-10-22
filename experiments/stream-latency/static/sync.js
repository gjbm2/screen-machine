// Shared helpers: high-resolution epoch clock + skew-aware mapping to the server clock.

export const nowEpoch = () => performance.timeOrigin + performance.now();
export const toEpoch = (domHighResTs) => performance.timeOrigin + domHighResTs;

export const fmtUtc = (ms) => new Date(ms).toISOString().slice(11, 23); // HH:MM:SS.mmm (UTC)

const WINDOW_MS = 20000;   // samples kept for the fit
const FIT_MIN_N = 5;       // envelope samples needed for a skew fit ...
const FIT_MIN_SPAN = 2000; // ... spanning at least this long (ms)

// NTP-style sync with a rate term. The server clock does not run at our rate (WSL's CLOCK_MONOTONIC was
// measured ~2 % fast), so a single offset is stale within seconds. Keep the last 20 s of ping samples,
// take the low-RTT envelope (queueing only ever adds delay) and least-squares fit
//   (s - c_mid) = a + b * (c_mid - c_ref)
// then map client time t -> server time t + a + b*(t - c_ref). beelink/fb_probe.py runs the same algorithm.
// Samples are cleared on every new connection (start) and whenever the server's epoch id changes
// (a restarted server is a different clock).
export class ClockSync {
  constructor() {
    this.id = 0;
    this.reset(null);
  }

  reset(epoch) {
    this.epoch = epoch;   // server instance id the samples belong to
    this.samples = [];    // {c0, c1, s}
    this.a = null;        // offset at c_ref (ms); null = no lock
    this.b = 0;           // rate (server ms per client ms - 1)
    this.cRef = 0;
    this.rtt = null;      // min RTT of the envelope
    this.ppm = null;
    this.resid = null;    // max |residual| of the envelope fit (ms)
    this.n = 0;           // envelope size
    this.fitted = false;  // true once the envelope supports a skew fit (else: offset only, b = 0)
    this.since = nowEpoch(); // when these samples started (start / epoch change)
    this.lastRtt = null;
  }

  get ready() { return this.a != null; }

  // config/pong carry the server's epoch id: a different one means a different server clock
  setEpoch(epoch) {
    if (epoch != null && epoch !== this.epoch) this.reset(epoch);
  }

  ping(ws) {
    if (ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({ type: "ping", id: ++this.id, c0: nowEpoch() }));
  }

  pong(msg) {
    const c1 = nowEpoch();
    this.setEpoch(msg.epoch);
    if (typeof msg.c0 !== "number" || typeof msg.s !== "number" || c1 < msg.c0) return;
    this.lastRtt = c1 - msg.c0;
    this.samples.push({ c0: msg.c0, c1, s: msg.s });
    const cutoff = c1 - WINDOW_MS;
    while (this.samples.length && this.samples[0].c1 < cutoff) this.samples.shift();
    this.fit();
  }

  fit() {
    let minRtt = Infinity;
    for (const x of this.samples) minRtt = Math.min(minRtt, x.c1 - x.c0);
    if (!Number.isFinite(minRtt)) return;
    const lim = minRtt + Math.max(1, 0.5 * minRtt);
    const env = [];
    for (const x of this.samples) {
      const rtt = x.c1 - x.c0;
      if (rtt <= lim) { const c = (x.c0 + x.c1) / 2; env.push({ c, o: x.s - c, rtt }); }
    }
    const span = env[env.length - 1].c - env[0].c; // samples arrive in time order
    let a, b = 0, cRef;
    const fitted = env.length >= FIT_MIN_N && span >= FIT_MIN_SPAN;
    if (fitted) {
      cRef = env.reduce((s, e) => s + e.c, 0) / env.length;
      const mo = env.reduce((s, e) => s + e.o, 0) / env.length;
      let sxx = 0, sxy = 0;
      for (const e of env) { sxx += (e.c - cRef) ** 2; sxy += (e.c - cRef) * (e.o - mo); }
      b = sxx > 0 ? sxy / sxx : 0;
      a = mo; // centred regressor: the fitted line passes through the means
    } else {
      let best = env[0];
      for (const e of env) if (e.rtt < best.rtt) best = e;
      a = best.o;
      cRef = best.c;
    }
    let resid = 0;
    for (const e of env) resid = Math.max(resid, Math.abs(e.o - (a + b * (e.c - cRef))));
    Object.assign(this, { a, b, cRef, rtt: minRtt, ppm: b * 1e6, resid, n: env.length, fitted });
  }

  // client epoch ms -> server clock ms (null until the first pong)
  toServer(t) {
    return this.a == null || t == null ? null : t + this.a + this.b * (t - this.cRef);
  }

  serverNow() { return this.toServer(nowEpoch()); }

  // current offset (server - client), for display
  get offset() { return this.a == null ? null : this.a + this.b * (nowEpoch() - this.cRef); }

  stats() {
    const r = (v, d) => (v == null ? null : +v.toFixed(d));
    return { rtt: r(this.rtt, 3), ppm: r(this.ppm, 1), resid: r(this.resid, 3), n: this.n };
  }

  // a new connection: forget everything, burst for a quick lock, then a steady cadence
  start(ws, periodMs = 250) {
    this.reset(null);
    let n = 0;
    const burst = setInterval(() => {
      this.ping(ws);
      if (++n >= 10) clearInterval(burst);
    }, 40);
    const timer = setInterval(() => this.ping(ws), periodMs);
    return () => { clearInterval(burst); clearInterval(timer); };
  }
}
