// A live screen's look, as state: the scene on glass, the auto director, direction, palette and
// the effects bus (room_service live_api.py, which passes the renderer's session API through).
//
// It lives in the always-mounted screen card, next to useScreenControls. The look is read when
// the screen's session changes, again shortly after any stream event for that session (scene
// changes, effects, the director, palette), and every 20 s as a backstop. Changes answer with
// the new look, so the controls follow at once; a refused change re-reads it.
//
// Scope (owner, 17 Oct): a control on a screen's card acts on that screen only. The renderer applies a look PATCH to
// every screen in the room unless it is sent with scope "screen", which needs its look_scope feature and a
// room_service that takes it; `scoped` says the card may send it. Without it the card's look controls say "both
// screens". The effects bus is always one screen's (a PUT), except the room's picks, which the room's leader owns.

import { useCallback, useEffect, useRef, useState } from "react";
import { explainError, RoomApi, RoomApiError } from "./api";
import { echo, putFxStack, swapPicked, withOnTop } from "./roomFx";
import type { CueBody, Direction, FxBus, FxEntry, FxNextBody, FxPut, Look, LookPatch } from "./types";
import type { NewLogEntry, StreamEvent } from "./useRoomConnection";

const EVENT_REFRESH_MS = 350;
const POLL_MS = 20_000;
/** The master slider sends at most this often while dragged; the last value always goes. */
const MASTER_INTERVAL_MS = 200;

export const REFRESH_ON = new Set([
  "session.transition",
  "session.cue",
  "session.fx",
  "session.director",
  "session.palette",
  "session.state",
]);

/** room_service turned down `scope` (it predates it): the page stops sending it and says "both screens" again. */
export const lookScope = { refused: false };

function scopeRefusal(e: unknown): boolean {
  return e instanceof RoomApiError && e.status === 400 && /may contain only/i.test(e.detail) && !/scope/.test(e.detail);
}

/** A direction change over the current one (direction.fx merged too, as room_service does). */
export function mergeDirection(cur: Direction | null | undefined, d: Partial<Direction>): Direction {
  const out: Direction = { ...(cur ?? {}), ...d };
  if (d.fx && cur?.fx) out.fx = { ...cur.fx, ...d.fx };
  return out;
}

export interface LiveLookArgs {
  api: RoomApi;
  screenId: string;
  /** The screen's current session (status observed.session), when it is live. */
  sessionId: string | null;
  /** False on a room_service without the live routes (the page falls back to the basic controls). */
  enabled: boolean;
  subscribe: (fn: (e: StreamEvent) => void) => () => void;
  addLog: (e: NewLogEntry) => void;
  reportError: (e: unknown) => void;
  /** Look PATCHes go with scope "screen" (the renderer has look_scope and this card's room has other screens). */
  scoped?: boolean;
}

