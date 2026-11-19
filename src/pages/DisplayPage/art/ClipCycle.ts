// The clip base's slow-motion loop, ported from MediaDisplay.tsx so it can be held and resumed.
//
// Same timeline as today: seek to 0, primer at 0.065x for 1.5 s, ease to 0.6x over 2.5 s, play,
// ease back to 0.065x over 2.5 s, pause on the last frame for 4 s, cross-fade to the extracted
// first frame over 8 s, wait 2 s, restart. The restart happens under the first-frame overlay: the
// clip is seeked to frame 0 while the overlay still covers it, and the overlay only goes once that
// frame is on screen (owner, 29 Oct: "after it cross fades back to the start it then jumps before
// play begins again": the overlay used to fade off over the paused last frame, and play() on the
// ended clip ran from 0 before begin() seeked it back to 0). Unlike today's version every timer, rAF and listener
// belongs to one generation, so hold()/dispose() stop it completely (today's nested timers can't
// be cancelled, so a paused clip would restart itself).
//
// hold() is what a freeze or the live layer needs: the clip stops on one frame that the art-only
// page can reproduce from `clip_t`. A paused <video> does not necessarily show the frame at its
// currentTime (while playing slowly it can show an earlier one), so hold() seeks the paused clip to
// just inside the frame that was last presented (tracked with requestVideoFrameCallback): the picture
// does not change, and the art-only page reaches the same frame through the same seek. A hold during
// the first-frame cross-fade snaps to frame 0 (the cross-fade target) instead.
//
// A clip with its ease rendered in (routes/clip_ease.py; its published sidecar says `clip.eased`) plays
// differently, because the browser can't ease smoothly: at 0.065x each frame stays up for 0.6 s, so the old loop
// started with two visible ticks and stepped for its first ~3 s (owner, 19 Nov). That clip is 30 fps and already
// eases from stillness and back, it ends on exactly its first frame, and that frame is the full-size still (the
// clip's motion is laid onto it). So, as the owner set out: the still shows (the first-frame overlay); when play
// time comes it fades over EASED_FADE to the clip's start, the clip plays at 1x, the still fades back in, and it
// rests for a random time in `restS` (longer for bigger movements) before playing again. The first play waits a
// short random while after the clip arrives.

export const EASE_IN_DURATION = 2500;
export const EASE_OUT_DURATION = 2500;
export const HOLD_FINAL_FRAME = 4000;
export const PRIMER_PLAYBACK_DURATION = 1500;
export const LOOP_RESTART_DELAY = 2000;
export const CROSSFADE_DURATION = 8000;
export const FIRST_FRAME_FADE_OUT_DURATION = 500;
export const PACE = 0.6;
const SLOW = 0.065;

type Phase = "idle" | "seeking" | "primer" | "easeIn" | "cruise" | "easeOut" | "holdFinal" | "crossfade" | "restartDelay" | "rest";

/** A clip with its ease rendered in: play at 1x, rest on the picture for `restS` seconds (a random time in the range). */
export interface EasedLoop {
  restS: [number, number];
}
const FIRST_REST_S: [number, number] = [5, 20];
export const EASED_FADE = 2000;

export interface ClipCycleHooks {
  /** Today's `paused` state (drives the ?debug "Paused on Final Frame" label). */
  setPaused(p: boolean): void;
  /** Today's `showFirstFrame` state (the 8 s cross-fade to frame 0). */
  setShowFirstFrame(show: boolean): void;
  /** Hide the first-frame overlay at once, with no fade (used by hold()). */
  snapFirstFrameOff(): void;
  log?: (...args: unknown[]) => void;
}

function easeInOutQuad(t: number): number {
  return t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
}

export class ClipCycle {
  private gen = 0;
  private phase: Phase = "idle";
  private heldPhase: Phase | null = null;
  private timers = new Set<ReturnType<typeof setTimeout>>();
  private rafs = new Set<number>();
  private cleanups: (() => void)[] = [];
  private disposed = false;
  /** mediaTime of the frame last presented (what is on screen), from requestVideoFrameCallback. */
  private presentedTime: number | null = null;
  private rvfc: number | null = null;
  /** The first-frame overlay is up (from the cross-fade until the restart has frame 0 on screen). */
  private overlay = false;
  private eased: EasedLoop | null = null;
  private played = false;
  /** The rest before this begin() is over: play now (a begin() without one rests first). */
  private rested = false;

  constructor(private video: HTMLVideoElement, private hooks: ClipCycleHooks) {
    this.watchFrames();
  }

  private watchFrames = (): void => {
    if (this.disposed || typeof this.video.requestVideoFrameCallback !== "function") return;
    this.rvfc = this.video.requestVideoFrameCallback((_now, md) => {
      this.presentedTime = md.mediaTime;
      this.watchFrames();
    });
  };

