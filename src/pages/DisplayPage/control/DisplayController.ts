// The controlled page's brain (?ctl=ws://host:port). Lives outside React render:
//   * one ControlClient (hello/welcome, hb, liveness, reconnect);
//   * the mask: push (mask.set, applied in rAF through refs) or the 3 s poll, per LIVE_V1 §2.2 rule 2;
//   * freeze/unfreeze with the exact ArtState ack (rule 3);
//   * the live layer (LiveController, rules 4-8);
//   * kiosk.state every 5 s; timed-reload deferral (rule 9).
// React subscribes to `store` only for coarse state (frozen, layer, live mounted).
import { ControlClient } from "./ControlClient";
import { MaskPoller } from "./MaskPoller";
import { ReloadScheduler } from "./ReloadScheduler";
import { probeCaps } from "./caps";
import { createCtlStore, type CtlStore } from "./store";
import type { Ack, ArtFlags, ArtState, Envelope, Fid, Hello, KioskState, LiveAttach, MaskSet, Policy, Role, Welcome } from "./protocol";
import { LEGACY_FADE, MaskController } from "../art/MaskController";
import { LiveController } from "../live/LiveController";

const KIOSK_STATE_MS = 5_000;

const FREEZE_SAFETY_MS = 30_000;
/**
 * Upper bound on waiting for transitions/decode before the frozen ack. A through-black base change
 * takes >= 3.1 s (1 s fade-out, 1 s black, 1 s fade-in), so the cap is above it; room_service's
 * freeze_ack_s is 5 s (DCL v3 §8.1).
 */
const FREEZE_SETTLE_MAX_MS = 4_500;
/** With the control socket down this long, the 3 s mask poll resumes (LIVE_V1 §2.2 rule 2). */
const POLL_AFTER_DOWN_MS = 5_000;
/**
 * Before the first connection, the poll waits this long for it: a healthy start gets its mask from the
 * welcome and never polls Flask (each read advances Flask's shared EMA, LIVE_V1 §2.2b); a page whose
 * room_service is unreachable starts polling after this.
 */
const POLL_AT_BOOT_AFTER_MS = 1_500;
/**
 * With the control socket down this long, policy.timed_reload stops applying and today's reload
 * schedule returns (rule 9 "no control socket"; DCL v3 §5.4/§5.7: 30 min, so a room_service restart
 * or a short outage never turns timed reloads back on in a live mode).
 */
const POLICY_LAPSE_AFTER_DOWN_MS = 30 * 60_000;
/** Event kinds that can arrive at up to ~10/s: counted in `eventCounts`, never put in the ring (v3 L8). */
const COUNTED_ONLY = new Set(["mask.set"]);
const RELOAD_AFTER_ART_MS = 60_000;
/** A tab hidden this long lets go of the control socket (see onVisibility). */
const HIDDEN_RELEASE_MS = 5_000;
const EVENT_LOG_MAX = 400;
/** A rejoin waiting for the old session's teardown (at most ~250 ms) checks again this often, this many times. */
const REJOIN_RETRY_MS = 300;
const REJOIN_RETRIES = 20;

/** What the React art stack exposes to the controller. */
export interface ArtAdapter {
  /** Resolves once no base transition is running, a clip is held on one frame and the base is decoded. */
  settle(): Promise<void>;
  /** What the art stack shows now. */
  snapshot(): { base_url: string | null; kind: "image" | "video"; clip_t: number | null };
  /** Resolves once the base is decoded and can be revealed by a cut. */
  prepareReveal(): Promise<void>;
}

export interface DisplayControllerOptions {
  ctlUrl: string;
  screenId: string;
  role: Role;
  flags: ArtFlags;
  /** `&ctlkey=` from the page URL, sent as `hello.key` (LIVE_V1 §3). Never logged or exposed in events. */
  ctlKey?: string | null;
  /** Today's fade-to-black-and-reload, run by the timed-reload schedule (rule 9). */
  reload?: () => void;
}

