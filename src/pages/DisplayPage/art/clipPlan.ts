// When a still's own clip plays over it (owner, 24 Nov: at rest the screen shows the still and no video is held; when
// it is time, a short fade to the clip, the clip plays with its ease rendered in, a fade back to the still, and the
// video is let go). The decisions only, with the clock and timers injected, so they are tested without a browser
// (clipPlan.test.ts); ClipLayer.tsx carries them out.
//
// - The first play waits a short random while after the clip arrives; after each play it rests for a random time in
//   the clip's own `rest_s` (longer for bigger movements, routes/clip_ease.py).
// - Nothing plays while the page is quiet (frozen, live) or hidden (a sleeping tab or screensaver): on coming back it
//   starts a fresh rest, never a backlog of missed plays.
// - A transition has the screen first: a play waiting for the motion slot (motionSlot.ts) is put off, and a playing
//   clip finishes before the transition starts.

export const FIRST_REST_S: [number, number] = [5, 20];
export const DEFAULT_REST_S: [number, number] = [35, 55];
/** If a play hasn't finished this long after its expected end, it is ended (a stuck decoder never holds the slot). */
export const PLAY_GUARD_S = 20;

export type ClipPhase = "none" | "resting" | "playing" | "stopping";

export interface ClipPlanHooks {
  /** Start a play (mount the video, fade in, play). Call `played()` when it has faded out and let go. */
  start(): void;
  /** End the play now, fading out over `fadeMs`. Call `played()` once it has gone. */
  stop(fadeMs: number): void;
  /** May a play start now? (the motion slot is free) */
  slotFree(): boolean;
}

export class ClipPlan {
  phase: ClipPhase = "none";
  private clip: { url: string; rest: [number, number]; play_s: number } | null = null;
  /** A video played once over the still instead of its clip (the controls panel: another clip, a transition). */
  private onceUrl: string | null = null;
  private quiet = false;
  private hidden = false;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private guard: ReturnType<typeof setTimeout> | null = null;

  private hooks: ClipPlanHooks;
  private random: () => number;

  constructor(hooks: ClipPlanHooks, random: () => number = Math.random) {
    this.hooks = hooks;
    this.random = random;
  }

  get url(): string | null {
    return this.clip?.url ?? null;
  }

  /** What the current play shows: a video played once, else the still's clip. */
  get playingUrl(): string | null {
    return this.onceUrl ?? this.clip?.url ?? null;
  }

  /** The still's clip (null: none). A new clip starts with the short first rest. */
  setClip(c: { url: string; rest_s?: [number, number] | null; play_s?: number | null } | null): void {
    if ((c?.url ?? null) === (this.clip?.url ?? null)) return;
    this.endPlay(600);
    this.clip = c ? { url: c.url, rest: validRest(c.rest_s) ?? DEFAULT_REST_S, play_s: Number(c.play_s) || 30 } : null;
    this.rest(FIRST_REST_S);
  }

  /** Frozen or live: a playing clip fades out quickly (the frozen screen is the still), nothing new starts. */
  setQuiet(q: boolean, fadeMs = 300): void {
    if (q === this.quiet) return;
    this.quiet = q;
    if (q) this.endPlay(fadeMs);
    else this.rest(FIRST_REST_S);
  }

  /** The tab is hidden (asleep): no plays; a fresh rest on waking. */
  setHidden(h: boolean): void {
    if (h === this.hidden) return;
    this.hidden = h;
    if (h) this.endPlay(0);
    else this.rest(FIRST_REST_S);
  }

  /** Can the controls panel's Play start it now? */
  canPlayNow(): boolean {
    return !!this.clip && this.phase === "resting" && !this.quiet && !this.hidden && this.hooks.slotFree();
  }

  /** The controls panel's Play: now, instead of after the rest. */
  playNow(): boolean {
    if (!this.canPlayNow()) return false;
    this.begin();
    return true;
  }

  /** Play `url` once over the still now (another of its clips, or a transition into or out of it): when nothing is
   *  playing, the page isn't quiet or hidden and no transition has the screen. The still's own clip then rests. */
  playOnce(url: string): boolean {
    if (this.phase === "playing" || this.phase === "stopping" || this.quiet || this.hidden || !this.hooks.slotFree()) return false;
    this.onceUrl = url;
    this.begin();
    return true;
  }

  /** The play has faded out and the video is gone. */
  played(): void {
    if (this.phase !== "playing" && this.phase !== "stopping") return;
    this.clearGuard();
    this.onceUrl = null;
    this.phase = "resting";
    this.rest(this.clip?.rest ?? DEFAULT_REST_S);
  }

  /** A transition wants the screen: a play that hasn't started waits until after it. */
  yieldTo(): void {
    if (this.phase === "resting") this.rest(FIRST_REST_S);
  }

  dispose(): void {
    this.clearTimer();
    this.clearGuard();
    this.clip = null;
    this.phase = "none";
  }

  private rest(range: [number, number]): void {
    this.clearTimer();
    if (this.phase === "playing" || this.phase === "stopping") return; // played() rests when it is over
    if (!this.clip || this.quiet || this.hidden) {
      this.phase = this.clip ? "resting" : "none";
      return;
    }
    this.phase = "resting";
    const s = range[0] + this.random() * Math.max(0, range[1] - range[0]);
    this.timer = setTimeout(() => this.due(), s * 1000);
  }

  private due(): void {
    this.timer = null;
    if (!this.clip || this.quiet || this.hidden || this.phase !== "resting") return;
    if (!this.hooks.slotFree()) {
      this.rest(FIRST_REST_S); // the screen is busy (a transition): try again after a short while
      return;
    }
    this.begin();
  }

  private begin(): void {
    this.clearTimer();
    this.phase = "playing";
    this.clearGuard();
    this.guard = setTimeout(() => this.endPlay(600), ((this.clip?.play_s ?? 30) + PLAY_GUARD_S) * 1000);
    this.hooks.start();
  }

  private endPlay(fadeMs: number): void {
    this.clearTimer();
    if (this.phase === "playing") {
      this.phase = "stopping";
      this.hooks.stop(fadeMs);
    }
  }

  private clearTimer(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
  }

  private clearGuard(): void {
    if (this.guard) clearTimeout(this.guard);
    this.guard = null;
  }
}

function validRest(r: unknown): [number, number] | null {
  if (!Array.isArray(r) || r.length !== 2) return null;
  const a = Number(r[0]);
  const b = Number(r[1]);
  return Number.isFinite(a) && Number.isFinite(b) && a >= 0 && b >= a ? [a, b] : null;
}
