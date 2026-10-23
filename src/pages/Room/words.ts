// Plain-language wording for the main screen. Ids, phase names and URLs stay
// out of it; they appear only in the unrolled details.

import { parseSceneSpec, type SceneChoice } from "./catalog";
import { BUSY_PHASES, type Tone } from "./tones";
import type { ModeDef, ScreenStatus, TransitionStep } from "./types";

/** "north-screen" → "North screen", "devtest" → "Devtest". */
export function friendlyName(id: string): string {
  const s = id.replace(/[-_.]+/g, " ").trim();
  return s ? s[0].toUpperCase() + s.slice(1) : id;
}

/** A scene's label from the catalog, else its bare name. */
export function sceneLabel(nameOrKey: string, scenes: SceneChoice[]): string {
  const key = /^(preset|source):/.test(nameOrKey) ? nameOrKey : `preset:${nameOrKey}`;
  const c = scenes.find((x) => x.key === key) ?? scenes.find((x) => x.name === nameOrKey);
  return c?.label ?? nameOrKey.replace(/^(preset|source):/, "");
}

/** Modes may carry a description (not in v1; read if present). */
export function modeDescription(def: ModeDef | undefined): string | undefined {
  const d = (def as (ModeDef & { description?: unknown }) | undefined)?.description;
  return typeof d === "string" && d.trim() ? d.trim() : undefined;
}

export function modeLabel(name: string | null | undefined, modes: Record<string, ModeDef> | null): string {
  if (!name) return "—";
  return modes?.[name]?.label || friendlyName(name);
}

export interface ScreenSummary {
  tone: Tone;
  /** "Showing art", "Live · Fractal zoom", "Switching to live…", "Live didn't start · showing art". */
  text: string;
  /** A short secondary note, e.g. "Not connected". */
  hint?: string;
  busy: boolean;
  problem: boolean;
  live: boolean;
}

export function summariseScreen(screen: ScreenStatus, scenes: SceneChoice[], streamReachable?: boolean): ScreenSummary {
  const obs = screen.observed ?? {};
  const phase = obs.phase ?? null;
  const desired = screen.desired;
  const desiredLive = desired && typeof desired === "object" && "live" in desired ? desired.live : null;

  if (phase === "failed") {
    return { tone: "bad", text: "Live didn't start · showing art", busy: false, problem: true, live: false };
  }
  if (phase && BUSY_PHASES.has(phase)) {
    const text = desiredLive ? "Switching to live…" : desired === "art" ? "Going back to art…" : "Changing…";
    return { tone: "warn", text, busy: true, problem: false, live: false };
  }
  if (phase === "live" || obs.layer === "live") {
    const parsed = parseSceneSpec(desiredLive?.scene);
    const name = parsed ? sceneLabel(parsed.key, scenes) : null;
    return { tone: "live", text: name ? `Live · ${name}` : "Live", busy: false, problem: false, live: true };
  }
  if (desiredLive) {
    // Why live hasn't started yet, when the room knows (live starts without the TV page).
    const hint = (obs.detail ?? "").startsWith("the renderer is restarting")
      ? "Live pictures are restarting · back in a moment"
      : streamReachable === false
        ? "Waiting for the live pictures to come back"
        : obs.retry_in_s
          ? "Trying again in a moment"
          : undefined;
    return { tone: "warn", text: "Waiting to go live…", hint, busy: true, problem: false, live: false };
  }
  // Showing art. Whether its page is on room control, or live is allowed for it, only matters
  // for going live, so that is explained under the card's "More", not here.
  return {
    tone: "muted",
    text: "Showing art",
    busy: false,
    problem: false,
    live: false,
  };
}

/** A screen's last problem as one sentence, by the kind of error (the raw text stays in Technical details). */
export function problemWords(lastError: string | null | undefined, phase: string | null): string {
  const e = (lastError ?? "").trim();
  if (e.startsWith("fallback:")) return "Live stopped by itself.";
  if (/^\d+ fallbacks/.test(e)) return "Live keeps stopping.";
  if (e.startsWith("entry")) return "Live didn't start at first.";
  if (e.startsWith("exit")) return "The cut back to art wasn't smooth.";
  if (e.startsWith("live change refused")) return "That change didn't take; the screen kept what it had.";
  if (e.startsWith("internal:")) return "Something went wrong inside the room.";
  return phase === "failed" ? "The last change didn't work." : "The last try had a problem.";
}

/** A step describe can't word (a signal, or before describe loads): its target and action as they are. The real words
 *  come from describe (modeWords.stepSentence). */
export function stepText(step: TransitionStep): string {
  return `${friendlyName(step.subsystem ?? "")}: ${String(step.action ?? "")}`;
}

/** A failed step as the end of a sentence, for steps describe can't word. */
export function stepFailure(step: TransitionStep): string {
  return `${stepText(step)} didn't work`;
}

/** A screen step the room is still taking live past its deadline (room_service: "unconfirmed", detail "going live…"):
 *  in progress, not a problem. */
export function stepGoingLive(s: Pick<TransitionStep, "subsystem" | "status" | "detail">): boolean {
  return s.status === "unconfirmed" && !!s.subsystem?.startsWith("screens.") && /^going live/i.test(s.detail?.trim() ?? "");
}

export const STEP_WORDS: Record<string, string> = {
  pending: "Waiting",
  running: "Working…",
  ok: "Done",
  failed: "Didn't work",
  unconfirmed: "Sent, not confirmed",
  cancelled: "Cancelled",
  skipped: "Skipped",
};
