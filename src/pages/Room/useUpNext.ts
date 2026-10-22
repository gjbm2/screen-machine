// Up next for a live screen, as state (the renderer's upnext.py, through room_service's /renderer pass-through):
// the art director's forward plan, the next scenes (each with the way it comes in) and the next effects.
//
// Read when the screen's session changes, on each session.upnext event for it (the renderer sends one whenever the
// plan changes; in a room both screens' sessions get the leader's plan), after each change (the reply is the new
// plan), and every 10 s while the page is visible as a backstop. eta_s is seconds at read time, so `at` (when it was
// read) lets the times count down on the page. An older plan never paints over a newer one (rev).

import { useCallback, useEffect, useRef, useState } from "react";
import { RoomApi, RoomApiError } from "./api";
import type { UpNextEdit, UpNextEditResult, UpNextPlan } from "./types";
import type { StreamEvent } from "./useRoomConnection";

const POLL_MS = 10_000;

export interface UpNextRead {
  plan: UpNextPlan;
  /** Date.now() when it was read: eta_s counts from here. */
  at: number;
}

function isPlan(v: unknown): v is UpNextPlan {
  const p = v as UpNextPlan | null;
  return !!p && Array.isArray(p.scenes) && Array.isArray(p.effects);
}

export function useUpNext({
  api,
  sessionId,
  subscribe,
}: {
  api: RoomApi;
  /** The screen's live session; null while it isn't live. */
  sessionId: string | null;
  subscribe: (fn: (e: StreamEvent) => void) => () => void;
}) {
  const [read, setRead] = useState<UpNextRead | null>(null);
  /** null: not read yet; false: this renderer (or session) has no plan to show (404). */
  const [available, setAvailable] = useState<boolean | null>(null);
  /** The last read didn't reach the renderer (what shows is the last plan that came). */
  const [readFailed, setReadFailed] = useState(false);
  const seq = useRef(0);
  const sessionRef = useRef(sessionId);
  sessionRef.current = sessionId;

  /** A plan from a read, an event or a reply; an older one than what's shown is dropped. */
  const take = useCallback((plan: UpNextPlan) => {
    setRead((cur) =>
      cur && plan.planned_by === cur.plan.planned_by && typeof plan.rev === "number" && plan.rev < cur.plan.rev
        ? cur
        : { plan, at: Date.now() },
    );
  }, []);

  const refresh = useCallback(async () => {
    const sid = sessionRef.current;
    if (!sid) return;
    const my = ++seq.current;
    try {
      const p = await api.upnext(sid);
      if (my !== seq.current || sid !== sessionRef.current) return;
      setReadFailed(false);
      if (isPlan(p)) {
        setAvailable(true);
        take(p);
      } else setAvailable(false);
    } catch (e) {
      if (my !== seq.current || sid !== sessionRef.current) return;
      if (e instanceof RoomApiError && e.status === 404) setAvailable(false);
      else setReadFailed(true);
    }
  }, [api, take]);

  // A new session (or none): start from what it says.
  useEffect(() => {
    seq.current++;
    setRead(null);
    setAvailable(null);
    setReadFailed(false);
    if (sessionId) void refresh();
  }, [sessionId, refresh]);

  // Its session.upnext events, a backstop poll while the page is visible, and a read when it comes back into view.
  useEffect(() => {
    if (!sessionId) return;
    const off = subscribe((e) => {
      if (e.type !== "session.upnext" || e.session !== sessionId || !isPlan(e)) return;
      setAvailable(true);
      take(e);
    });
    const poll = setInterval(() => {
      if (document.visibilityState === "visible") void refresh();
    }, POLL_MS);
    const onVisible = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      off();
      clearInterval(poll);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [sessionId, subscribe, refresh, take]);

  /** POST one change. `optimistic` paints it first; a refusal or failure throws (the caller says so in words). */
  const edit = useCallback(
    async (body: UpNextEdit, optimistic?: (p: UpNextPlan) => UpNextPlan): Promise<UpNextEditResult> => {
      const sid = sessionRef.current;
      if (!sid) throw new RoomApiError(409, "not_live", "");
      seq.current++; // a read already in flight must not paint over this
      if (optimistic) setRead((r) => (r ? { ...r, plan: optimistic(r.plan) } : r));
      const res = await api.upnextEdit(sid, body);
      seq.current++;
      if (sid === sessionRef.current && isPlan(res?.upnext)) {
        setAvailable(true);
        setReadFailed(false);
        take(res.upnext);
      }
      return res;
    },
    [api, take],
  );

  return { session: sessionId, read, available, readFailed, refresh, edit };
}

export type UpNextState = ReturnType<typeof useUpNext>;
