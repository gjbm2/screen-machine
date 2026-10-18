// The rig view, polled every 2 s while the page is visible (and at once when it comes back into view), and the light
// types, read once and again whenever the Add flow asks. The last good copy is kept through a failed read. The Room
// map's space is read on demand (and after each change); the live view (where every beam lands) at 4 Hz while shown.

import { useCallback, useEffect, useRef, useState } from "react";
import { explainLightsError, type LightsApi } from "./api";
import {
  lightsUiFrom,
  LIGHTS_UI_DEFAULT,
  parseLive,
  parseSpace,
  placeablesFromRig,
  rigProblems,
  rowsFromRig,
  typesFrom,
  type LightRow,
  type LightsUi,
  type LightType,
  type LiveView,
  type Placeable,
  type RoomSpace,
} from "./lib";

const POLL_MS = 2000;

export interface RigState {
  rows: LightRow[] | null;
  /** Every light and screen, for the Room map. */
  all: Placeable[];
  /** Move it's numbers (show.yaml lights.jog). */
  ui: LightsUi;
  /** The registry's own problems (a hand-file clash, a bad entry), in its words. */
  problems: string[];
  /** The rig isn't running (enabled: false), with its reason. */
  off: string | null;
  error: string | null;
  /** When the last good read came back (ms). */
  at: number | null;
  /** Read again now (after a save or a remove). */
  refresh: () => void;
}

export function useRig(api: LightsApi): RigState {
  const [rows, setRows] = useState<LightRow[] | null>(null);
  const [all, setAll] = useState<Placeable[]>([]);
  const [ui, setUi] = useState<LightsUi>(LIGHTS_UI_DEFAULT);
  const [problems, setProblems] = useState<string[]>([]);
  const [off, setOff] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [at, setAt] = useState<number | null>(null);
  const kick = useRef<() => void>(() => {});

  useEffect(() => {
    let stop = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let inFlight = false;
    // A kick while a read is out reads again as soon as it's back (one chain of timers, never two).
    let again = false;
    const ac = new AbortController();
    const poll = async () => {
      if (stop) return;
      if (inFlight) {
        again = true;
        return;
      }
      clearTimeout(timer);
      if (document.visibilityState === "visible") {
        inFlight = true;
        try {
          const rig = await api.rig(ac.signal);
          if (stop) return;
          const r = rig && typeof rig === "object" ? (rig as Record<string, unknown>) : {};
          setOff(r.enabled === false ? (typeof r.why === "string" ? r.why : "") : null);
          setRows(rowsFromRig(rig));
          setAll(placeablesFromRig(rig));
          setUi(lightsUiFrom(rig));
          setProblems(rigProblems(rig));
          setError(null);
          setAt(Date.now());
        } catch (e) {
          if (stop || (e instanceof DOMException && e.name === "AbortError")) return;
          setError(explainLightsError(e));
        } finally {
          inFlight = false;
        }
      }
      if (stop) return;
      if (again) {
        again = false;
        void poll();
        return;
      }
      timer = setTimeout(poll, POLL_MS);
    };
    kick.current = () => void poll();
    const onVisible = () => {
      if (document.visibilityState === "visible") void poll();
    };
    document.addEventListener("visibilitychange", onVisible);
    void poll();
    return () => {
      stop = true;
      ac.abort();
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
      kick.current = () => {};
    };
  }, [api]);

  const refresh = useCallback(() => kick.current(), []);
  return { rows, all, ui, problems, off, error, at, refresh };
}

export interface TypesState {
  types: LightType[] | null;
  error: string | null;
  /** Read them again (the Add flow does when they failed or haven't come yet). */
  reload: () => void;
}

export function useLightTypes(api: LightsApi): TypesState {
  const [types, setTypes] = useState<LightType[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const ac = new AbortController();
    api
      .types(ac.signal)
      .then((raw) => {
        setTypes(typesFrom(raw));
        setError(null);
      })
      .catch((e) => {
        if (e instanceof DOMException && e.name === "AbortError") return;
        setError(explainLightsError(e));
      });
    return () => ac.abort();
  }, [api, tick]);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { types, error, reload };
}

export interface SpaceState {
  space: RoomSpace | null;
  error: string | null;
  /** Read it again (after a change). */
  reload: () => void;
  /** Put a fresh answer in place (a write's answer is the new room). */
  set: (space: RoomSpace) => void;
}

/** The Room map's room (GET /show/api/v1/rig/space): read while `on`, again on reload and when the page comes back. */
export function useSpace(api: LightsApi, on = true): SpaceState {
  const [space, setSpace] = useState<RoomSpace | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!on) return;
    const ac = new AbortController();
    api
      .space(ac.signal)
      .then((raw) => {
        setSpace(parseSpace(raw));
        setError(null);
      })
      .catch((e) => {
        if (e instanceof DOMException && e.name === "AbortError") return;
        setError(explainLightsError(e));
      });
    return () => ac.abort();
  }, [api, on, tick]);
  useEffect(() => {
    if (!on) return;
    const onVisible = () => {
      if (document.visibilityState === "visible") setTick((t) => t + 1);
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [on]);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { space, error, reload, set: setSpace };
}

const LIVE_MS = 250;

/** Where every beam lands and the flow (GET /show/api/v1/rig/live), 4 Hz while `on` and the page is visible. */
export function useLive(api: LightsApi, on: boolean): LiveView | null {
  const [live, setLive] = useState<LiveView | null>(null);
  useEffect(() => {
    if (!on) return;
    let stop = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const ac = new AbortController();
    const poll = async () => {
      if (stop) return;
      if (document.visibilityState === "visible") {
        try {
          setLive(parseLive(await api.live(ac.signal)));
        } catch {
          // the last good view stays; the next poll tries again
        }
      }
      if (!stop) timer = setTimeout(poll, LIVE_MS);
    };
    void poll();
    return () => {
      stop = true;
      ac.abort();
      clearTimeout(timer);
    };
  }, [api, on]);
  return live;
}
