// What the screen shows (art/screenState.ts): output/<screen>.state.json, read by the page itself at start, every
// POLL_MS (a conditional request: nothing comes back unless it changed), when the tab wakes, when the network comes
// back and after the control socket (re)connects, and taken from room_service's `art.state` push when that arrives
// first. Whatever arrives is the whole state; the newer one wins. `available` stays false while the screen has no
// document yet (nothing published since it was introduced): the page then reads the screen's files as before, but
// only once `checked` (the first read has answered), so a page never starts on the screen's old files first (25 Nov:
// a page load mounted the screen's .mp4 for a moment before the document arrived).
import { useEffect, useRef, useState } from "react";
import type { DisplayController } from "../control/DisplayController";
import { isState, newer, POLL_MS, type ScreenState } from "../art/screenState";
import { startTicker } from "../control/ticker";

export default function useScreenState(name: string | null, ctl: DisplayController | null) {
  const [state, setState] = useState<ScreenState | null>(null);
  const [available, setAvailable] = useState(false);
  const [checked, setChecked] = useState(false);
  const reading = useRef(false);

  useEffect(() => {
    if (!name || !ctl) return;
    let alive = true;
    const take = (next: unknown) => {
      if (!alive || !isState(next)) return;
      setAvailable(true);
      setState((cur) => newer(cur, next));
    };
    const read = async () => {
      if (reading.current) return;
      reading.current = true;
      try {
        const r = await fetch(`/output/${encodeURIComponent(name)}.state.json`, { cache: "no-cache" });
        if (r.ok) take(await r.json());
      } catch {
        // offline or the server restarting: the next read (or a push) brings it
      } finally {
        reading.current = false;
        if (alive) setChecked(true);
      }
    };
    void read();
    const stopTicker = startTicker(POLL_MS, () => void read());     // keeps time in a hidden tab too
    const onVisible = () => document.visibilityState === "visible" && void read();
    const onOnline = () => void read();
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("online", onOnline);
    const off = ctl.onArtState((pushed) => (pushed ? take(pushed) : void read()));
    return () => {
      alive = false;
      stopTicker();
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("online", onOnline);
      off();
    };
  }, [name, ctl]);

  return { state, available, checked };
}