export interface CtlEvent {
  t: number;
  kind: string;
  [k: string]: unknown;
}

function twoFrames(): Promise<void> {
  return new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => r())));
}

/** Resolve after `p` or after `ms`, whichever comes first (rAF never fires in a hidden tab). */
function atMost(p: Promise<unknown>, ms: number): Promise<void> {
  return new Promise((resolve) => {
    const t = setTimeout(resolve, ms);
    p.then(
      () => (clearTimeout(t), resolve()),
      () => (clearTimeout(t), resolve()),
    );
  });
}

/** usedJSHeapSize in MB, 0.1 MB resolution (Chrome only; null elsewhere). */
function heapMb(): number | null {
  const mem = (performance as Performance & { memory?: { usedJSHeapSize?: number } }).memory;
  const used = mem?.usedJSHeapSize;
  return typeof used === "number" ? Math.round((used / 1048576) * 10) / 10 : null;
}

function randomId(prefix: string): string {
  const rnd = typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID().slice(0, 8) : Math.random().toString(36).slice(2, 10);
  return `${prefix}-${rnd}`;
}

/** Fixed when this module is first evaluated, i.e. once per document load. */
const PAGE_LOAD_ID = randomId("p");

/** The bundle name of this build (e.g. "index--tLZUHxv"), or "dev" under the Vite dev server. */
function buildId(): string {
  const s = document.querySelector<HTMLScriptElement>('script[type="module"][src*="/assets/"]');
  const m = s?.src.match(/\/assets\/([^/]+)\.js/);
  return m ? m[1] : import.meta.env.DEV ? "dev" : "unknown";
}

/** `?ctl=ws://host:port` -> `ws://host:port/v1/screen/<id>?role=…`; null when absent, `off` or invalid. */
export function controlSocketUrl(ctl: string | null, screenId: string, role: Role): string | null {
  if (!ctl || ctl === "off") return null;
  let u: URL;
  try {
    u = new URL(ctl);
  } catch {
    return null;
  }
  if (u.protocol !== "ws:" && u.protocol !== "wss:") return null;
  const base = `${u.protocol}//${u.host}${u.pathname.replace(/\/+$/, "")}`;
  return `${base}/v1/screen/${encodeURIComponent(screenId)}?role=${role}`;
}

export class DisplayController {
  readonly store: CtlStore = createCtlStore();
  readonly mask: MaskController;
  /** Replaced (when nothing is mounted) by a subscriber's rejoin of a session it ended: see attachLive. */
  live: LiveController;
  /** One id per document load, shared by every controller this load creates (LIVE_V1 §2.1 hello). */
  readonly pageLoadId = PAGE_LOAD_ID;
  private client: ControlClient;
  private poller: MaskPoller;
  private art: ArtAdapter | null = null;
  private artStack: HTMLElement | null = null;
  private liveLayerEl: HTMLElement | null = null;
  private flags: ArtFlags;
  private epoch: string | number | null = null;
  private maskRev: number | null = null;
  private everUp = false;
  /** A pushed mask has arrived on the current connection (until then the poll keeps the screen lit). */
  private pushedThisConnection = false;
  private lastPushAt = Number.NEGATIVE_INFINITY;
  private downSince: number | null = null;
  private pollTimer: ReturnType<typeof setTimeout> | null = null;
  private stateTimer: ReturnType<typeof setInterval> | null = null;
  private freezeTimer: ReturnType<typeof setTimeout> | null = null;
  private freezeSeq = 0;
  private started = false;
  private startedAt = 0;
  /** A welcome has been received on some connection (until then the policy defaults apply). */
  private welcomed = false;
  /** A welcome has been received on the current connection (reset when it goes down). */
  private welcomedThisConnection = false;
  private readonly reloads: ReloadScheduler;
  /** A subscriber's rejoin held while the tab is hidden (nothing is presented there, so it could only time out),
   *  or retrying while the old session is torn down. */
  private pendingRejoin: { a: LiveAttach; origin: "attach" | "welcome" } | null = null;
  private hiddenTimer: ReturnType<typeof setTimeout> | null = null;
  private visibilityChanges = 0;
  private releasedHidden = false;
  private rejoinTimer: ReturnType<typeof setTimeout> | null = null;
  private rejoinRetries = 0;
  private longTaskObserver: PerformanceObserver | null = null;
  longTasks = 0;
  overlaysDropped = 0;
  freezeTimeouts = 0;
  /** Ring buffer of timestamped events (exposed as window.__smCtl.events for tests and debugging). */
  readonly events: CtlEvent[] = [];
  /** Counts for high-rate kinds kept out of the ring (COUNTED_ONLY). */
  readonly eventCounts: Record<string, number> = {};

