// Imperative mask writer for the controlled (?ctl=) and art-only pages.
//
// Written through element refs inside requestAnimationFrame, never through React state: a burst of
// mask.set coalesces to at most one commit per animation frame and no re-render (LIVE_V1 §2.2 rule 11).
// Compositor-only animation (rule 11), unlike today's page, which transitions background-color and
// so repaints the full 4K layer on every frame of every fade:
//   * brightness: a static black layer whose `opacity` is 1 - brightness (same alpha over black);
//   * warmth: the multiply layer's `opacity` animates; its colour (warm_hex) changes with no
//     transition, at most once per 5 s (a newer colour waits for the 5 s to pass);
//   * both layers have `will-change: opacity` (art/styles.ts).
// While the art stack is hidden under live, values commit with no transition and the colour at once;
// a commit still pending when a cut reveals the art stack is flushed with no transition first.
import { drawnMask, NEUTRAL_MASK, type ArtFlags, type MaskValues } from "../control/protocol";

/** Today's poll fade: 1000 ms ease-in-out (index.tsx brightness/warm layers). */
export const LEGACY_FADE = { transitionMs: 1000, easing: "ease-in-out" } as const;

export interface MaskApplyOptions {
  transitionMs: number;
  easing?: string;
  /** "poll"/"push" (sensor values) are subject to the 5 s warm-colour limit; "state"/"flags" are not. */
  source?: "poll" | "push" | "state" | "flags";
}

/** A warm_hex change applies at most once per this many ms (rule 11). */
export const WARM_HEX_MIN_INTERVAL_MS = 5_000;

/** Today's initial mask: brightness 0 (a fully black layer) until the first value arrives. */
const INITIAL_RAW: MaskValues = { brightness: 0, warm_hex: "#FFFFFF", warm_alpha: 0 };

/** A finite number clamped to 0-1 (CSS clamps opacity the same way, so nothing drawn changes). */
function unit(v: unknown, fallback: number): number {
  return typeof v === "number" && Number.isFinite(v) ? Math.min(1, Math.max(0, v)) : fallback;
}

const HEX = /^#[0-9a-fA-F]{6}$/;

/**
 * Valid fields of `v` over `prev`: a missing or malformed field keeps its previous value. An invalid
 * colour is never accepted, because the browser would ignore it while the ArtState reported it.
 */
export function normaliseMask(v: Partial<MaskValues> | null | undefined, prev: MaskValues = INITIAL_RAW): MaskValues {
  return {
    brightness: unit(v?.brightness, prev.brightness),
    warm_hex: typeof v?.warm_hex === "string" && HEX.test(v.warm_hex) ? v.warm_hex : prev.warm_hex,
    warm_alpha: unit(v?.warm_alpha, prev.warm_alpha),
  };
}

/** Opacity of the black brightness layer: 1 - brightness (today's rgba alpha), 0 with nomask. */
export function brightnessOpacity(m: MaskValues, nomask: boolean): string {
  return nomask ? "0" : String(1 - m.brightness);
}

export class MaskController {
  private bright: HTMLDivElement | null = null;
  private warm: HTMLDivElement | null = null;
  private flags: Pick<ArtFlags, "nomask" | "nocolour">;
  /** Latest raw values accepted (not frozen); `committed` is what is in the DOM. */
  private raw: MaskValues = { ...INITIAL_RAW };
  private committed: MaskValues = { ...INITIAL_RAW };
  private pendingOpts: MaskApplyOptions | null = null;
  private raf: number | null = null;
  private frozen = false;
  private queued: { raw: MaskValues; opts: MaskApplyOptions } | null = null;
  private hidden = false;
  private transitionEndsAt = 0;
  /** performance.now() of the last warm-colour change written to the DOM. */
  private hexChangedAt = Number.NEGATIVE_INFINITY;
  /** Re-commit once a deferred warm-colour change is allowed. */
  private hexTimer: ReturnType<typeof setTimeout> | null = null;
  /** CSS values last written to each layer (null: unknown, write it). Unchanged values are not rewritten. */
  private written: { bright: string | null; hex: string | null; alpha: string | null } = { bright: null, hex: null, alpha: null };
  /** performance.now() of the last DOM commit (tests / latency stats). */
  lastCommitAt = 0;
  /** A real value (push, poll or state) has been received; until then the initial black mask is up. */
  hasValue = false;
  lastSource: MaskApplyOptions["source"] = undefined;
  private waiters: (() => void)[] = [];

