// Whether the docked player (DockedPlayer.tsx) is showing at the bottom of the screen just now. Read by the page parts
// that would otherwise put a second bar there or say the same thing twice: ListenHere's "This device" bar (the dock
// carries its line instead) and the Room page's in-page mini player (NowPlayingRow: the dock is it while music plays).
// No imports, so anything can read it without pulling the player in.

import { useSyncExternalStore } from "react";

let docked = false;
const subs = new Set<() => void>();

export function setDocked(v: boolean): void {
  if (v === docked) return;
  docked = v;
  subs.forEach((f) => f());
}

const subscribe = (f: () => void) => {
  subs.add(f);
  return () => {
    subs.delete(f);
  };
};

/** True while the docked player is on screen. */
export function useDocked(): boolean {
  return useSyncExternalStore(subscribe, () => docked, () => false);
}
