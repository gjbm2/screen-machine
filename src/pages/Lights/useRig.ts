// The rig view, polled every 2 s while the page is visible (and at once when it comes back into view), and the light
// types, read once and again whenever the Add flow asks. The last good copy is kept through a failed read.

import { useCallback, useEffect, useRef, useState } from "react";
import { explainLightsError, type LightsApi } from "./api";
import { rigProblems, rowsFromRig, typesFrom, type LightRow, type LightType } from "./lib";

const POLL_MS = 2000;

export interface RigState {
  rows: LightRow[] | null;
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
  return { rows, problems, off, error, at, refresh };
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