  /** Set before start(): the clip has its ease rendered in. */
  setEased(loop: EasedLoop | null): void {
    this.eased = loop;
  }

  get isHeld(): boolean {
    return this.heldPhase !== null;
  }

  /** Today's maybeStartPlayback(): reload, then begin the cycle once metadata is known. */
  start(): void {
    if (this.disposed) return;
    if (this.isHeld) {
      this.heldPhase = "idle";
      return;
    }
    const g = this.next();
    const v = this.video;
    v.load();
    if (v.readyState >= 1) this.begin();
    else this.once(g, "loadedmetadata", () => this.begin());
  }

  /** Pause on the current frame and cancel the timeline. Resolves once that frame is ready to show. */
  hold(): Promise<void> {
    if (this.disposed) return Promise.resolve();
    if (this.isHeld) return this.frameReady(this.gen);
    this.heldPhase = this.phase;
    const g = this.next();
    const v = this.video;
    v.pause();
    if (this.phase === "idle") return this.frameReady(g); // never started: still on its first frame
    let target: number;
    if (this.phase === "crossfade" || this.phase === "restartDelay" || this.overlay) {
      // Mid cross-fade (or its restart): show frame 0 itself, with no fade, so the held picture is clip_t = 0.
      this.overlay = false;
      this.hooks.snapFirstFrameOff();
      this.hooks.setPaused(false);
      target = 0;
    } else {
      // Just inside the presented frame (+1 ms; frames last >= 16 ms), so the seek keeps the picture.
      const shown = this.presentedTime ?? v.currentTime;
      target = Number.isFinite(v.duration) ? Math.min(shown + 0.001, v.duration) : shown + 0.001;
    }
    return new Promise<void>((resolve) => {
      this.once(g, "seeked", () => void this.frameReady(g).then(resolve));
      v.currentTime = target;
      this.later(g, resolve, 2000); // never wedge a freeze on a seek
    });
  }

  /** Continue the cycle after a hold. */
  resume(): void {
    if (this.disposed || !this.isHeld) return;
    const from = this.heldPhase;
    this.heldPhase = null;
    this.next();
    switch (from) {
      case "idle":
        this.start();
        break;
      case "holdFinal":
        this.crossfadeStep(this.gen);
        break;
      case "rest":
        this.restStep(this.gen);
        break;
      case "crossfade":
      case "restartDelay":
        this.begin();
        break;
      case "seeking":
        this.begin();
        break;
      default:
        if (this.eased) this.playEased(this.gen);
        else this.resumeMotion(this.gen);
    }
  }

  dispose(): void {
    this.disposed = true;
    this.overlay = false;
    if (this.rvfc !== null) this.video.cancelVideoFrameCallback?.(this.rvfc);
    this.rvfc = null;
    this.next();
  }

  // -- timeline ---------------------------------------------------------------------------------

  private begin(): void {
    const g = this.next();
    const v = this.video;
    this.phase = "seeking";
    this.hooks.log?.("=== Begin Playback Cycle ===");
    this.hooks.setPaused(false);
    v.pause();
    v.playbackRate = SLOW;
    this.presentedTime = null; // the next presented frame is frame 0
    v.currentTime = 0;
    this.once(g, "seeked", () => {
      void this.frameReady(g).then(() => this.framePresented(g)).then(() => {
        if (g !== this.gen) return;
        if (this.eased) {
          if (this.rested) this.playEased(g);
          else this.restStep(g);
          return;
        }
        if (this.overlay) {
          // Frame 0 is on screen under the overlay, which shows the same picture: it can go now.
          this.overlay = false;
          this.hooks.setShowFirstFrame(false);
        }
        this.play(g, () => {
          this.phase = "primer";
          this.later(g, () => {
            this.phase = "easeIn";
            this.ease(g, SLOW, PACE, EASE_IN_DURATION, () => {
              this.phase = "cruise";
              // Today's formula, unchanged (MediaDisplay.tsx playUntil).
              const primerSeconds = (PRIMER_PLAYBACK_DURATION / 1000) * SLOW;
              const easeInSeconds = (EASE_IN_DURATION / 1000) * ((PACE + SLOW) / 2);
              const playUntil = Math.max(0, (v.duration / PACE - (primerSeconds + easeInSeconds) - EASE_OUT_DURATION / 1000) * 1000);
              this.later(g, () => this.easeOutStep(g), playUntil);
            });
          }, PRIMER_PLAYBACK_DURATION);
        });
      });
    });
  }

  private playEased(g: number): void {
    const v = this.video;
    this.played = true;
    this.rested = false;
    this.phase = "cruise";
    this.overlay = false;
    this.hooks.setShowFirstFrame(false); // fades to the clip's start, which is the same picture
    this.hooks.setPaused(false);
    v.playbackRate = 1;
    this.once(g, "ended", () => this.restStep(g));
    this.play(g, () => {});
  }

