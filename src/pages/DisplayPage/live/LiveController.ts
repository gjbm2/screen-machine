// The live layer's state machine (DCL "liveMachine", LIVE_V1 §2.2 rules 4-8).
//
//   live.attach -> connecting -> warming (under the art stack) -> [entry cut] playing
//   live.leave  -> leaving -> [exit cut at hold_pts] -> teardown
//   failures: warm-up 8 s -> ack live_failed; no presented frame for 2 s while live -> NO cut (owner, 17 Oct:
//             a slow stream never takes the screen out of live): the last frame stays, the player is nudged;
//             no presented frame for 10 s -> cut to art, ack art_visible{stall} (owner, 23 Oct: a dead man's
//             handle, so a missed live.leave or a session that is gone never leaves a frozen live frame on glass);
//             player error -> live_failed before the cut, art_visible{error} after it;
//             live.abort -> cut if visible, ack art_visible{abort}; leave timeout 20 s -> exit-timeout cut.
//
// Every cut is ONE attribute flip on the art stack, made inside a requestVideoFrameCallback (or a
// timer, for failure cuts); the ack goes out right after the flip.
import type { CtlStore } from "../control/store";
import type { Ack, ArtVisibleReason, LiveAttach, LiveStats } from "../control/protocol";
import { MseLivePlayer } from "./MseLivePlayer";
import { probeCaps } from "../control/caps";

const WARMUP_TIMEOUT_MS = 8_000;
const STALL_MS = 2_000;
/** Dead man's handle (owner, 23 Oct): this long with no presented frame means the stream is gone, not slow. */
const DEAD_MS = 10_000;
const EXIT_TIMEOUT_MS = 20_000;
/** How many ended session ids are remembered (bounded; a session id is never reused). */
const ENDED_MAX = 64;

export interface LiveDeps {
  store: CtlStore;
  sendAck(ack: Ack): void;
  liveLayer(): HTMLElement | null;
  /** Show/hide the art stack in one property change. */
  setArtHidden(hidden: boolean): void;
  /** Resolves when the art stack's base is decoded and ready to be revealed. */
  prepareReveal(): Promise<void>;
  log(msg: string, detail?: unknown): void;
  event(kind: string, detail?: Record<string, unknown>): void;
  /** This page's role (kiosk / viewer / lab): sent with the media stats, so the renderer's diagnostics read the TV's own. */
  role?: string;
}

interface Session {
  attach: LiveAttach;
  player: MseLivePlayer;
  visible: boolean;
  leaving: { holdPts: number; artReady: boolean } | null;
  warmTimer: ReturnType<typeof setTimeout> | null;
  exitTimer: ReturnType<typeof setTimeout> | null;
  framesSinceStart: number;
  ended: boolean;
  /** performance.now() of the last stall nudge (the player is nudged at most every STALL_MS). */
  lastKick: number;
}

/** Two animation frames, or `capMs` if rAF is not running (hidden tab): teardown must never wedge. */
function twoFramesAtMost(capMs: number): Promise<void> {
  return new Promise((resolve) => {
    const t = setTimeout(resolve, capMs);
    requestAnimationFrame(() =>
      requestAnimationFrame(() => {
        clearTimeout(t);
        resolve();
      }),
    );
  });
}

export class LiveController {
  private cur: Session | null = null;
  /** performance.now() when the page last returned to art after live (timed-reload deferral). */
  returnedToArtAt: number | null = null;
  private tearingDown = 0;
  /**
   * Sessions this page has finished with (cut away, failed, aborted or refused). A `welcome.live`
   * naming one (e.g. after a stall cut and a reconnect) must not re-attach it mid-scene: room_service
   * re-enters live through a new session (DCL v3 §8.1).
   */
  private endedIds = new Set<string>();

  constructor(private deps: LiveDeps) {}

  /** Has this page already ended `session`? */
  hasEnded(session: string): boolean {
    return this.endedIds.has(session);
  }

