// A room's live screens as one (owner, 17 Oct: a control acts where it sits, and the rooms are config,
// routes/data/rooms.json; a control at room level acts on every screen in the room).
//
// Reads each live screen's session summary from the renderer (GET /renderer/api/sessions/{sid}: the look's fields,
// its room {id, role, leader, screens} and its Up next) when the screens or their sessions change, shortly after their
// stream events, and every 20 s.
//
// The renderer already runs one direction per room (stream_service rooms.py): a look PATCH, Next scene, Next effect
// and Up next on any screen of a room reach all of them through its leader. So those go once per renderer room (to
// its leader); a screen outside it (no auto block: "solo") gets its own. Changes that are per screen in the renderer
// (a scene by hand, the effects bus) go to every live screen. With the renderer's look_scope they carry scope "room",
// which also gives back to the room any part a screen had set for itself (its "own"); without it, none (the default
// is the room's).

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { explainError, RoomApi, RoomApiError } from "./api";
import { LOOK_SCOPE_FEATURE, hasFeature } from "./liveLib";
import { REFRESH_ON, lookScope, mergeDirection } from "./useLiveLook";
import { clearEffects, eachOf, echo, isPicked, ownStack, pickEffect, putFxStack, swapPicked, withOnTop, type LiveMember } from "./roomFx";
import type { CueBody, Direction, FxEntry, Library, Look, LookPatch, SceneSpec, SessionView } from "./types";
import type { NewLogEntry, StreamEvent } from "./useRoomConnection";

const EVENT_REFRESH_MS = 350;
const POLL_MS = 20_000;

export interface RoomLiveArgs {
  api: RoomApi;
  /** The room's id in rooms.json (the renderer's room has the same id). */
  roomId: string;
  roomName: string;
  /** The room's live screens, in rooms.json order. */
  members: LiveMember[];
  /** The library (its features say whether the renderer takes a scope, and has 04's fx routes). */
  lib: Library | null;
  subscribe: (fn: (e: StreamEvent) => void) => () => void;
  addLog: (e: NewLogEntry) => void;
  reportError: (e: unknown) => void;
}

/** A picker tile's key as a cue's scene: a preset by name, a plain source as {source}. */
export function sceneSpecOfKey(key: string): SceneSpec {
  return key.startsWith("source:") ? { source: key.slice(7) } : key.replace(/^preset:/, "");
}

/** scope "room" on a look PATCH (room_service must take it too) and on fx/next / fx/clear (the renderer only). */
const lookRoom = (lib: Library | null) => hasFeature(lib, LOOK_SCOPE_FEATURE) && !lookScope.refused;
const fxRoom = (lib: Library | null) => (hasFeature(lib, LOOK_SCOPE_FEATURE) ? ("room" as const) : undefined);
const fxOpts = (lib: Library | null) => ({ lib, scope: fxRoom(lib) });

