// How the page describes its link to room_service: a one-word state for the
// header, and the fuller wording for the menu.

import type { Tone } from "./tones";
import type { ConnectionState } from "./useRoomConnection";

/** How old a polled status may be before the page counts as disconnected. */
export const STALE_MS = 6_000;

export interface LinkState {
  tone: Tone;
  /** One word for the header. */
  word: string;
  /** A plain sentence for the menu. */
  sentence: string;
  pulse: boolean;
}

export function linkState(conn: ConnectionState, now: number): LinkState {
  if (conn.authRequired) {
    return { tone: "warn", word: "Locked", sentence: "This room needs a key before you can control it from here.", pulse: false };
  }
  const fresh = conn.lastStatusAt !== null && now - conn.lastStatusAt < STALE_MS;
  if (conn.ws === "open") return { tone: "ok", word: "Connected", sentence: "Connected, with live updates.", pulse: false };
  if (fresh && conn.polling) {
    return {
      tone: "warn",
      word: "Reconnecting",
      sentence: "Connected, but live updates are down, so the page checks every 2 seconds.",
      pulse: true,
    };
  }
  if (conn.lastStatusAt === null && conn.ws === "connecting") {
    return { tone: "muted", word: "Connecting", sentence: "Connecting to the room…", pulse: true };
  }
  return { tone: "muted", word: "Offline", sentence: "Can't reach the room service right now.", pulse: false };
}

/** True while the page has fresh status and may send commands. */
export function isConnected(conn: ConnectionState, now: number): boolean {
  return !conn.authRequired && conn.lastStatusAt !== null && (conn.ws === "open" || now - conn.lastStatusAt < STALE_MS);
}

export function ago(now: number, t: number | null): string {
  if (t === null) return "never";
  const s = Math.max(0, Math.round((now - t) / 1000));
  return s < 2 ? "just now" : s < 120 ? `${s} s ago` : `${Math.round(s / 60)} min ago`;
}