  constructor(flags: Pick<ArtFlags, "nomask" | "nocolour">) {
    this.flags = { nomask: flags.nomask, nocolour: flags.nocolour };
  }

  /** Ref callbacks. A newly attached element gets the committed values at once, with no transition. */
  attachBrightness = (el: HTMLDivElement | null): void => {
    this.bright = el;
    this.written.bright = null;
    if (el) this.write(0, "linear", this.committed);
  };

  attachWarm = (el: HTMLDivElement | null): void => {
    this.warm = el;
    this.written.hex = this.written.alpha = null;
    if (el) this.write(0, "linear", this.committed);
  };

  setFlags(flags: Pick<ArtFlags, "nomask" | "nocolour">): void {
    if (flags.nomask === this.flags.nomask && flags.nocolour === this.flags.nocolour) return;
    this.flags = { nomask: flags.nomask, nocolour: flags.nocolour };
    this.schedule(this.hidden ? { transitionMs: 0, source: "flags" } : { ...LEGACY_FADE, source: "flags" });
  }

  /**
   * While the art stack is hidden (live on glass), masks apply with no transition. Call it with
   * `false` just before a cut reveals the art stack: a commit still waiting for its animation frame
   * is made now, with no transition, so the revealed frame already has the latest mask.
   */
  setHidden(hidden: boolean): void {
    const revealing = this.hidden && !hidden;
    this.hidden = hidden;
    if (revealing && this.raf !== null && !this.frozen) {
      cancelAnimationFrame(this.raf);
      this.raf = null;
      const o = this.pendingOpts;
      this.pendingOpts = null;
      this.commit({ ...(o ?? { transitionMs: 0 }), transitionMs: 0, source: "state" });
      this.waiters.splice(0).forEach((w) => w());
    }
  }

  apply(values: Partial<MaskValues>, opts: MaskApplyOptions): void {
    this.hasValue = true;
    const raw = normaliseMask(values, this.frozen && this.queued ? this.queued.raw : this.raw);
    if (this.frozen) {
      this.queued = { raw, opts };
      return;
    }
    this.raw = raw;
    this.schedule(opts);
  }

  /** Apply a full state at once with no transition (art-only renders, cut preparation). */
  applyNow(values: Partial<MaskValues>, flags?: Pick<ArtFlags, "nomask" | "nocolour">): void {
    if (flags) this.flags = { nomask: flags.nomask, nocolour: flags.nocolour };
    this.hasValue = true;
    this.raw = normaliseMask(values, this.raw);
    if (this.raf !== null) cancelAnimationFrame(this.raf);
    this.raf = null;
    this.pendingOpts = null;
    this.commit({ transitionMs: 0, source: "state" });
  }

  freeze(): void {
    this.frozen = true;
    // A deferred warm-colour change must not land after the frozen ack: it waits for the unfreeze.
    if (this.hexTimer) clearTimeout(this.hexTimer);
    this.hexTimer = null;
  }

  /** Leave the freeze and apply the newest queued value (or a deferred colour) as a normal change. */
  unfreeze(): void {
    this.frozen = false;
    const q = this.queued;
    this.queued = null;
    if (q) {
      this.raw = q.raw;
      this.schedule(q.opts);
    } else if (this.raw.warm_hex !== this.committed.warm_hex) {
      this.schedule({ transitionMs: 0, source: "push" });
    }
  }

