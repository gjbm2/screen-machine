// The player's view (GET /show/api/v1/player), polled every 1 s while it prepares or plays and every 5 s otherwise,
// only while the page is visible (and at once when it comes back). Actions (POST /show/api/v1/player) update the
// view at once with what they will most likely do, then take the reply; a poll that left before an action is dropped.
// Shared by the Music page and the Room page's Music player row.

import { useCallback, useEffect, useRef, useState } from "react";
import { listenFlush, listenResume } from "@/pages/Room/listenStream";
import { isOldShow, isUnreached, musicWords, type MusicApi, type PlayerAction, type PlayerView } from "./api";

const POLL_FAST_MS = 1000;
const POLL_SLOW_MS = 5000;

export const PLAYER_BUSY = ["preparing", "playing"];
const fast = (v: PlayerView | null) => !!v && PLAYER_BUSY.includes(v.state);

/** "ok": answering · "old": show_service has no player yet · "unreached": nothing answering · "loading": first look. */
export type PlayerLink = "loading" | "ok" | "old" | "unreached";

/** ok: the player took it (view: its reply) · else `words` says why, in page words. */
export interface ActResult {
  ok: boolean;
  view: PlayerView | null;
  words: string;
}

export interface PlayerHook {
  view: PlayerView | null;
  link: PlayerLink;
  /** The last action's problem, in words (cleared by the next action). */
  error: string | null;
  clearError: () => void;
  /** The action under way ("play", "stop", "next", …), for a spinner. */
  busy: string | null;
  /** Send one action; `guess` is shown until the reply comes. Resolves to the new view, or the problem in words. */
  act: (body: PlayerAction, guess?: Partial<PlayerView>) => Promise<ActResult>;
  refresh: () => void;
}

/** ``enabled`` false: no polling (a component that shares the page's player when there is one: PlayBar). */
export function usePlayer(api: MusicApi, enabled = true): PlayerHook {
  const [view, setViewState] = useState<PlayerView | null>(null);
  const [link, setLink] = useState<PlayerLink>("loading");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const viewRef = useRef<PlayerView | null>(null);
  const gen = useRef(0);
  const kick = useRef<() => void>(() => undefined);

  const setView = useCallback((v: PlayerView | null) => {
    viewRef.current = v;
    setViewState(v);
  }, []);

  useEffect(() => {
    if (!enabled) return;
    let stop = false;
    let inflight = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const ac = new AbortController();
    const schedule = (ms: number) => {
      clearTimeout(timer);
      if (!stop) timer = setTimeout(poll, ms);
    };
    const poll = async () => {
      if (inflight) return;
      if (document.visibilityState !== "visible") {
        schedule(POLL_SLOW_MS);
        return;
      }
      inflight = true;
      const g = gen.current;
      try {
        const v = await api.player(ac.signal);
        if (stop) return;
        if (g === gen.current && v && typeof v === "object" && typeof v.state === "string") {
          setView(v);
          setLink("ok");
        }
      } catch (e) {
        if (stop || (e instanceof DOMException && e.name === "AbortError")) return;
        if (isOldShow(e)) setLink("old");
        else if (isUnreached(e)) setLink("unreached");
        // anything else: keep the last view and look again
      } finally {
        inflight = false;
      }
      schedule(fast(viewRef.current) ? POLL_FAST_MS : POLL_SLOW_MS);
    };
    kick.current = () => {
      clearTimeout(timer);
      void poll();
    };
    const onVisible = () => {
      if (document.visibilityState === "visible") kick.current();
    };
    document.addEventListener("visibilitychange", onVisible);
    void poll();
    return () => {
      stop = true;
      ac.abort();
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
      kick.current = () => undefined;
    };
  }, [api, setView, enabled]);

  const act = useCallback(
    async (body: PlayerAction, guess?: Partial<PlayerView>): Promise<ActResult> => {
      gen.current += 1;
      // a device playing PC22's stream drops what it holds unplayed, so a pause, stop or jump is heard at once
      if (body.action === "pause" || body.action === "stop") listenFlush();
      else if (["seek", "next", "prev"].includes(body.action)) {
        listenFlush(); // the old place's sound is dropped, the new one's plays
        listenResume();
      } else if (body.action === "play" || body.action === "resume") listenResume();
      const before = viewRef.current;
      if (guess && before) setView({ ...before, ...guess });
      setBusy(body.action);
      setError(null);
      try {
        const v = await api.playerAction(body);
        gen.current += 1;
        if (v && typeof v === "object" && typeof v.state === "string") {
          setView(v);
          setLink("ok");
        }
        return { ok: true, view: v ?? null, words: "" };
      } catch (e) {
        if (isOldShow(e)) setLink("old");
        const words = musicWords(e);
        setError(words);
        if (guess && before) setView(before);
        return { ok: false, view: null, words };
      } finally {
        setBusy(null);
        // look again soon (and at the right pace for what it's doing now)
        kick.current();
      }
    },
    [api, setView],
  );

  const clearError = useCallback(() => setError(null), []);
  const refresh = useCallback(() => kick.current(), []);
  return { view, link, error, clearError, busy, act, refresh };
}
