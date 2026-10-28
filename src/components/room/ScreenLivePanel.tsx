// A screen tab's live parts on the home page, for its screen strip (ScreenStrip.tsx): is this screen live, going
// live with the scene it last showed (else the room's default), back to art, the scene picker, and the Room page's own
// look controls (Auto/Next, energy, colours, effects), so the two pages never drift.
//
// Live shows only on the LAN (room_service answers) and only for screens room_service lets go live; elsewhere
// `shown` is false and the strip leaves the live parts out. It reads the room link through selectors (one value
// each), so a room status frame re-renders the strip only when something it shows changes.

import { useEffect, useState } from "react";
import { explainError, storageGet, storageSet } from "@/pages/Room/api";
import { LookControls, SceneButton, ScenePicker } from "@/pages/Room/LivePanel";
import { sceneInfo, type SceneInfo } from "@/pages/Room/liveLib";
import { useLiveLook } from "@/pages/Room/useLiveLook";
import {
  isLiveCapable,
  liveSceneKey,
  liveStateOf,
  roomApi,
  sceneSpecOf,
  subscribeStream,
  useRoomLibrary,
  useRoomLinkSelect,
  wantsLive,
  type LiveState,
} from "./roomLink";

/** The room's default live scene (config/room/modes.json, the live modes). */
const DEFAULT_SCENE_KEY = "preset:fractal-zoom";
const LAST_SCENE_KEY = (id: string) => `sm_home_live_scene:${id}`;
const noop = () => {};

export function useScreenLive(screenId: string) {
  const onLan = useRoomLinkSelect((s) => s.onLan === true);
  const canControl = useRoomLinkSelect((s) => s.canControl);
  const connected = useRoomLinkSelect((s) => s.connected);
  const inRoom = useRoomLinkSelect((s) => !!s.status?.screens?.[screenId]);
  const capable = useRoomLinkSelect((s) => isLiveCapable(s.status?.screens?.[screenId]));
  const ls: LiveState = useRoomLinkSelect((s) => liveStateOf(s.status?.screens?.[screenId]));
  const want = useRoomLinkSelect((s) => wantsLive(s.status?.screens?.[screenId]));
  const sceneKey = useRoomLinkSelect((s) => liveSceneKey(s.status?.screens?.[screenId]));
  const session = useRoomLinkSelect((s) => s.status?.screens?.[screenId]?.observed?.session ?? null);
  const pageMissing = useRoomLinkSelect((s) => s.status?.screens?.[screenId]?.observed?.page_connected === false);
  const detail = useRoomLinkSelect((s) => s.status?.screens?.[screenId]?.observed?.detail ?? null);
  const lastError = useRoomLinkSelect((s) => s.status?.screens?.[screenId]?.last_error ?? null);

  const shown = onLan && canControl && capable;
  const api = roomApi();
  const lib = useRoomLibrary(shown);
  const liveNow = ls === "live";
  const lv = useLiveLook({
    api,
    screenId,
    sessionId: shown && liveNow ? (session as string | null) : null,
    enabled: !!lib,
    subscribe: subscribeStream,
    addLog: noop,
    reportError: noop,
  });
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [picker, setPicker] = useState<"choose" | "cue" | null>(null);

  // Remember the scene this screen shows, so Go live brings it back next time.
  useEffect(() => {
    if (sceneKey) storageSet(LAST_SCENE_KEY(screenId), sceneKey);
  }, [screenId, sceneKey]);

  const lastKey = storageGet(LAST_SCENE_KEY(screenId)) ?? DEFAULT_SCENE_KEY;
  const nextInfo: SceneInfo | null = sceneInfo(lib, api, lastKey);
  // The scene on glass, as the renderer says (Auto changes it by itself), else the one asked for.
  const nowInfo: SceneInfo | null = sceneInfo(lib, api, (liveNow && lv.look?.current_scene) || sceneKey);

  const run = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(explainError(e));
    } finally {
      setBusy(null);
    }
  };
  const goLive = (key: string) => void run("Going live", () => api.setDesired(screenId, { live: { scene: sceneSpecOf(key) } }));
  const backToArt = () => void run("Going back to art", () => api.setDesired(screenId, "art"));

  return {
    screenId,
    onLan,
    connected,
    inRoom,
    shown,
    api,
    lib,
    lv,
    ls,
    liveNow,
    /** Live is asked for (going live, live, or failing while it retries). */
    want,
    sceneKey,
    lastKey,
    nextInfo,
    nowInfo,
    pageMissing,
    detail,
    lastError,
    busy,
    error,
    picker,
    setPicker,
    goLive,
    backToArt,
  };
}

export type ScreenLive = ReturnType<typeof useScreenLive>;

/** While live: the scene on glass (tap to change it) and the Room page's look controls. */
export function LiveLookPanel({ live }: { live: ScreenLive }) {
  const { screenId, lv, lib, api, nowInfo } = live;
  return (
    <div className="space-y-3">
      <SceneButton
        info={nowInfo}
        caption="Tap to change scene"
        onClick={() => live.setPicker("cue")}
        disabled={!lib || !!lv.busy}
        testId={`home-scene-${screenId}`}
      />
      <LookControls screenId={screenId} lv={lv} lib={lib} api={api} />
    </div>
  );
}

/** The scene picker: a scene to go live with (showing art), or a scene to change to (live, as a cue). */
export function LiveScenePicker({ live }: { live: ScreenLive }) {
  const { screenId, lv, lib, api, picker, setPicker, sceneKey, lastKey, goLive } = live;
  return (
    <ScenePicker
      open={picker !== null}
      onOpenChange={(o) => !o && setPicker(null)}
      lib={lib}
      api={api}
      currentKey={picker === "cue" ? sceneKey : lastKey}
      mode={picker ?? "choose"}
      onPick={(key, choice) => {
        if (picker === "cue") {
          const label = sceneInfo(lib, api, key)?.label ?? key;
          void lv.cue(`switch to ${label}`, {
            to: sceneSpecOf(key),
            ...(choice.transition ? { transition: choice.transition } : {}),
            at: choice.at ?? "now",
          });
        } else {
          goLive(key);
        }
      }}
      testId={`home-picker-${screenId}`}
    />
  );
}
