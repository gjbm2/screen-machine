// The show bus's notes for the live music screen (LiveMusicView.tsx): what's held, what's still rising, the sustain
// pedal, each applied `delayMs` after it arrived (the stream's delay behind the room). music.notes snapshots heal a
// missed note-off (and strike a key held before the screen joined); a new bus session releases everything.

/** Seconds a note takes to rise from its key to the top. */
export const RISE_S = 5;
const MAX_QUEUE = 4000;
const MAX_BARS = 800;
export const NARROW: [number, number] = [36, 96]; // C2..C7: the keys most music uses, wider keys on a phone
const FULL: [number, number] = [21, 108]; // A0..C8
const BLACK = new Set([1, 3, 6, 8, 10]);
export const isBlack = (p: number) => BLACK.has(((p % 12) + 12) % 12);

type Ev =
  | { at: number; kind: "on"; pitch: number; vel: number }
  | { at: number; kind: "off"; pitch: number }
  | { at: number; kind: "pedal"; value: number }
  | { at: number; kind: "keys"; keys: [number, number][] }
  | { at: number; kind: "reset" };

interface Bar {
  pitch: number;
  vel: number;
  start: number;
  end: number | null;
}

/** The bus's notes for the screen: what's held, what's still rising, the sustain pedal. Times are performance.now(). */
export class NoteFeed {
  private queue: Ev[] = [];
  private sess: string | null = null;
  readonly held = new Map<number, Bar>();
  bars: Bar[] = [];
  damper = 0;
  range: [number, number] = NARROW;
  /** performance.now() of the last note-on drawn. */
  lastOn = 0;

  /** One bus message, to be drawn `delayMs` from now. */
  push(m: { type?: string; data?: Record<string, unknown> }, delayMs: number) {
    const d = m.data ?? {};
    const at = performance.now() + Math.max(0, delayMs);
    const sess = typeof d.sess === "string" ? d.sess : null;
    if (sess && this.sess && sess !== this.sess) this.queue.push({ at, kind: "reset" });
    if (sess) this.sess = sess;
    if (m.type === "music.note" && typeof d.pitch === "number") {
      const vel = typeof d.vel === "number" ? d.vel : 64;
      this.queue.push(d.on === true ? { at, kind: "on", pitch: d.pitch, vel } : { at, kind: "off", pitch: d.pitch });
    } else if (m.type === "music.pedal" && d.pedal === "damper" && typeof d.value === "number") {
      this.queue.push({ at, kind: "pedal", value: d.value });
    } else if (m.type === "music.notes" && Array.isArray(d.keys)) {
      const keys = (d.keys as unknown[])
        .filter((k): k is number[] => Array.isArray(k) && typeof k[0] === "number")
        .map((k): [number, number] => [k[0], typeof k[1] === "number" ? k[1] : 64]);
      this.queue.push({ at, kind: "keys", keys });
    }
    if (this.queue.length > MAX_QUEUE) this.queue.splice(0, this.queue.length - MAX_QUEUE);
    this.advance(performance.now());
  }

  private strike(pitch: number, vel: number, at: number) {
    this.release(pitch, at);
    const b = { pitch, vel, start: at, end: null };
    this.held.set(pitch, b);
    this.bars.push(b);
    this.lastOn = at;
    if (pitch < this.range[0] || pitch > this.range[1]) this.range = FULL;
  }

  private release(pitch: number, at: number) {
    const b = this.held.get(pitch);
    if (b) {
      b.end = at;
      this.held.delete(pitch);
    }
  }

  /** Apply what's due by `now`, and forget what has risen off the top. */
  advance(now: number) {
    let i = 0;
    for (; i < this.queue.length && this.queue[i].at <= now; i++) {
      const e = this.queue[i];
      if (e.kind === "on") this.strike(e.pitch, e.vel, e.at);
      else if (e.kind === "off") this.release(e.pitch, e.at);
      else if (e.kind === "pedal") this.damper = e.value;
      else if (e.kind === "reset") [...this.held.keys()].forEach((p) => this.release(p, e.at));
      else {
        const down = new Set(e.keys.map(([p]) => p));
        [...this.held.keys()].filter((p) => !down.has(p)).forEach((p) => this.release(p, e.at));
        e.keys.filter(([p]) => !this.held.has(p)).forEach(([p, v]) => this.strike(p, v, e.at));
      }
    }
    if (i) this.queue.splice(0, i);
    const gone = now - RISE_S * 1000 - 500;
    if (this.bars.length && (this.bars[0].end ?? Infinity) < gone) this.bars = this.bars.filter((b) => b.end === null || b.end >= gone);
    if (this.bars.length > MAX_BARS) this.bars = this.bars.slice(-MAX_BARS);
  }
}