  /** Fade back to the still and rest on it, then seek back to frame 0 (under the still) and play again. */
  private restStep(g: number): void {
    if (!this.eased) return;
    this.phase = "rest";
    this.video.pause();
    this.overlay = true;
    this.hooks.setShowFirstFrame(true);
    this.hooks.setPaused(true);
    const [lo, hi] = this.played ? this.eased.restS : FIRST_REST_S;
    this.later(g, () => {
      this.rested = true;
      this.begin();
    }, (lo + Math.random() * (hi - lo)) * 1000);
  }

  private easeOutStep(g: number): void {
    this.phase = "easeOut";
    this.ease(g, PACE, SLOW, EASE_OUT_DURATION, () => {
      this.video.pause();
      this.hooks.setPaused(true);
      this.phase = "holdFinal";
      this.later(g, () => this.crossfadeStep(g), HOLD_FINAL_FRAME);
    });
  }

  private crossfadeStep(g: number): void {
    this.phase = "crossfade";
    this.overlay = true;
    this.hooks.setShowFirstFrame(true);
    this.later(g, () => {
      this.phase = "restartDelay";
      this.later(g, () => this.begin(), LOOP_RESTART_DELAY); // under the overlay: it goes once frame 0 shows
    }, CROSSFADE_DURATION);
  }

  /** After a hold in a moving phase: play on from the held frame, ease up to pace, ease out near the end. */
  private resumeMotion(g: number): void {
    const v = this.video;
    this.play(g, () => {
      this.phase = "easeIn";
      this.ease(g, v.playbackRate, PACE, EASE_IN_DURATION, () => {
        this.phase = "cruise";
        // Media consumed by the ease-out: its duration times the mean rate.
        const easeOutMedia = (EASE_OUT_DURATION / 1000) * ((PACE + SLOW) / 2);
        const left = Number.isFinite(v.duration) ? v.duration - v.currentTime - easeOutMedia : 0;
        this.later(g, () => this.easeOutStep(g), Math.max(0, (left / PACE) * 1000));
      });
    });
  }

  // -- helpers ----------------------------------------------------------------------------------

  /** Start a new generation: everything scheduled by older generations is cancelled. */
  private next(): number {
    this.gen++;
    this.timers.forEach((t) => clearTimeout(t));
    this.rafs.forEach((r) => cancelAnimationFrame(r));
    this.cleanups.splice(0).forEach((c) => c());
    this.timers.clear();
    this.rafs.clear();
    return this.gen;
  }

  private later(g: number, fn: () => void, ms: number): void {
    const t = setTimeout(() => {
      this.timers.delete(t);
      if (g === this.gen) fn();
    }, ms);
    this.timers.add(t);
  }

  private frame(g: number, fn: (now: number) => void): void {
    const r = requestAnimationFrame((now) => {
      this.rafs.delete(r);
      if (g === this.gen) fn(now);
    });
    this.rafs.add(r);
  }

  private once(g: number, event: string, fn: () => void): void {
    const v = this.video;
    const h = () => {
      v.removeEventListener(event, h);
      if (g === this.gen) fn();
    };
    v.addEventListener(event, h);
    this.cleanups.push(() => v.removeEventListener(event, h));
  }

  private play(g: number, then: () => void): void {
    this.video.play().then(
      () => {
        if (g === this.gen) then();
      },
      (e) => this.hooks.log?.("play() rejected", String(e)),
    );
  }

  /** Today's ensureFrameRendered(): a frame is decoded and no seek is pending. */
  private frameReady(g: number): Promise<void> {
    return new Promise((resolve) => {
      const check = () => {
        if (g !== this.gen) return resolve();
        if (this.video.readyState >= 2 && !this.video.seeking) resolve();
        else this.frame(g, check);
      };
      this.frame(g, check);
    });
  }

  /** The seeked frame has been presented (requestVideoFrameCallback saw it), plus a frame for the compositor;
   *  at most 500 ms (a browser without the callback, or one that doesn't call it for a paused seek). */
  private framePresented(g: number): Promise<void> {
    return new Promise((resolve) => {
      const t0 = performance.now();
      let extra = 1;
      const check = (now: number) => {
        if (g !== this.gen) return resolve();
        const shown = this.presentedTime !== null && this.presentedTime < 0.05;
        if (shown || now - t0 > 500) {
          if (extra-- <= 0) return resolve();
        }
        this.frame(g, check);
      };
      this.frame(g, check);
    });
  }

  private ease(g: number, start: number, end: number, duration: number, done: () => void): void {
    const t0 = performance.now();
    const step = (now: number) => {
      const p = Math.min(1, (now - t0) / duration);
      const rate = start + (end - start) * easeInOutQuad(p);
      this.video.playbackRate = Math.max(0.0625, Math.min(16, rate));
      if (p < 1) this.frame(g, step);
      else done();
    };
    this.frame(g, step);
  }
}