export function useRoomLive({ api, roomId, roomName, members, lib, subscribe, addLog, reportError }: RoomLiveArgs) {
  const [views, setViews] = useState<Record<string, SessionView>>({});
  const [readFailed, setReadFailed] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const membersRef = useRef(members);
  membersRef.current = members;
  const viewsRef = useRef(views);
  viewsRef.current = views;
  const libRef = useRef(lib);
  libRef.current = lib;
  const seq = useRef(0);
  const key = members.map((m) => `${m.id}=${m.session}`).join(" ");

  const refresh = useCallback(async () => {
    const ms = membersRef.current;
    if (!ms.length) return;
    const my = ++seq.current;
    const got = await Promise.allSettled(ms.map((m) => api.session(m.session)));
    if (my !== seq.current) return;
    setViews((cur) => {
      const next: Record<string, SessionView> = {};
      ms.forEach((m, i) => {
        const r = got[i];
        if (r.status === "fulfilled" && r.value) next[m.id] = r.value;
        else if (cur[m.id]?.session === m.session) next[m.id] = cur[m.id]; // keep the last one it sent
      });
      return next;
    });
    const bad = got.find((r) => r.status === "rejected") as PromiseRejectedResult | undefined;
    setReadFailed(got.every((r) => r.status === "rejected"));
    if (bad) reportError(bad.reason);
  }, [api, reportError]);

  // The screens (or their sessions) changed: read them again.
  useEffect(() => {
    seq.current++;
    setError(null);
    setInfo(null);
    if (key) void refresh();
    else setViews({});
  }, [key, refresh]);

  // Their stream events, coalesced; plus a slow backstop poll.
  useEffect(() => {
    if (!key) return;
    const sessions = new Set(membersRef.current.map((m) => m.session));
    let timer: ReturnType<typeof setTimeout> | null = null;
    const off = subscribe((e) => {
      if (!sessions.has(String(e.session)) || !REFRESH_ON.has(String(e.type))) return;
      if (!timer)
        timer = setTimeout(() => {
          timer = null;
          void refresh();
        }, EVENT_REFRESH_MS);
    });
    const poll = setInterval(() => {
      if (document.visibilityState === "visible") void refresh();
    }, POLL_MS);
    return () => {
      off();
      clearInterval(poll);
      if (timer) clearTimeout(timer);
    };
  }, [key, subscribe, refresh]);

  // One target per renderer room (its leader), and each screen outside one. Unread screens are taken to be in the
  // room (the renderer's own rule: rooms.json, with an auto block).
  const targets = useMemo(() => {
    const groups = new Map<string, LiveMember[]>();
    for (const m of members) {
      const v = views[m.id];
      const k = !v ? `room:${roomId}` : v.room && v.room.role !== "solo" ? `room:${v.room.id}` : `solo:${m.id}`;
      groups.set(k, [...(groups.get(k) ?? []), m]);
    }
    return [...groups.values()].map((g) => g.find((m) => views[m.id]?.room?.role === "leader") ?? g[0]);
  }, [members, views, roomId]);
  const targetsRef = useRef(targets);
  targetsRef.current = targets;

  const lead = targets[0] ?? null;
  const look: SessionView | null = lead ? views[lead.id] ?? null : null;

  const run = useCallback(
    async (label: string, fn: () => Promise<string | null | void>) => {
      setBusy(label);
      setError(null);
      setInfo(null);
      try {
        const said = await fn();
        if (said) setInfo(said);
        addLog({ type: "ui", summary: `${roomName}: ${label} accepted` });
      } catch (e) {
        reportError(e);
        const msg = explainError(e);
        setError(msg);
        addLog({ type: "ui", level: "error", summary: `${roomName}: ${label} refused: ${msg}` });
      } finally {
        setBusy(null);
        setTimeout(() => void refresh(), EVENT_REFRESH_MS);
      }
    },
    [addLog, refresh, reportError, roomName],
  );

  /** Paint a look change on every screen's view first (the replies and the next read replace it). */
  const paint = useCallback((f: (l: Look) => Look) => {
    seq.current++;
    setViews((cur) => Object.fromEntries(Object.entries(cur).map(([id, v]) => [id, { ...v, ...f(v) }])));
  }, []);

  /** A look change for the room: once per renderer room. Same shape as a screen's (LookSettings takes either). */
  const patch = useCallback(
    (label: string, body: LookPatch, optimistic?: (l: Look) => Look) => {
      if (optimistic) paint(optimistic);
      return run(label, async () => {
        const send = (m: LiveMember) => {
          if (!lookRoom(libRef.current)) return api.patchLook(m.id, body);
          return api.patchLook(m.id, { ...body, scope: "room" }).catch((e) => {
            // A room_service that predates scope: the room's change is its default anyway.
            if (!(e instanceof RoomApiError && e.status === 400 && /may contain only/i.test(e.detail) && !/scope/.test(e.detail))) throw e;
            lookScope.refused = true;
            return api.patchLook(m.id, body);
          });
        };
        const got = await eachOf(targetsRef.current, send);
        seq.current++;
        setViews((cur) => {
          const next = { ...cur };
          targetsRef.current.forEach((m, i) => {
            if (next[m.id] && got[i]) next[m.id] = { ...next[m.id], ...got[i] };
          });
          return next;
        });
      });
    },
    [api, paint, run],
  );

  const setDirection = useCallback(
    (label: string, d: Partial<Direction>) => patch(label, { direction: d }, (l) => ({ ...l, direction: mergeDirection(l.direction, d) })),
    [patch],
  );

  const setScenePicking = useCallback(
    (on: boolean) =>
      patch(on ? "scene picking on" : "scene picking off", { auto: { enabled: on } }, (l) => ({ ...l, auto: { ...(l.auto ?? {}), enabled: on } })),
    [patch],
  );

  const setFxPicking = useCallback(
    (on: boolean) =>
      patch(on ? "effect picking on" : "effect picking off", { auto: { fx: { enabled: on } } }, (l) => ({
        ...l,
        auto: { ...(l.auto ?? {}), fx: { ...(l.auto?.fx ?? {}), enabled: on } },
      })),
    [patch],
  );

  /** Exactly n picked effects at a time (density 1: floor(1·n + U) = n); yours are extra. */
  const setHowMany = useCallback(
    (n: number) =>
      patch(`${n} ${n === 1 ? "effect" : "effects"} at a time`, { auto: { fx: { max: n } }, direction: { fx: { density: 1 } } }, (l) => ({
        ...l,
        auto: { ...(l.auto ?? {}), fx: { ...(l.auto?.fx ?? {}), max: n } },
        direction: mergeDirection(l.direction, { fx: { density: 1 } }),
      })),
    [patch],
  );

  const nextScene = useCallback(
    () => run("next scene", async () => void (await eachOf(targetsRef.current, (m) => api.cue(m.id, { to: "auto" })))),
    [api, run],
  );

  const nextEffect = useCallback(
    () => run("next effect", async () => void (await eachOf(targetsRef.current, (m) => api.nextEffect(m.session, fxRoom(libRef.current) ? { scope: "room" } : {})))),
    [api, run],
  );

  /** A scene by hand on every live screen here (the renderer keeps a hand-picked scene on its own screen). */
  const changeScene = useCallback(
    (key: string, label: string, how: { transition?: string; at?: "now" | "beat" }) => {
      const body: CueBody = { to: sceneSpecOfKey(key), ...(how.transition ? { transition: how.transition } : {}), at: how.at ?? "now" };
      return run(`changing scene to ${label}`, async () => void (await eachOf(membersRef.current, (m) => api.cue(m.id, body))));
    },
    [api, run],
  );

  /** The effects fade time, on every screen's bus. */
  const setFxFade = useCallback(
    (fade_s: number) =>
      run("effects fade time", async () => void (await eachOf(membersRef.current, (m) => putFxStack(api, m.id, (s) => s.map(echo), { fade_s })))),
    [api, run],
  );

  // The room's picks: raw (they throw), for a screen's card to run under its own busy line too.
  const takeOffPickedRaw = useCallback(
    (effect: string) => clearEffects(api, targetsRef.current, membersRef.current, effect, fxOpts(libRef.current)),
    [api],
  );
  const swapPickedRaw = useCallback(
    async (preset: string) => {
      await eachOf(targetsRef.current, (m) => {
        const slot = (viewsRef.current[m.id]?.fx?.stack ?? []).find((e) => isPicked(e) && e.preset === preset)?.id ?? null;
        return swapPicked(api, m.session, slot, fxRoom(libRef.current));
      });
    },
    [api],
  );

  const takeOffPicked = useCallback((effect: string, label: string) => run(`taking ${label} off`, () => takeOffPickedRaw(effect)), [run, takeOffPickedRaw]);
  const swapPickedOne = useCallback((preset: string, label: string) => run(`swapping ${label}`, () => swapPickedRaw(preset)), [run, swapPickedRaw]);

  /** Put one on now in place of the room's pick. */
  const changeEffect = useCallback(
    (preset: string, label: string) =>
      run(`putting ${label} on`, async () => {
        await eachOf(targetsRef.current, (m) => pickEffect(api, m.session, preset, fxOpts(libRef.current)));
      }),
    [api, run],
  );

  /** Add one on top on every live screen here, as yours. At the limit each screen's oldest of yours comes off. */
  const addOnTop = useCallback(
    (preset: string, label: string, max: number, words: (dropped: FxEntry) => string) =>
      run(`adding ${label}`, async () => {
        const dropped: FxEntry[] = [];
        await eachOf(membersRef.current, (m) =>
          putFxStack(api, m.id, (stack) => {
            const r = withOnTop(stack, preset, max);
            if (r.dropped) dropped.push(r.dropped);
            return r.stack;
          }),
        );
        return dropped.length ? words(dropped[0]) : null;
      }),
    [api, run],
  );

  /** No effects: picking off (so nothing comes back), then every effect off every screen here. */
  const noEffects = useCallback(
    () =>
      run("no effects", async () => {
        paint((l) => ({ ...l, auto: { ...(l.auto ?? {}), fx: { ...(l.auto?.fx ?? {}), enabled: false } } }));
        await eachOf(targetsRef.current, (m) => api.patchLook(m.id, { auto: { fx: { enabled: false } } }));
        await clearEffects(api, targetsRef.current, membersRef.current, undefined, fxOpts(libRef.current));
      }),
    [api, paint, run],
  );

  /** Any effect on any screen here. */
  const anyOn = members.some((m) => ownStack(views[m.id]?.fx?.stack).length > 0);
  const picks = (look?.fx?.stack ?? []).filter(isPicked).length;

  return {
    look,
    lead,
    views,
    targets,
    readFailed,
    busy,
    error,
    info,
    anyOn,
    picks,
    refresh,
    patch,
    setDirection,
    setScenePicking,
    setFxPicking,
    setHowMany,
    setFxFade,
    nextScene,
    nextEffect,
    changeScene,
    changeEffect,
    addOnTop,
    noEffects,
    takeOffPicked,
    swapPicked: swapPickedOne,
    takeOffPickedRaw,
    swapPickedRaw,
  };
}

export type RoomLiveState = ReturnType<typeof useRoomLive>;