  constructor(private opts: DisplayControllerOptions) {
    this.flags = { ...opts.flags };
    this.mask = new MaskController(this.flags);
    this.poller = new MaskPoller(
      opts.screenId,
      (m, startedAt) => {
        if (startedAt < this.lastPushAt) return; // a pushed value is newer than this poll
        this.mask.apply(m, { ...LEGACY_FADE, source: "poll" });
      },
      this.log,
    );
    this.client = new ControlClient({
      url: opts.ctlUrl,
      hello: () => {
        const h: Hello = {
          role: opts.role,
          screen: opts.screenId,
          page_load_id: this.pageLoadId,
          build: buildId(),
          caps: probeCaps(),
          flags: { ...this.flags },
        };
        if (opts.ctlKey) h.key = opts.ctlKey;
        return h;
      },
      onMessage: (m) => this.onMessage(m),
      onStatus: (st) => this.onSocketStatus(st),
      log: this.log,
    });
    this.live = this.createLive();
    this.reloads = new ReloadScheduler({
      canReloadNow: () => this.canReloadNow(),
      reload: () => this.commitReload(),
      log: this.log,
    });
  }

  private createLive(): LiveController {
    return new LiveController({
      store: this.store,
      sendAck: (ack) => this.sendAck(ack),
      liveLayer: () => this.liveLayerEl,
      setArtHidden: (hidden) => this.setArtHidden(hidden),
      prepareReveal: () => this.art?.prepareReveal() ?? Promise.resolve(),
      log: this.log,
      event: (kind, detail) => this.event(kind, detail),
      role: this.opts.role,
    });
  }

  start(): void {
    if (this.started) return;
    this.started = true;
    this.startedAt = performance.now();
    document.addEventListener("visibilitychange", this.onVisibility);
    this.client.start();
    this.reloads.start();
    this.updateDerived();
    this.stateTimer = setInterval(() => this.sendKioskState(), KIOSK_STATE_MS);
    try {
      this.longTaskObserver = new PerformanceObserver((list) => {
        this.longTasks += list.getEntries().length;
      });
      this.longTaskObserver.observe({ type: "longtask", buffered: true });
    } catch {
      this.longTaskObserver = null; // not supported: long_tasks stays 0
    }
    this.onVisibility(); // opened in a background tab: release it too
  }

  stop(): void {
    if (!this.started) return;
    this.started = false;
    document.removeEventListener("visibilitychange", this.onVisibility);
    if (this.hiddenTimer) clearTimeout(this.hiddenTimer);
    this.hiddenTimer = null;
    this.dropRejoin(null);
    this.live.dispose();
    this.client.stop();
    this.poller.stop();
    this.reloads.stop();
    this.mask.dispose();
    this.longTaskObserver?.disconnect();
    this.longTaskObserver = null;
    if (this.stateTimer) clearInterval(this.stateTimer);
    if (this.pollTimer) clearTimeout(this.pollTimer);
    if (this.freezeTimer) clearTimeout(this.freezeTimer);
    this.stateTimer = this.pollTimer = this.freezeTimer = null;
  }