  private markEnded(session: string): void {
    this.endedIds.delete(session);
    this.endedIds.add(session);
    while (this.endedIds.size > ENDED_MAX) this.endedIds.delete(this.endedIds.values().next().value as string);
  }

  /** A terminal live_failed for a session that was never mounted. */
  private refuse(session: string, reason: string): void {
    this.markEnded(session);
    this.deps.event("live-failed", { session, reason });
    this.deps.sendAck({ kind: "live_failed", session, reason });
  }

  /** A live session is mounted, or still being torn down. */
  get busy(): boolean {
    return this.cur !== null || this.tearingDown > 0;
  }

  get visible(): boolean {
    return !!this.cur?.visible;
  }

  /**
   * `live.attach` (origin "attach") or the `live` of a welcome (origin "welcome"). A session this page
   * has already ended is never re-attached: a welcome naming it is ignored, and an explicit attach is
   * answered with live_failed{session-ended} so room_service does not wait for a cut that won't come.
   */
  attach(a: LiveAttach, origin: "attach" | "welcome" = "attach"): void {
    const { deps } = this;
    if (!a?.session || !a.url) {
      deps.log("live.attach without session/url ignored", { session: a?.session ?? null });
      return;
    }
    const prev = this.active;
    if (prev?.attach.session === a.session) return; // idempotent (welcome after a reconnect)
    if (this.endedIds.has(a.session)) {
      deps.event("attach-ended-ignored", { session: a.session, origin });
      if (origin === "attach") deps.sendAck({ kind: "live_failed", session: a.session, reason: "session-ended" });
      return;
    }
    if (prev) this.finish(prev, "abort", prev.player.lastMediaTime); // a new session replaces the old one
    if (!deps.store.getState().policy.live_enabled) {
      this.refuse(a.session, "live_disabled");
      return;
    }
    const caps = probeCaps();
    const family = a.codec === "hevc" ? caps.mse_hevc : a.codec === "h264" ? caps.mse_h264 : a.codec === "av1" ? caps.mse_av1 : true;
    const layer = deps.liveLayer();
    if (!family || !layer) {
      this.refuse(a.session, family ? "no-live-layer" : "unsupported-codec");
      return;
    }

    const s: Session = {
      attach: a,
      player: null as unknown as MseLivePlayer,
      visible: false,
      leaving: null,
      warmTimer: null,
      exitTimer: null,
      framesSinceStart: 0,
      ended: false,
      lastKick: 0,
    };
    s.player = new MseLivePlayer(layer, a, {
      onFrame: (now, meta) => this.onFrame(s, now, meta),
      onStarted: () => {
        if (this.cur === s && !s.visible) deps.store.dispatch({ type: "live", live: { session: a.session, phase: "warming" } });
      },
      onTick: () => this.checkStall(s), // the 2 s stall nudge, checked on the player's 250 ms watch
      onFatal: (reason) => this.onPlayerFatal(s, reason),
      onEnded: (reason) => {
        deps.event("media-ended", { session: a.session, reason });
        if (!s.visible) this.fail(s, reason === "eos" ? "eos-before-cut" : "media-closed");
        // Once live, the screen stays live: room_service ends it (a new attach, live.leave or live.abort), or the
        // dead man's handle in checkStall does when no frame has come for DEAD_MS.
      },
      log: deps.log,
    }, deps.role);
    this.cur = s;
    document.documentElement.setAttribute("data-sm-live", a.session);
    deps.store.dispatch({ type: "live", live: { session: a.session, phase: "connecting" } });
    deps.event("attach", { session: a.session, codec: a.codec, target_buffer_ms: a.target_buffer_ms });
    s.warmTimer = setTimeout(() => {
      if (this.cur === s && !s.visible) this.fail(s, "warmup-timeout");
    }, WARMUP_TIMEOUT_MS);
    s.player.start();
  }

