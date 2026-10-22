// The virtual DMX fixtures view (room_service dmx/api.py: GET /api/v1/fixtures), polled. The page runs one
// poll and hands it to every room's LightsSection (Fixtures.tsx).

import { useCallback, useEffect, useRef, useState } from "react";
import { explainError, type RoomApi } from "./api";
import type { FixturesView } from "./types";

const POLL_MS = 1500;

/** The fixtures view, polled every 1.5 s while the tab is visible. One poll can feed several LightsSections. */
export function useFixtures(api: RoomApi, enabled: boolean) {
  const [view, setView] = useState<FixturesView | null>(null);
  const [missing, setMissing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const tick = useRef(0);
  const refresh = useCallback(() => {
    tick.current++;
  }, []);
  useEffect(() => {
    if (!enabled) return;
    let stop = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const ac = new AbortController();
    const poll = async () => {
      if (document.visibilityState === "visible") {
        try {
          const v = await api.fixtures(ac.signal);
          if (stop) return;
          if (v === null) setMissing(true);
          else {
            setView(v);
            setMissing(false);
          }
          setError(null);
        } catch (e) {
          if (stop || (e instanceof DOMException && e.name === "AbortError")) return;
          setError(explainError(e));
        }
      }
      if (!stop) timer = setTimeout(poll, POLL_MS);
    };
    void poll();
    return () => {
      stop = true;
      ac.abort();
      clearTimeout(timer);
    };
  }, [api, enabled]);
  return { view, missing, error, refresh };
}

export type FixturesState = ReturnType<typeof useFixtures>;