  // -- wiring from React ---------------------------------------------------------------------------

  setArtAdapter(a: ArtAdapter | null): void {
    this.art = a;
  }

  attachArtStack = (el: HTMLElement | null): void => {
    this.artStack = el;
  };

  attachLiveLayer = (el: HTMLElement | null): void => {
    this.liveLayerEl = el;
  };

  setFlags(flags: ArtFlags): void {
    this.flags = { ...flags };
    this.mask.setFlags(flags);
    this.updateDerived();
  }

  /** An overlay arrived while frozen or live: dropped (LIVE_V1 §2.2 rule 5). */
  noteOverlayDropped(): void {
    this.overlaysDropped++;
    this.event("overlay-dropped", { total: this.overlaysDropped });
  }

  /** LIVE_V1 §2.2 rule 9: no timed reload while live, frozen, attaching, or within 60 s of returning to art. */
  canReloadNow(): boolean {
    const s = this.store.getState();
    if (s.frozen || s.live || this.live.busy) return false;
    const back = this.live.returnedToArtAt;
    return back === null || performance.now() - back >= RELOAD_AFTER_ART_MS;
  }

  onLiveLayerError(): void {
    this.live.onLayerError();
  }

  /**
   * A timed reload is committing: close the control socket first (1001 "reloading"), so room_service
   * sees the page leave and no freeze can be acked by a page that is fading out and unloading.
   */
  private commitReload(): void {
    this.event("reload", {});
    this.client.stop(1001, "reloading");
    (this.opts.reload ?? (() => location.reload()))();
  }

  // -- control messages ----------------------------------------------------------------------------

  private onMessage(m: Envelope): void {
    const d = (m.data ?? {}) as Record<string, unknown>;
    switch (m.type) {
      case "welcome":
        this.onWelcome(d as unknown as Welcome);
        break;
      case "policy":
        this.onPolicy(d as Partial<Policy>);
        break;
      case "mask.set":
        this.onMaskSet(d as unknown as MaskSet);
        break;
      case "freeze":
        void this.onFreeze(d.fid as Fid, String(d.reason ?? ""));
        break;
      case "unfreeze":
        this.onUnfreeze(d.fid as Fid);
        break;
      case "live.attach":
        this.attachLive(d as unknown as LiveAttach, "attach");
        break;
      case "live.leave":
        this.dropRejoin(String(d.session));
        this.live.leave(String(d.session), Number(d.hold_pts));
        break;
      case "live.abort":
        this.dropRejoin(String(d.session));
        this.live.abort(String(d.session), String(d.reason ?? ""));
        break;
      case "hb":
        break;
      default:
        break; // unknown types are ignored (additive protocol)
    }
  }

  private onWelcome(w: Welcome): void {
    if (w.epoch !== this.epoch) {
      this.epoch = w.epoch;
      this.maskRev = null; // new epoch: the snapshot is authoritative
    }
    this.welcomed = true;
    this.welcomedThisConnection = true;
    this.store.dispatch({ type: "welcome", epoch: w.epoch, policy: w.policy });
    this.event("welcome", { epoch: w.epoch, policy: this.store.getState().policy, live: w.live?.session ?? null });
    if (w.mask) this.onMaskSet(w.mask, true);
    this.updateDerived();
    const l = w.live;
    this.dropRejoin(null); // the welcome says what the screen shows now
    if (l && l.attach && l.attach.url) {
      const session = l.session ?? l.attach.session;
      this.attachLive({ ...(l.attach as LiveAttach), session: session as string }, "welcome");
    }
  }