  /** The mounted session, unless it is already cut away and only waiting for its teardown. */
  private get active(): Session | null {
    return this.cur && !this.cur.ended ? this.cur : null;
  }

  leave(session: string, holdPts: number): void {
    const s = this.active;
    if (!s || s.attach.session !== session) {
      // Nothing (or something else) is live: the art stack is what's on glass.
      if (!s) this.deps.sendAck({ kind: "art_visible", session, media_time: null, reason: "exit" });
      return;
    }
    if (!s.visible) {
      this.finish(s, "exit", null);
      return;
    }
    if (s.leaving) {
      s.leaving.holdPts = Number(holdPts); // a re-exit moves the target
      return;
    }
    s.leaving = { holdPts: Number(holdPts), artReady: false };
    this.deps.store.dispatch({ type: "live", live: { session, phase: "leaving" } });
    this.deps.event("leave", { session, hold_pts: holdPts });
    void this.deps.prepareReveal().then(() => {
      if (s.leaving) s.leaving.artReady = true;
    });
    s.exitTimer = setTimeout(() => {
      if (this.cur === s && s.visible) this.finish(s, "exit-timeout", s.player.lastMediaTime);
    }, EXIT_TIMEOUT_MS);
  }

  abort(session: string, reason: string): void {
    const s = this.active;
    if (!s) {
      this.deps.sendAck({ kind: "art_visible", session, media_time: null, reason: "abort" });
      return;
    }
    if (s.attach.session !== session) return; // stale
    this.deps.event("abort", { session, reason });
    this.finish(s, "abort", s.visible ? s.player.lastMediaTime : null);
  }

  /** A render error in the live layer: treated as a stall (LIVE_V1 §2.2 rule 10). */
  onLayerError(): void {
    const s = this.active;
    if (!s) return;
    if (s.visible) this.finish(s, "error", s.player.lastMediaTime);
    else this.fail(s, "live-layer-error");
  }

  stats(): LiveStats | null {
    const s = this.cur;
    if (!s) return null;
    const p = s.player;
    return {
      session: s.attach.session,
      fps: p.fps,
      presented: p.presented,
      dropped: p.dropped(),
      stalls: p.stalls,
      buffered_ms: Math.round(p.bufferedMs()),
      edge_jumps: p.edgeJumps,
      phase: this.deps.store.getState().live?.phase,
      gap_skips: p.gapSkips,
    };
  }

  dispose(): void {
    if (this.cur) this.teardown(this.cur);
  }

  // -- internals -----------------------------------------------------------------------------------

  private onFrame(s: Session, now: DOMHighResTimeStamp, meta: VideoFrameCallbackMetadata): void {
    if (this.cur !== s || s.ended) return;
    if (!s.player.started) return; // a frame shown by the initial seek, before play()
    s.framesSinceStart++;
    if (!s.visible) {
      // ENTRY CUT: the first presented frame after play(). One attribute flip hides the art stack;
      // the video (already showing this frame underneath) is all that is composited from now on.
      const t0 = performance.now();
      this.deps.setArtHidden(true);
      s.visible = true;
      if (s.warmTimer) clearTimeout(s.warmTimer);
      s.warmTimer = null;
      this.deps.sendAck({ kind: "live_visible", session: s.attach.session, media_time: meta.mediaTime });
      this.deps.store.dispatch({ type: "layer", layer: "live" });
      this.deps.store.dispatch({ type: "live", live: { session: s.attach.session, phase: "playing" } });
      this.deps.event("entry-cut", {
        session: s.attach.session,
        rvfc_now: now,
        flip_at: t0,
        expected_display: meta.expectedDisplayTime,
        media_time: meta.mediaTime,
        presented_frames: meta.presentedFrames,
        frames_since_start: s.framesSinceStart,
      });
      return;
    }
    const l = s.leaving;
    if (l && l.artReady && meta.mediaTime >= l.holdPts - 0.5 / s.player.fps) {
      // EXIT CUT on the first presented frame of the held target.
      this.deps.event("exit-frame", { session: s.attach.session, rvfc_now: now, media_time: meta.mediaTime, hold_pts: l.holdPts });
      this.finish(s, "exit", meta.mediaTime);
    }
  }

