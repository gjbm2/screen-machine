// Display page <-> room_service control protocol, v1.
// Source of truth: docs/av/contracts/LIVE_V1.md §2. Changes are additive only.

export const PROTOCOL_VERSION = 1;

/** `{"v": 1, "type": "<type>", "data": {…}}` */
export interface Envelope<T = unknown> {
  v: number;
  type: string;
  data: T;
}

export type Fid = string | number;
export type Role = "kiosk" | "viewer" | "lab";
export type Fit = "fit" | "fill";

/** Mask values as the page draws them (brightness 0-1; warm multiply layer colour + opacity). */
export interface MaskValues {
  brightness: number;
  warm_hex: string;
  warm_alpha: number;
}

export interface MaskSet extends MaskValues {
  rev: number;
  transition_ms: number;
}

/** LIVE_V1 §2.1 `Policy`; a `policy` message may carry only the fields that changed. */
export interface Policy {
  live_enabled: boolean;
  mask_push: boolean;
  /** Today's timed hard reloads (rule 9). Defaults to true (room.yaml default; also before any welcome). */
  timed_reload: boolean;
}

export const DEFAULT_POLICY: Policy = { live_enabled: false, mask_push: false, timed_reload: true };

export interface LiveAttach {
  session: string;
  url: string;
  codec: string;
  w: number;
  h: number;
  fps: number;
  target_buffer_ms: number;
  /**
   * Additive. Set only for a subscriber (a viewer, or glass that is not the driven TV): play this session even if
   * this page already ended it itself (a stall, a hidden tab, a warm-up timeout). See DisplayController.attachLive.
   */
  rejoin?: boolean;
}

export interface Welcome {
  epoch: string | number;
  screen: string;
  policy: Policy;
  mask: MaskSet | null;
  live: null | { session: string; attach: LiveAttach | (Omit<LiveAttach, "session"> & { session?: string }); phase: string };
}

export interface ArtFlags {
  nomask: boolean;
  nocolour: boolean;
  fit: Fit;
}

/** What is on the art stack, exactly (mask as drawn, after nomask/nocolour). LIVE_V1 §2.1. */
export interface ArtState {
  base_url: string | null;
  kind: "image" | "video";
  clip_t: number | null;
  mask: MaskValues;
  flags: ArtFlags;
  viewport: { w: number; h: number };
}

export interface Caps {
  mse_hevc: boolean;
  mse_h264: boolean;
  mse_av1: boolean;
}

export interface Hello {
  role: Role;
  screen: string;
  page_load_id: string;
  build: string;
  caps: Caps;
  flags: ArtFlags;
  /** Per-screen control key from the kiosk URL's `&ctlkey=` (LIVE_V1 §3, machine channels). Never logged. */
  key?: string;
}

export type ArtVisibleReason = "exit" | "exit-timeout" | "stall" | "abort" | "error";

export type Ack =
  | { kind: "frozen"; fid: Fid; state: ArtState }
  | { kind: "live_visible"; session: string; media_time: number }
  | { kind: "art_visible"; session: string; media_time: number | null; reason: ArtVisibleReason }
  | { kind: "live_failed"; session: string; reason: string };

export interface LiveStats {
  session: string;
  fps: number;
  presented: number;
  dropped: number;
  stalls: number;
  buffered_ms: number;
  edge_jumps: number;
  // additive (not in the frozen v1 table, ignored by older readers)
  phase?: string;
  gap_skips?: number;
}

export interface KioskState {
  /** performance.memory.usedJSHeapSize in MB (null where the browser does not expose it). */
  heap_mb: number | null;
  /** Long tasks (> 50 ms) observed since the page loaded. */
  long_tasks: number;
  layer: "art" | "live";
  frozen: boolean;
  base_url: string | null;
  mask: MaskValues;
  /** Policy as applied (timed_reload is the effective value, rule 9). */
  policy: Policy;
  /** ISO time of the next timed reload, or null when timed reloads are off. */
  next_reload_at: string | null;
  live: LiveStats | null;
  // additive
  overlays_dropped?: number;
  freeze_timeouts?: number;
  reconnects?: number;
  mask_source?: "poll" | "push";
  /** The glass's size in CSS px: room_service shapes the live render to it. */
  viewport?: { w: number; h: number };
  /** document.visibilityState: "hidden" (a background tab, a minimised or covered window) paints no video. */
  visibility?: "visible" | "hidden";
  /** visibilitychange events since the page loaded. */
  visibility_changes?: number;
}

/** Neutral mask: no darkening, no tint (what nomask draws). */
export const NEUTRAL_MASK: MaskValues = { brightness: 1, warm_hex: "#FFFFFF", warm_alpha: 0 };

/**
 * The mask exactly as the page draws it, given the raw values and the page flags.
 * nomask: the brightness layer is transparent and the warm layer neutral (today's page never
 * fetches a mask with ?nomask, so its warm layer stays #FFFFFF at opacity 0).
 * nocolour: the warm layer is not rendered at all.
 */
export function drawnMask(raw: MaskValues, flags: Pick<ArtFlags, "nomask" | "nocolour">): MaskValues {
  if (flags.nomask) return { ...NEUTRAL_MASK };
  if (flags.nocolour) return { brightness: raw.brightness, warm_hex: "#FFFFFF", warm_alpha: 0 };
  return { brightness: raw.brightness, warm_hex: raw.warm_hex, warm_alpha: raw.warm_alpha };
}

export function envelope<T>(type: string, data: T): string {
  const msg: Envelope<T> = { v: PROTOCOL_VERSION, type, data };
  return JSON.stringify(msg);
}

export function parseEnvelope(raw: unknown): Envelope | null {
  if (typeof raw !== "string") return null;
  try {
    const m = JSON.parse(raw);
    if (!m || typeof m !== "object" || typeof m.type !== "string") return null;
    if (m.v !== PROTOCOL_VERSION) return null; // unsupported major: ignore
    return { v: m.v, type: m.type, data: m.data ?? {} };
  } catch {
    return null;
  }
}