  /**
   * live.attach, or a welcome's `live`. room_service marks a subscriber's attach `rejoin` (a viewer, or glass that is
   * not the driven TV; the TV never gets it): such a page shows whatever the screen shows, so it plays the session
   * even if it ended that session itself (a stall in a hidden tab, a Wi-Fi drop, a warm-up timeout), which
   * LiveController otherwise never re-attaches. A rejoin waits while the tab is hidden: nothing is presented there.
   */
  private attachLive(a: LiveAttach, origin: "attach" | "welcome", retry = false): void {
    this.dropRejoin(null);
    if (!retry) this.rejoinRetries = 0;
    if (a.rejoin) {
      if (document.visibilityState === "hidden") {
        this.pendingRejoin = { a, origin };
        this.event("rejoin-deferred", { session: a.session, origin });
        return;
      }
      if (this.live.hasEnded(a.session)) {
        if (this.live.busy) {
          // Still cutting away from a session (or showing another one): let that finish first.
          const cur = this.store.getState().live?.session;
          if (cur && cur !== a.session) this.live.abort(cur, "rejoin");
          if (this.rejoinRetries++ < REJOIN_RETRIES) {
            this.pendingRejoin = { a, origin };
            this.rejoinTimer = setTimeout(() => this.attachLive(a, origin, true), REJOIN_RETRY_MS);
          }
          return;
        }
        // Nothing is mounted: a fresh LiveController forgets the sessions this page ended.
        const back = this.live.returnedToArtAt;
        this.live.dispose();
        this.live = this.createLive();
        this.live.returnedToArtAt = back;
        this.event("rejoin", { session: a.session, origin });
      }
    }
    this.live.attach(a, origin);
  }

  /** Forget a held or retrying rejoin (`session` null: any; else only that session's). */
  private dropRejoin(session: string | null): void {
    if (session !== null && this.pendingRejoin && this.pendingRejoin.a.session !== session) return;
    this.pendingRejoin = null;
    if (this.rejoinTimer) clearTimeout(this.rejoinTimer);
    this.rejoinTimer = null;
  }

  // A hidden tab can't hold the control socket (Chrome throttles its timers to about once a minute,
  // so its heartbeats stop and room closes it as dead) or show live (it renders no frames, so the
  // handshake fails). After HIDDEN_RELEASE_MS hidden it lets go: room runs the screen without a page
  // (headless live). Visible again, it reconnects at once and the welcome joins whatever is live.
  // The TVs' kiosk pages are always visible (checked 15 Oct), so this never touches them.
  private releaseHidden = (): void => {
    this.hiddenTimer = null;
    if (!this.started || document.visibilityState !== "hidden" || this.releasedHidden) return;
    this.releasedHidden = true;
    this.event("hidden-release");
    this.client.stop(1001, "tab hidden");
  };

  private onVisibility = (e?: Event): void => {
    if (e) {
      this.visibilityChanges++;
      this.sendKioskState(); // room (and the monitor) see "hidden: paints no video" at once, not 5 s later
    }
    if (document.visibilityState === "hidden") {
      if (!this.hiddenTimer && !this.releasedHidden) this.hiddenTimer = setTimeout(this.releaseHidden, HIDDEN_RELEASE_MS);
    } else {
      if (this.hiddenTimer) clearTimeout(this.hiddenTimer);
      this.hiddenTimer = null;
      if (this.releasedHidden && this.started) {
        this.releasedHidden = false;
        this.event("visible-reconnect");
        this.client.start();
      }
    }
    const p = this.pendingRejoin;
    if (document.visibilityState !== "visible" || !p) return;
    this.attachLive(p.a, p.origin);
  };

  /** A `policy` message: only the fields it carries change. */
  private onPolicy(p: Partial<Policy>): void {
    const before = this.store.getState().policy;
    this.store.dispatch({ type: "policy", policy: p });
    const after = this.store.getState().policy;
    if (after === before) return;
    // mask_push switched back on: keep polling until a pushed value arrives on this connection.
    if (after.mask_push && !before.mask_push) this.pushedThisConnection = false;
    this.event("policy", { policy: after });
    this.updateDerived();
  }