  /** Resolves once no commit is pending and the last transition has finished. */
  settled(): Promise<void> {
    return new Promise((resolve) => {
      const check = () => {
        if (this.raf !== null) {
          this.waiters.push(check);
          return;
        }
        const left = this.transitionEndsAt - performance.now();
        if (left > 0) setTimeout(check, left + 1);
        else resolve();
      };
      check();
    });
  }

  /** The mask exactly as drawn now (after nomask/nocolour), assuming running transitions complete. */
  drawn(): MaskValues {
    return drawnMask(this.committed, this.flags);
  }

  /** Latest raw value received, including a value queued while frozen. */
  latestRaw(): MaskValues {
    return { ...(this.queued ? this.queued.raw : this.raw) };
  }

  dispose(): void {
    if (this.raf !== null) cancelAnimationFrame(this.raf);
    this.raf = null;
    if (this.hexTimer) clearTimeout(this.hexTimer);
    this.hexTimer = null;
    this.waiters.splice(0).forEach((w) => w());
  }

  private schedule(opts: MaskApplyOptions): void {
    this.pendingOpts = opts;
    if (this.raf !== null) return;
    this.raf = requestAnimationFrame(() => {
      this.raf = null;
      const o = this.pendingOpts ?? opts;
      this.pendingOpts = null;
      this.commit(o);
      this.waiters.splice(0).forEach((w) => w());
    });
  }

  private commit(opts: MaskApplyOptions): void {
    const now = performance.now();
    const t = this.hidden ? 0 : Math.max(0, opts.transitionMs | 0);
    const before = this.drawn();
    const next = { ...this.raw };
    // Rule 11: a sensor-driven warm colour change at most once per 5 s while the art is visible.
    const limited = !this.hidden && (opts.source === "push" || opts.source === "poll");
    if (next.warm_hex !== this.committed.warm_hex && limited && now - this.hexChangedAt < WARM_HEX_MIN_INTERVAL_MS) {
      next.warm_hex = this.committed.warm_hex;
      if (!this.hexTimer && !this.frozen) {
        this.hexTimer = setTimeout(() => {
          this.hexTimer = null;
          // A pending commit will carry the colour itself; otherwise commit just the colour (write()
          // leaves the unchanged opacities, and any transition they are running, alone).
          if (!this.frozen && this.raf === null) this.schedule({ transitionMs: 0, source: "push" });
        }, this.hexChangedAt + WARM_HEX_MIN_INTERVAL_MS - now + 5);
      }
    }
    if (next.warm_hex !== this.committed.warm_hex) this.hexChangedAt = now;
    this.committed = next;
    const after = this.drawn();
    const changed = before.brightness !== after.brightness || before.warm_alpha !== after.warm_alpha;
    this.write(t, opts.easing ?? "linear", this.committed);
    this.lastCommitAt = now;
    this.lastSource = opts.source;
    if (changed && t > 0) this.transitionEndsAt = Math.max(this.transitionEndsAt, now + t);
  }

  /**
   * Opacity-only animation; the colour never transitions (rule 11). Only values that changed are
   * written, so an unchanged layer keeps any transition it is running.
   */
  private write(t: number, easing: string, m: MaskValues): void {
    const { nomask } = this.flags;
    // nomask: today's page never fetches, so its warm layer stays neutral (#FFFFFF at opacity 0).
    const warm = nomask ? NEUTRAL_MASK : m;
    const transition = t > 0 ? `opacity ${t}ms ${easing}` : "none";
    const w = this.written;
    if (this.bright) {
      const op = brightnessOpacity(m, nomask);
      if (op !== w.bright) {
        const s = this.bright.style;
        s.transition = transition;
        s.opacity = op;
        w.bright = op;
      }
    }
    if (this.warm) {
      const s = this.warm.style;
      if (warm.warm_hex !== w.hex) {
        s.backgroundColor = warm.warm_hex;
        w.hex = warm.warm_hex;
      }
      const alpha = String(warm.warm_alpha);
      if (alpha !== w.alpha) {
        s.transition = transition;
        s.opacity = alpha;
        w.alpha = alpha;
      }
    }
  }
}