  private checkStall(s: Session): void {
    if (this.cur !== s || !s.visible || s.ended) return;
    const last = s.player.lastPresentedAt;
    const now = performance.now();
    // Owner, 17 Oct: a slow or stuck stream must never stop live by itself. Keep the last frame on glass, report
    // it, and nudge the player to the live edge every STALL_MS while no frame comes; room_service ends live.
    // Dead man's handle (owner, 23 Oct: "if live mode stops for a period of time, the display page flips out of live
    // mode"): DEAD_MS with no frame is a stream that is gone (a missed live.leave, a deleted session), not a slow one.
    // Cut to art and say so (art_visible{stall}): room_service treats it as a fallback and goes live again if it
    // still wants live.
    if (last !== null && now - last > DEAD_MS) {
      this.deps.event("dead", { session: s.attach.session, since_last_frame_ms: Math.round(now - last) });
      this.finish(s, "stall", s.player.lastMediaTime);
      return;
    }
    if (last !== null && now - last > STALL_MS && now - s.lastKick > STALL_MS) {
      s.lastKick = now;
      this.deps.event("stall", { session: s.attach.session, since_last_frame_ms: Math.round(now - last) });
      s.player.kick();
    }
  }

  private onPlayerFatal(s: Session, reason: string): void {
    if (this.cur !== s || s.ended) return;
    if (s.visible) this.finish(s, "error", s.player.lastMediaTime);
    else this.fail(s, reason);
  }

  /** Before the entry cut: report and tear down; the art stack never left the screen. */
  private fail(s: Session, reason: string): void {
    if (s.ended) return;
    this.deps.event("live-failed", { session: s.attach.session, reason });
    this.deps.sendAck({ kind: "live_failed", session: s.attach.session, reason });
    this.teardown(s);
  }

  /** Show the art stack (one flip) if live is visible, ack art_visible, then tear down. */
  private finish(s: Session, reason: ArtVisibleReason, mediaTime: number | null): void {
    if (s.ended) return;
    this.markEnded(s.attach.session);
    const wasVisible = s.visible;
    if (wasVisible) {
      this.deps.setArtHidden(false);
      s.visible = false;
      this.returnedToArtAt = performance.now();
      this.deps.store.dispatch({ type: "layer", layer: "art" });
    }
    this.deps.event("art-visible", { session: s.attach.session, reason, media_time: mediaTime, cut: wasVisible, at: performance.now() });
    this.deps.sendAck({ kind: "art_visible", session: s.attach.session, media_time: wasVisible ? mediaTime : null, reason });
    if (wasVisible) {
      // Let the art frame composite before the decoder goes away (the art stack is opaque anyway).
      this.tearingDown++;
      s.ended = true;
      void twoFramesAtMost(250).then(() => {
        this.tearingDown--;
        this.teardown(s);
      });
    } else {
      this.teardown(s);
    }
  }

  private teardown(s: Session): void {
    s.ended = true;
    this.markEnded(s.attach.session);
    if (s.warmTimer) clearTimeout(s.warmTimer);
    if (s.exitTimer) clearTimeout(s.exitTimer);
    s.warmTimer = s.exitTimer = null;
    s.player.destroy();
    if (s.visible) {
      // Should not happen (finish() flips first), but never leave a hidden art stack over nothing.
      this.deps.setArtHidden(false);
      this.deps.store.dispatch({ type: "layer", layer: "art" });
    }
    if (this.cur === s) {
      this.cur = null;
      document.documentElement.removeAttribute("data-sm-live");
      this.deps.store.dispatch({ type: "live", live: null });
    }
    this.deps.event("teardown", { session: s.attach.session, videos_left: document.querySelectorAll("video[data-sm-live]").length });
  }
}
