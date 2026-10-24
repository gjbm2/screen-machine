// What this device remembers, for Settings › This device (kept in the browser only; nothing here reaches the room).
// The keys belong to the pages that use them (listed here so Settings can show and reset them, never to set them:
// each choice is still made where it's used, e.g. the piano sound on its chips, the score view on the score).

import { storageGet, storageSet } from "@/pages/Room/api";

/** This device's own name, for other devices' Play on lists (listenStream.ts deviceName reads it). */
export { DEVICE_NAME_KEY } from "@/pages/Room/listenStream";

/** Where the app opens at /start: "/room" or "/music". */
export const HOME_KEY = "sm_home";
export type Home = "/room" | "/music";

export function readHome(): Home | null {
  const v = storageGet(HOME_KEY);
  return v === "/room" || v === "/music" ? v : null;
}

export function writeHome(h: Home) {
  storageSet(HOME_KEY, h);
}

/** Every key a group of remembered choices is kept under. */
export const KEYS = {
  playsHere: ["sm_room_listen_here", "sm_room_listen_also"],
  piano: ["sm_room_piano_sound", "sm_room_piano_output", "sm_room_piano_speed"],
  scenes: ["sm_room_cue_via", "sm_room_cue_beat", "sm_room_fx_add"],
  score: ["music.score.mode", "music.score.staff", "music.score.paper"],
  tabs: ["music.tab", "lights.tab"],
} as const;

/** The Room page's remembered folds (Section.tsx: sm_room_fold_<id>). */
export function foldKeys(): string[] {
  const out: string[] = [];
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k && k.startsWith("sm_room_fold_")) out.push(k);
    }
  } catch {
    /* storage blocked: nothing remembered */
  }
  return out;
}

export function clearKeys(keys: readonly string[]) {
  for (const k of keys) storageSet(k, null);
}

/** The pages hold what they read at start, so a reset takes hold on a fresh load of the app. */
export function reloadApp() {
  window.location.reload();
}