  /**
   * `fromWelcome`: the welcome's mask is authoritative, so it counts as a pushed value on this
   * connection even when its rev was already applied (a same-epoch reconnect); otherwise the 3 s
   * poll would keep running while connected, advancing Flask's read-driven EMA for nothing.
   */
  private onMaskSet(m: MaskSet, fromWelcome = false): void {
    if (!this.store.getState().policy.mask_push) return;
    if (fromWelcome && !this.pushedThisConnection) {
      this.pushedThisConnection = true;
      this.updateDerived();
    }
    if (typeof m.rev === "number") {
      if (this.maskRev !== null && m.rev <= this.maskRev) return; // stale within this epoch
      this.maskRev = m.rev;
    }
    this.event("mask.set");
    this.lastPushAt = performance.now();
    // The first real value replaces the initial black mask at once, with no fade from brightness 0.
    const transitionMs = this.mask.hasValue ? Number(m.transition_ms) || 0 : 0;
    this.mask.apply(m, { transitionMs, easing: "linear", source: "push" });
    if (!this.pushedThisConnection) {
      this.pushedThisConnection = true;
      this.updateDerived();
    }
  }

  private async onFreeze(fid: Fid, reason: string): Promise<void> {
    const seq = ++this.freezeSeq;
    this.event("freeze", { fid, reason });
    this.store.dispatch({ type: "freeze", fid });
    this.mask.freeze();
    if (this.freezeTimer) clearTimeout(this.freezeTimer);
    this.freezeTimer = setTimeout(() => {
      if (seq !== this.freezeSeq || !this.store.getState().frozen) return;
      this.freezeTimeouts++;
      console.warn(`[DisplayPage] freeze ${String(fid)} not released within 30 s; unfreezing on our own`);
      this.event("freeze-timeout", { fid });
      this.unfreeze();
    }, FREEZE_SAFETY_MS);

    // Let running transitions finish, hold a clip, make sure the base is decoded; then let it composite.
    await atMost(Promise.all([this.art?.settle() ?? Promise.resolve(), this.mask.settled()]), FREEZE_SETTLE_MAX_MS);
    await atMost(twoFrames(), 200);
    const cur = this.store.getState().frozen;
    if (seq !== this.freezeSeq || !cur || cur.fid !== fid) return; // superseded or released meanwhile
    const state = this.artState();
    this.sendAck({ kind: "frozen", fid, state });
    this.event("frozen-ack", { fid, state });
  }

  private onUnfreeze(fid: Fid): void {
    const cur = this.store.getState().frozen;
    if (!cur) return;
    if (cur.fid !== fid) {
      this.log("unfreeze for another fid ignored", { have: cur.fid, got: fid });
      return;
    }
    this.event("unfreeze", { fid });
    this.unfreeze();
  }

  private unfreeze(): void {
    this.freezeSeq++;
    if (this.freezeTimer) clearTimeout(this.freezeTimer);
    this.freezeTimer = null;
    this.store.dispatch({ type: "unfreeze" });
    this.mask.unfreeze();
  }

  artState(): ArtState {
    const snap = this.art?.snapshot() ?? { base_url: null, kind: "image" as const, clip_t: null };
    return {
      base_url: snap.base_url,
      kind: snap.kind,
      clip_t: snap.clip_t,
      mask: this.mask.drawn(),
      flags: { ...this.flags },
      viewport: { w: window.innerWidth, h: window.innerHeight },
    };
  }

  // -- art stack visibility (the cut) --------------------------------------------------------------

  private setArtHidden(hidden: boolean): void {
    // Revealing: a mask commit still waiting for its frame is made first, with no transition.
    if (!hidden) this.mask.setHidden(false);
    // ONE property change: the guard rule `[data-sm-art][data-hidden] { visibility:hidden }` does the rest.
    this.artStack?.toggleAttribute("data-hidden", hidden);
    if (hidden) this.mask.setHidden(true);
  }

  // -- socket status and the mask poll -------------------------------------------------------------