export function useLiveLook({ api, screenId, sessionId, enabled, subscribe, addLog, reportError, scoped = false }: LiveLookArgs) {
  const [look, setLook] = useState<Look | null>(null);
  /** null: unknown yet; false: this room_service has no look route. */
  const [available, setAvailable] = useState<boolean | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  /** A word about the last change that went through but not quite as asked ("your oldest came off"). */
  const [info, setInfo] = useState<string | null>(null);
  const seq = useRef(0);
  const sessionRef = useRef(sessionId);
  sessionRef.current = sessionId;

  const refresh = useCallback(async () => {
    if (!sessionRef.current || !enabled) return;
    const my = ++seq.current;
    try {
      const l = await api.look(screenId);
      if (my !== seq.current) return;
      setLook(l);
      setAvailable(true);
    } catch (e) {
      if (my !== seq.current) return;
      if (e instanceof RoomApiError && e.status === 404) setAvailable(false);
      else if (e instanceof RoomApiError && e.code === "not_live") setLook(null);
      reportError(e);
    }
  }, [api, screenId, enabled, reportError]);

  // A new session (or none): start from what it says.
  useEffect(() => {
    seq.current++;
    setLook(null);
    setError(null);
    setInfo(null);
    if (sessionId && enabled) void refresh();
  }, [sessionId, enabled, refresh]);

  // Stream events for this session, coalesced; plus a slow backstop poll.
  useEffect(() => {
    if (!sessionId || !enabled) return;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const off = subscribe((e) => {
      if (e.session !== sessionId || !REFRESH_ON.has(String(e.type))) return;
      if (!timer)
        timer = setTimeout(() => {
          timer = null;
          void refresh();
        }, EVENT_REFRESH_MS);
    });
    const poll = setInterval(() => void refresh(), POLL_MS);
    return () => {
      off();
      clearInterval(poll);
      if (timer) clearTimeout(timer);
    };
  }, [sessionId, enabled, subscribe, refresh]);

  const run = useCallback(
    async (label: string, fn: () => Promise<void>) => {
      setBusy(label);
      setError(null);
      setInfo(null);
      try {
        await fn();
        addLog({ type: "ui", screen: screenId, summary: `${screenId}: ${label} accepted` });
      } catch (e) {
        reportError(e);
        const msg = explainError(e);
        setError(msg);
        addLog({ type: "ui", level: "error", screen: screenId, summary: `${screenId}: ${label} refused: ${msg}` });
        void refresh();
      } finally {
        setBusy(null);
      }
    },
    [addLog, refresh, reportError, screenId],
  );

  /** PATCH the session's look; the reply is the new look. `optimistic` paints the change first. */
  const patch = useCallback(
    (label: string, body: LookPatch, optimistic?: (l: Look) => Look) => {
      if (optimistic) setLook((l) => (l ? optimistic(l) : l));
      seq.current++; // an older read in flight must not paint over this
      return run(label, async () => {
        const send: LookPatch = scoped && !lookScope.refused ? { ...body, scope: "screen" } : body;
        let l: Look;
        try {
          l = await api.patchLook(screenId, send);
        } catch (e) {
          // Never fall back to the room-wide change: the tap was for this screen only.
          if (send.scope && scopeRefusal(e)) {
            lookScope.refused = true;
            throw new Error("This screen can't change on its own yet; it would change both. Use the room's controls above.");
          }
          throw e;
        }
        seq.current++;
        setLook(l);
      });
    },
    [api, run, screenId, scoped],
  );

  const cue = useCallback(
    (label: string, body: CueBody) =>
      run(label, async () => {
        await api.cue(screenId, body);
        setTimeout(() => void refresh(), EVENT_REFRESH_MS);
      }),
    [api, refresh, run, screenId],
  );

  /** Next effect (body: a chosen effect, or the slot to swap; an older renderer ignores it). */
  const nextEffect = useCallback(
    (body?: FxNextBody, label = "next effect") =>
      run(label, async () => {
        const sid = sessionRef.current;
        if (!sid) throw new Error("This screen isn't live.");
        await api.nextEffect(sid, body);
        setTimeout(() => void refresh(), EVENT_REFRESH_MS);
      }),
    [api, refresh, run],
  );

  /** Swap the room's pick in `slot` (its id on this screen) for another: on this screen only when `scoped` (its
   *  effects become its own), else the room's. */
  const swapEffect = useCallback(
    (slot: string, label: string) =>
      run(`swapping ${label}`, async () => {
        const sid = sessionRef.current;
        if (!sid) throw new Error("This screen isn't live.");
        await swapPicked(api, sid, slot, scoped ? "screen" : undefined);
        setTimeout(() => void refresh(), EVENT_REFRESH_MS);
      }),
    [api, refresh, run, scoped],
  );

  /** Take one of the room's picks off this screen only (scope "screen": needs look_scope; its effects become its own). */
  const takeOffHere = useCallback(
    (effect: string, label: string) =>
      run(`taking ${label} off`, async () => {
        const sid = sessionRef.current;
        if (!sid) throw new Error("This screen isn't live.");
        await api.fxClear(sid, { effect, scope: "screen" });
        setTimeout(() => void refresh(), EVENT_REFRESH_MS);
      }),
    [api, refresh, run],
  );

  // ── Effects bus: PUT replaces the whole stack, so read, change, write (if_rev; one retry on a race) ──
  const putFx = useCallback(
    async (change: (stack: FxEntry[]) => FxPut["stack"], extra: { master?: number; fade_s?: number } = {}) => {
      const res: FxBus = await putFxStack(api, screenId, change, extra);
      seq.current++;
      setLook((l) => (l ? { ...l, fx: { ...(l.fx ?? {}), ...res } } : l));
    },
    [api, screenId],
  );

  /** Add an effect on top, as yours, on this screen. At `max` your oldest comes off in the same change. */
  const addEffect = useCallback(
    (preset: string, label: string, max: number, onDropped?: (e: FxEntry) => string) =>
      run(`add ${label}`, async () => {
        const out: { dropped: FxEntry | null } = { dropped: null };
        await putFx((stack) => {
          const r = withOnTop(stack, preset, max);
          out.dropped = r.dropped;
          return r.stack;
        });
        if (out.dropped && onDropped) setInfo(onDropped(out.dropped));
      }),
    [putFx, run],
  );

  const removeEffect = useCallback(
    (id: string, label: string) => {
      setLook((l) => (l?.fx ? { ...l, fx: { ...l.fx, stack: (l.fx.stack ?? []).filter((e) => e.id !== id) } } : l));
      return run(`remove ${label}`, () => putFx((stack) => stack.filter((e) => e.id !== id).map(echo)));
    },
    [putFx, run],
  );

  /** One of your effects: pause it (bypass, it keeps its place), its own strength, or another preset in its place. */
  const setEffect = useCallback(
    (id: string, label: string, change: { bypass?: boolean; level?: number; preset?: string }) => {
      if (change.preset === undefined)
        setLook((l) =>
          l?.fx ? { ...l, fx: { ...l.fx, stack: (l.fx.stack ?? []).map((e) => (e.id === id ? { ...e, ...change } : e)) } } : l,
        );
      return run(label, () =>
        putFx((stack) =>
          stack.map((e) => {
            if (e.id !== id) return echo(e);
            if (change.preset) return { id: e.id, preset: change.preset, level: e.level, bypass: e.bypass };
            return { ...echo(e), ...change };
          }),
        ),
      );
    },
    [putFx, run],
  );

  const setFxFade = useCallback(
    (fade_s: number) => run("effects fade time", () => putFx((stack) => stack.map(echo), { fade_s })),
    [putFx, run],
  );

  // The master slider: one request in flight, the latest value next.
  const masterPending = useRef<number | null>(null);
  const masterBusy = useRef(false);
  const [masterDraft, setMasterDraft] = useState<number | null>(null);
  const flushMaster = useCallback(async () => {
    if (masterBusy.current || masterPending.current === null) return;
    const v = masterPending.current;
    masterPending.current = null;
    masterBusy.current = true;
    try {
      await putFx((stack) => stack.map(echo), { master: v });
    } catch (e) {
      reportError(e);
      setError(explainError(e));
    } finally {
      masterBusy.current = false;
      if (masterPending.current !== null) setTimeout(() => void flushMaster(), MASTER_INTERVAL_MS);
      else setMasterDraft(null);
    }
  }, [putFx, reportError]);
  const setMaster = useCallback(
    (v: number) => {
      setMasterDraft(v);
      const first = masterPending.current === null && !masterBusy.current;
      masterPending.current = v;
      if (first) setTimeout(() => void flushMaster(), MASTER_INTERVAL_MS);
    },
    [flushMaster],
  );

  const setDirection = useCallback(
    (label: string, d: Partial<Direction>) => patch(label, { direction: d }, (l) => ({ ...l, direction: mergeDirection(l.direction, d) })),
    [patch],
  );

  /** Any other change, with the card's busy line and its words on a refusal (re-reads the look after). */
  const act = useCallback(
    (label: string, fn: () => Promise<unknown>, after?: (r: unknown) => string | null | void) =>
      run(label, async () => {
        const r = await fn();
        const said = after?.(r);
        if (said) setInfo(said);
        setTimeout(() => void refresh(), EVENT_REFRESH_MS);
      }),
    [refresh, run],
  );

  return {
    look,
    available,
    busy,
    error,
    info,
    clearError: () => setError(null),
    refresh,
    patch,
    cue,
    nextEffect,
    swapEffect,
    takeOffHere,
    addEffect,
    removeEffect,
    setEffect,
    setFxFade,
    setMaster,
    masterDraft,
    setDirection,
    act,
    /** Look PATCHes from here change this screen only. */
    scoped: scoped && !lookScope.refused,
    /** fx/next and fx/clear from here act on this screen only (straight to the renderer: room_service isn't asked). */
    fxScoped: scoped,
    /** The live session (null while not live) and the stream events, for the card's other live pieces (Up next). */
    session: sessionId,
    subscribe,
  };
}

export type LiveLook = ReturnType<typeof useLiveLook>;

/** What LookSettings needs: a look and the ways to change it (one screen's, or a whole room's). */
export type LookCtl = Pick<LiveLook, "look" | "patch" | "setDirection" | "setFxFade">;
