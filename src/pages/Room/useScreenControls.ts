// A screen's live controls as state, without any layout: the scene, transition
// and profile choices, the parameter form, Go live / Back to art, scene switches
// and live parameter edits (LIVE_V1 §3: PUT /screens/{id}, PATCH /screens/{id}/live).
//
// It lives in the screen card, which is always mounted, so the form and the
// batched live edits survive the "More" area being closed and reopened. The inline
// core controls and the unrolled panels both read and drive this one state.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { explainError, RoomApi, RoomApiError, type LivePatch } from "./api";
import {
  baseValues,
  buildSceneSpec,
  parseSceneSpec,
  pruneOverrides,
  sceneChoices,
  transitionChoices,
} from "./catalog";
import { BUSY_PHASES } from "./tones";
import type { Catalog, LiveDesired, Params, ScreenStatus, TransitionSpec } from "./types";

/** How the live scene picker asks for a change (room_service's cue route; LivePanel's CueChoice). */
export interface SceneSwitch {
  /** POST /screens/{id}/cue instead of PATCH /live {scene} (room_service has the live routes). */
  cue?: boolean;
  /** A transition preset; absent lets the renderer choose (auto's pick, else the in transition). */
  transition?: string;
  at?: "now" | "beat";
}
import type { NewLogEntry } from "./useRoomConnection";

/** Live edits go out at most this often while a control is being dragged; the last value always goes. */
const PATCH_INTERVAL_MS = 200;
/** Don't overwrite the form from status while the person has touched it this recently. */
const TOUCH_GRACE_MS = 3_000;

export interface FormState {
  sceneKey: string | null;
  /** Params that differ from the chosen preset (or from the schema defaults, for a raw source). */
  overrides: Params;
  inSpec: TransitionSpec | null;
  outSpec: TransitionSpec | null;
  /** "" means "let stream_service choose" (the profile field is omitted). */
  profile: string;
}

const EMPTY_FORM: FormState = { sceneKey: null, overrides: {}, inSpec: null, outSpec: null, profile: "" };

export type LiveEditState = "idle" | "pending" | "ok" | "error";

export function liveOf(s: ScreenStatus): LiveDesired | null {
  const d = s.desired;
  return d && typeof d === "object" && "live" in d && d.live ? d.live : null;
}

function formFromLive(live: LiveDesired): FormState {
  const parsed = parseSceneSpec(live.scene);
  return {
    sceneKey: parsed?.key ?? null,
    // A top-level live.params is accepted by the API too; treat it as overrides.
    overrides: { ...(parsed?.overrides ?? {}), ...(live.params ?? {}) },
    inSpec: live.transitions?.in ?? null,
    outSpec: live.transitions?.out ?? null,
    profile: live.profile ?? "",
  };
}

export interface ScreenControlsArgs {
  screenId: string;
  screen: ScreenStatus;
  catalog: Catalog | null;
  api: RoomApi;
  /** False while the page has no fresh status from room_service. */
  connected: boolean;
  addLog: (e: NewLogEntry) => void;
  reportError: (e: unknown) => void;
}