  private onSocketStatus(st: "connecting" | "up" | "down"): void {
    if (st !== "up") this.pushedThisConnection = this.welcomedThisConnection = false;
    if (st === "up") {
      this.everUp = true;
      this.downSince = null;
    } else if (this.everUp && this.downSince === null) {
      this.downSince = performance.now();
    }
    this.store.dispatch({ type: "socket", status: st });
    this.event("socket", { status: st });
    this.updateDerived();
  }

  /** Recompute everything that follows from socket status, policy and flags. */
  private updateDerived(): void {
    if (this.pollTimer) clearTimeout(this.pollTimer);
    this.pollTimer = null;
    if (!this.started) return;
    // Re-evaluate at the next deadline: the boot wait for a first welcome; 5 s down (poll resumes);
    // 30 min down (the policy lapses).
    const now = performance.now();
    const deadlines: number[] = [];
    if (!this.welcomed) deadlines.push(this.startedAt + POLL_AT_BOOT_AFTER_MS);
    if (this.store.getState().socket !== "up" && this.downSince !== null) {
      deadlines.push(this.downSince + POLL_AFTER_DOWN_MS, this.downSince + POLICY_LAPSE_AFTER_DOWN_MS);
    }
    const next = Math.min(...deadlines.filter((t) => t > now));
    if (Number.isFinite(next)) this.pollTimer = setTimeout(() => this.updateDerived(), next - now + 5);
    this.updatePoll();
    this.reloads.setEnabled(this.effectiveTimedReload());
  }

  /**
   * Rule 9: `policy.timed_reload` applies while the control socket is up, and keeps applying for
   * 30 min after it goes down. Before the first welcome, or once the socket has been down for 30 min,
   * today's schedule applies (timed reloads on). Whatever this says, ReloadScheduler never reloads
   * while live, frozen, mid-attach/teardown or within 60 s of returning to art (canReloadNow).
   */
  private effectiveTimedReload(): boolean {
    if (!this.welcomed) return true;
    const s = this.store.getState();
    if (s.socket !== "up" && this.downSince !== null && performance.now() - this.downSince >= POLICY_LAPSE_AFTER_DOWN_MS) {
      return true;
    }
    return s.policy.timed_reload;
  }

  /** The policy as applied (timed_reload is the effective value). */
  appliedPolicy(): Policy {
    return { ...this.store.getState().policy, timed_reload: this.effectiveTimedReload() };
  }

  /**
   * Poll while the server does not push masks: from 1.5 s after start until the first welcome; while
   * connected with mask_push off (or before the first pushed value on this connection, so a welcome
   * with `mask: null` can't leave the initial black mask up); or once the socket has been down for 5 s.
   * Between a socket opening and its welcome nothing changes. ?nomask never fetches (as today).
   */
  private updatePoll(): void {
    const s = this.store.getState();
    const bootWaitOver = performance.now() - this.startedAt >= POLL_AT_BOOT_AFTER_MS;
    let on: boolean;
    if (this.flags.nomask) on = false;
    else if (s.socket === "up" && this.welcomedThisConnection) on = !(s.policy.mask_push && this.pushedThisConnection);
    else if (s.socket === "up") on = this.poller.isEnabled || (!this.welcomed && bootWaitOver);
    else if (!this.everUp || this.downSince === null) on = !this.welcomed && bootWaitOver;
    else {
      // (A page that has never had a welcome, e.g. its key is refused, polls once the boot wait is over.)
      on = performance.now() - this.downSince >= POLL_AFTER_DOWN_MS || (!this.welcomed && bootWaitOver);
      if (on && s.policy.mask_push && !this.poller.isEnabled) this.event("poll-resumed", {});
    }
    this.poller.setEnabled(on);
  }

  // -- outbound ------------------------------------------------------------------------------------

  private sendAck(ack: Ack): void {
    const sent = this.client.send("ack", ack);
    if (!sent) this.log("ack not sent (control socket down)", ack.kind);
  }

