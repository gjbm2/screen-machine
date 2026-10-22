// Status colours for the light, warm Room theme. Muted tints with dark text,
// so every pairing passes WCAG AA (4.5:1) for normal text.

export type Tone = "ok" | "warn" | "bad" | "info" | "live" | "muted";

/** Soft pill / notice: tinted background, matching border, dark text. */
export const TONE_CLASSES: Record<Tone, string> = {
  ok: "border-emerald-200 bg-emerald-50 text-emerald-800",
  warn: "border-amber-200 bg-amber-50 text-amber-900",
  bad: "border-rose-200 bg-rose-50 text-rose-800",
  info: "border-sky-200 bg-sky-50 text-sky-900",
  live: "border-teal-200 bg-teal-50 text-teal-800",
  muted: "border-stone-200 bg-stone-100 text-stone-700",
};

/** Status dots (decorative: the words next to them carry the meaning). */
export const DOT_CLASSES: Record<Tone, string> = {
  ok: "bg-emerald-500",
  warn: "bg-amber-500",
  bad: "bg-rose-500",
  info: "bg-sky-500",
  live: "bg-teal-600",
  muted: "bg-stone-400",
};

/** Coloured text on white or the page background (all ≥ 4.5:1). */
export const TEXT_CLASSES: Record<Tone, string> = {
  ok: "text-emerald-700",
  warn: "text-amber-700",
  bad: "text-rose-700",
  info: "text-sky-700",
  live: "text-teal-700",
  muted: "text-stone-600",
};

/** Phases between the two resting states (idle, live) are a cut in progress. */
export const BUSY_PHASES = new Set(["freezing", "snapshot", "starting", "attaching", "entering", "exiting", "leaving"]);

export function phaseTone(phase?: string | null): Tone {
  if (!phase) return "muted";
  if (phase === "live") return "live";
  if (phase === "failed") return "bad";
  if (phase === "idle") return "muted";
  if (BUSY_PHASES.has(phase)) return "warn";
  return "info";
}

export function stepTone(status?: string | null): Tone {
  switch (status) {
    case "ok":
      return "ok";
    case "running":
      return "warn";
    case "failed":
      return "bad";
    case "unconfirmed":
      return "info";
    default:
      return "muted";
  }
}