export function useScreenControls({ screenId, screen, catalog, api, connected, addLog, reportError }: ScreenControlsArgs) {
  const scenes = useMemo(() => sceneChoices(catalog), [catalog]);
  const transitions = useMemo(() => transitionChoices(catalog), [catalog]);
  const profiles = useMemo(() => catalog?.profiles ?? {}, [catalog]);

  const desiredLive = liveOf(screen);
  const phase = screen.observed?.phase ?? null;
  const layer = screen.observed?.layer ?? null;
  const liveNow = !!desiredLive && (phase === "live" || phase === "entering");

  // live_enabled may be missing from status, so a 409 live_disabled is how the page may learn it.
  const [refused, setRefused] = useState(false);
  const policyLive = screen.policy?.live_enabled ?? screen.live_enabled;
  const liveDisabled: "policy" | "refused" | null = policyLive === false ? "policy" : refused ? "refused" : null;

  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const touchedAt = useRef(0);
  const touch = () => (touchedAt.current = Date.now());

  // ── Follow the desired state when it changes (a mode switch, another client) ──
  const desiredKey = JSON.stringify(screen.desired ?? null);
  const syncedKey = useRef<string | null>(null);
  // A scene switched in-stream by PATCH (desired may not reflect it).
  const [patchedSceneKey, setPatchedSceneKey] = useState<string | null>(null);
  // Bumped to retry a sync that was held back while the person was editing.
  const [syncRetry, setSyncRetry] = useState(0);

  useEffect(() => {
    if (syncedKey.current === desiredKey) return;
    const first = syncedKey.current === null;
    const sinceTouch = Date.now() - touchedAt.current;
    if (!first && sinceTouch < TOUCH_GRACE_MS) {
      // Don't yank controls out from under someone mid-edit (e.g. a status echo of their own
      // live edit). Look again once they've paused.
      const t = setTimeout(() => setSyncRetry((n) => n + 1), TOUCH_GRACE_MS - sinceTouch + 50);
      return () => clearTimeout(t);
    }
    syncedKey.current = desiredKey;
    setPatchedSceneKey(null);
    const live = desiredKey === "null" ? null : liveOf({ desired: JSON.parse(desiredKey) });
    if (live) setForm(formFromLive(live));
  }, [desiredKey, syncRetry]);

  // Fill gaps with catalog defaults once it arrives: the first preset of each kind.
  useEffect(() => {
    setForm((f) => {
      let next = f;
      if (!f.sceneKey && scenes.length) next = { ...next, sceneKey: scenes[0].key };
      if (!f.inSpec && transitions.length) next = { ...next, inSpec: transitions[0].key };
      if (!f.outSpec && transitions.length) next = { ...next, outSpec: transitions[0].key };
      return next;
    });
  }, [scenes, transitions]);

  const choice = scenes.find((c) => c.key === form.sceneKey);
  const base = useMemo(() => baseValues(choice), [choice]);
  const overrides = useMemo(() => pruneOverrides(form.overrides, base), [form.overrides, base]);
  const values = useMemo(() => ({ ...base, ...form.overrides }), [base, form.overrides]);
  const overridden = useMemo(() => new Set(Object.keys(overrides)), [overrides]);

  const liveSceneKey = patchedSceneKey ?? parseSceneSpec(desiredLive?.scene)?.key ?? null;
  const editsGoLive = liveNow && !!form.sceneKey && form.sceneKey === liveSceneKey;

  // ── Live parameter edits: a short throttle, one request at a time ──────────
  // Edits merge into `pending`. At most one PATCH is in flight; edits made meanwhile go in the
  // next one, sent when it returns. So order is kept and a slow room_service never builds a
  // backlog of stale intermediate values (a queue of one request per 200 ms would).
  const pending = useRef<Params>({});
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const inFlight = useRef(false);
  const flushRef = useRef<() => void>(() => {});
  const [liveEdit, setLiveEdit] = useState<{ state: LiveEditState; msg?: string }>({ state: "idle" });

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
      timer.current = null;
      pending.current = {};
    },
    [],
  );

  const flush = useCallback(async () => {
    timer.current = null;
    if (inFlight.current) return; // sent when the request in flight returns
    const params = pending.current;
    pending.current = {};
    if (!Object.keys(params).length) return;
    inFlight.current = true;
    try {
      await api.patchLive(screenId, { params });
      setLiveEdit((s) => (Object.keys(pending.current).length || timer.current ? s : { state: "ok" }));
    } catch (e) {
      reportError(e);
      const msg = explainError(e);
      setLiveEdit({ state: "error", msg });
      addLog({ type: "ui", level: "error", screen: screenId, summary: `live edit refused: ${msg}`, data: params });
    } finally {
      inFlight.current = false;
      if (Object.keys(pending.current).length && !timer.current) {
        timer.current = setTimeout(() => flushRef.current(), PATCH_INTERVAL_MS);
      }
    }
  }, [api, screenId, reportError, addLog]);
  flushRef.current = () => void flush();

  const queueLiveParams = (p: Params) => {
    Object.assign(pending.current, p);
    setLiveEdit({ state: "pending" });
    if (!timer.current) timer.current = setTimeout(() => flushRef.current(), PATCH_INTERVAL_MS);
  };

  const onParamChange = (key: string, v: unknown) => {
    touch();
    setForm((f) => ({ ...f, overrides: { ...f.overrides, [key]: v } }));
    if (editsGoLive) queueLiveParams({ [key]: v });
  };

  const onParamReset = (key: string) => {
    touch();
    setForm((f) => {
      const { [key]: _gone, ...rest } = f.overrides;
      return { ...f, overrides: rest };
    });
    // PATCH merges, so "reset" means sending the preset's value back.
    if (editsGoLive && key in base) queueLiveParams({ [key]: base[key] });
  };

  const resetAll = () => {
    touch();
    const keys = Object.keys(overrides);
    setForm((f) => ({ ...f, overrides: {} }));
    if (editsGoLive && keys.length) {
      const back: Params = {};
      for (const k of keys) if (k in base) back[k] = base[k];
      queueLiveParams(back);
    }
  };

  // ── Pickers ─────────────────────────────────────────────────────────────────
  /** Choose the scene to go live with (an art screen). A new scene starts from its preset values. */
  const setScene = (key: string) => {
    touch();
    setForm((f) => ({ ...f, sceneKey: key, overrides: {} }));
  };
  const setInSpec = (spec: TransitionSpec) => {
    touch();
    setForm((f) => ({ ...f, inSpec: spec }));
  };
  const setOutSpec = (spec: TransitionSpec) => {
    touch();
    setForm((f) => ({ ...f, outSpec: spec }));
  };
  const setProfile = (profile: string) => {
    touch();
    setForm((f) => ({ ...f, profile }));
  };

  // ── Actions ─────────────────────────────────────────────────────────────────
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const run = async (label: string, fn: () => Promise<unknown>, onOk?: () => void, onFail?: () => void) => {
    setBusy(label);
    setActionError(null);
    try {
      await fn();
      addLog({ type: "ui", screen: screenId, summary: `${screenId}: ${label} accepted` });
      onOk?.();
    } catch (e) {
      reportError(e);
      const disabled = e instanceof RoomApiError && e.code === "live_disabled";
      if (disabled) setRefused(true);
      const msg = explainError(e);
      // live_disabled has its own, fuller notice.
      setActionError(disabled ? null : msg);
      addLog({ type: "ui", level: "error", screen: screenId, summary: `${screenId}: ${label} refused: ${msg}` });
      onFail?.();
    } finally {
      setBusy(null);
    }
  };

  const goLive = () => {
    if (!choice) return;
    const live: LiveDesired = { scene: buildSceneSpec(choice, overrides) };
    if (form.inSpec || form.outSpec) {
      live.transitions = {};
      if (form.inSpec) live.transitions.in = form.inSpec;
      if (form.outSpec) live.transitions.out = form.outSpec;
    }
    if (form.profile) live.profile = form.profile;
    touchedAt.current = 0; // let the resulting desired state flow back into the form
    void run("go live", () => api.setDesired(screenId, { live }), () => setRefused(false));
  };

  const backToArt = () => void run("back to art", () => api.setDesired(screenId, "art"));

  /**
   * While live: switch to another scene straight away. With the live routes it is a cue
   * (POST /screens/{id}/cue {to, transition?, at?}: the picker's transition, else the
   * renderer's choice); without them, PATCH /live {scene, transition: the in transition}.
   * The new scene starts from its preset values; its parameters can then be changed live.
   * If the switch is refused, the picker goes back.
   */
  const switchSceneTo = (key: string, how: SceneSwitch = {}) => {
    const next = scenes.find((s) => s.key === key);
    if (!next || !liveNow || busy) return;
    if (!how.cue && key === liveSceneKey && key === form.sceneKey) return;
    touch();
    const before = { sceneKey: form.sceneKey, overrides: form.overrides };
    setForm((f) => ({ ...f, sceneKey: key, overrides: {} }));
    const spec = buildSceneSpec(next, {});
    const send = how.cue
      ? () => api.cue(screenId, { to: spec, ...(how.transition ? { transition: how.transition } : {}), at: how.at ?? "now" })
      : () => api.patchLive(screenId, { scene: spec, ...(form.inSpec ? { transition: form.inSpec } : {}) } as LivePatch);
    void run(
      "switch scene",
      send,
      () => setPatchedSceneKey(key),
      () => setForm((f) => ({ ...f, ...before })),
    );
  };

  /**
   * The renderer changed scene by itself (Auto): follow it, so the inline settings are that
   * scene's. Not while the person is editing (the same grace as a desired-state change).
   */
  const followScene = useCallback(
    (key: string) => {
      if (!liveNow || key === form.sceneKey || Date.now() - touchedAt.current < TOUCH_GRACE_MS) return;
      if (!scenes.some((s) => s.key === key)) return;
      setForm((f) => ({ ...f, sceneKey: key, overrides: {} }));
      setPatchedSceneKey(key);
    },
    [liveNow, form.sceneKey, scenes],
  );

  // Go live stays available after a failure, or if the screen is somehow resting in art.
  const alreadyLive = !!desiredLive && phase !== "failed" && phase !== "idle";
  const canGoLive = connected && !!choice && !liveDisabled && !busy && !alreadyLive;
  const canBackToArt =
    connected && !busy && (!!desiredLive || layer === "live" || phase === "failed" || BUSY_PHASES.has(phase ?? ""));
  const pickersDisabled = !catalog || !connected;

  return {
    scenes,
    transitions,
    profiles,
    form,
    choice,
    base,
    values,
    overridden,
    liveNow,
    liveSceneKey,
    editsGoLive,
    alreadyLive,
    liveDisabled,
    clearRefused: () => setRefused(false),
    liveEdit,
    busy,
    actionError,
    pickersDisabled,
    canGoLive,
    canBackToArt,
    setScene,
    setInSpec,
    setOutSpec,
    setProfile,
    onParamChange,
    onParamReset,
    resetAll,
    goLive,
    backToArt,
    switchSceneTo,
    followScene,
  };
}

export type ScreenControls = ReturnType<typeof useScreenControls>;