  private sendKioskState(): void {
    if (!this.client.isOpen) return;
    const s = this.store.getState();
    const state: KioskState = {
      heap_mb: heapMb(),
      long_tasks: this.longTasks,
      layer: s.layer,
      frozen: !!s.frozen,
      base_url: this.art?.snapshot().base_url ?? null,
      mask: this.mask.drawn(),
      policy: this.appliedPolicy(),
      next_reload_at: this.reloads.nextReloadAt(),
      live: this.live.stats(),
      overlays_dropped: this.overlaysDropped,
      freeze_timeouts: this.freezeTimeouts,
      reconnects: this.client.reconnects,
      mask_source: this.poller.isEnabled ? "poll" : "push",
      viewport: { w: window.innerWidth, h: window.innerHeight },
      visibility: document.visibilityState === "hidden" ? "hidden" : "visible",
      visibility_changes: this.visibilityChanges,
    };
    this.client.send("kiosk.state", state);
  }

  // -- diagnostics ---------------------------------------------------------------------------------

  private log = (msg: string, detail?: unknown): void => {
    console.info(`[DisplayPage/ctl] ${msg}`, detail ?? "");
  };

  private event(kind: string, detail: Record<string, unknown> = {}): void {
    if (COUNTED_ONLY.has(kind)) {
      this.eventCounts[kind] = (this.eventCounts[kind] ?? 0) + 1;
      return;
    }
    this.events.push({ t: performance.now(), kind, ...detail });
    if (this.events.length > EVENT_LOG_MAX) this.events.splice(0, this.events.length - EVENT_LOG_MAX);
  }
}

// -- one controller per document load ---------------------------------------------------------------
//
// LIVE_V1 §2.2 rule 1 ("one socket per page") and a stable page_load_id: React may run a useMemo
// factory twice (StrictMode) or drop its cache, and StrictMode mounts effects twice, so the page
// never constructs a controller itself. It asks this registry, which keeps one instance per
// configuration and stops it only once nothing has used it for a whole task (a StrictMode
// unmount/remount keeps the same instance and the same socket). A different configuration (an in-app
// navigation to another screen) gets its own controller; the old one stops when released.

interface Entry {
  ctl: DisplayController;
  refs: number;
  stopTimer: ReturnType<typeof setTimeout> | null;
}

const registry = new Map<string, Entry>();
const entryOf = new WeakMap<DisplayController, { key: string; entry: Entry }>();

function configKey(o: DisplayControllerOptions): string {
  return JSON.stringify([o.ctlUrl, o.screenId, o.role, o.ctlKey ?? null]);
}

/** The controller for this configuration (created on first use; safe to call from render). */
export function getDisplayController(opts: DisplayControllerOptions): DisplayController {
  const key = configKey(opts);
  const have = registry.get(key);
  if (have) return have.ctl;
  const entry: Entry = { ctl: new DisplayController(opts), refs: 0, stopTimer: null };
  registry.set(key, entry);
  entryOf.set(entry.ctl, { key, entry });
  return entry.ctl;
}

/** A mounted user of `ctl` (an effect): starts it if needed. */
export function retainDisplayController(ctl: DisplayController): void {
  const e = entryOf.get(ctl)?.entry;
  if (!e) return;
  e.refs++;
  if (e.stopTimer) clearTimeout(e.stopTimer);
  e.stopTimer = null;
  ctl.start();
}

/** The effect's cleanup: `ctl` stops (and is forgotten) if no user retains it again within this task. */
export function releaseDisplayController(ctl: DisplayController): void {
  const found = entryOf.get(ctl);
  if (!found) return;
  const { key, entry } = found;
  entry.refs = Math.max(0, entry.refs - 1);
  if (entry.refs > 0 || entry.stopTimer) return;
  entry.stopTimer = setTimeout(() => {
    entry.stopTimer = null;
    if (entry.refs > 0) return;
    entry.ctl.stop();
    if (registry.get(key) === entry) registry.delete(key);
  }, 0);
}
