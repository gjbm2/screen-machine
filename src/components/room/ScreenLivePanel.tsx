// A screen tab's Live panel on the home page: is this screen live, and a switch to turn it on and off.
//
// Shown only on the LAN (room_service answers) and only for screens room_service lets go live.
// Off: one line, the switch, and "Choose a scene". The switch goes live with the scene this screen
// last showed (else the room's default). On: the scene on glass (tap to change it) and the Room
// page's own look controls (Auto/Next, energy, colours, effects), so the two pages never drift.
// The Room page stays the full console; "Room" opens it on this screen.

import { useEffect, useState } from "react";
import { ChevronDown, ChevronUp, Radio, SlidersHorizontal } from "lucide-react";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import { explainError, storageGet, storageSet } from "@/pages/Room/api";
import { Spinner } from "@/pages/Room/bits";
import { LookControls, SceneButton, ScenePicker } from "@/pages/Room/LivePanel";
import { sceneInfo } from "@/pages/Room/liveLib";
import { RoomThemeStyle, THEME_CLASS } from "@/pages/Room/theme";
import { useLiveLook } from "@/pages/Room/useLiveLook";
import {
  isLiveCapable,
  liveSceneKey,
  liveStateOf,
  roomApi,
  roomPageUrl,
  sceneSpecOf,
  subscribeStream,
  useRoomLibrary,
  useRoomLink,
  type LiveState,
} from "./roomLink";

/** The room's default live scene (config/room/modes.json, the live modes). */
const DEFAULT_SCENE_KEY = "preset:fractal-zoom";
const LAST_SCENE_KEY = (id: string) => `sm_home_live_scene:${id}`;
const OPEN_KEY = "sm_home_live_open";
const noop = () => {};

const BADGE: Record<LiveState, { text: string; className: string; pulse?: boolean }> = {
  art: { text: "Art", className: "border-stone-300 bg-stone-100 text-stone-700" },
  entering: { text: "Going live", className: "border-amber-300 bg-amber-50 text-amber-800", pulse: true },
  live: { text: "LIVE", className: "border-red-600 bg-red-600 text-white", pulse: true },
  leaving: { text: "Leaving", className: "border-amber-300 bg-amber-50 text-amber-800", pulse: true },
  failed: { text: "Not live", className: "border-rose-300 bg-rose-50 text-rose-800" },
};

export function ScreenLivePanel({ screenId }: { screenId: string }) {
  const { onLan, canControl, status, connected } = useRoomLink();
  const screen = status?.screens?.[screenId];
  const shown = !!onLan && canControl && isLiveCapable(screen);
  const api = roomApi();
  const lib = useRoomLibrary(shown);
  const ls = liveStateOf(screen);
  const liveNow = ls === "live";
  const sceneKey = liveSceneKey(screen);
  const lv = useLiveLook({
    api,
    screenId,
    sessionId: shown && liveNow ? screen?.observed?.session ?? null : null,
    enabled: !!lib,
    subscribe: subscribeStream,
    addLog: noop,
    reportError: noop,
  });
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [picker, setPicker] = useState<"choose" | "cue" | null>(null);
  const [open, setOpen] = useState(() => storageGet(OPEN_KEY) !== "0");

  // Remember the scene this screen shows, so the switch brings it back next time.
  useEffect(() => {
    if (sceneKey) storageSet(LAST_SCENE_KEY(screenId), sceneKey);
  }, [screenId, sceneKey]);

  if (!shown || !screen) return null;

  const lastKey = storageGet(LAST_SCENE_KEY(screenId)) ?? DEFAULT_SCENE_KEY;
  const nextInfo = sceneInfo(lib, api, lastKey);
  const nowInfo = sceneInfo(lib, api, sceneKey);
  const pageMissing = screen.observed?.page_connected === false;
  const wantLive = ls === "entering" || ls === "live";

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
  const toggleOpen = () => {
    setOpen((o) => {
      storageSet(OPEN_KEY, o ? "0" : "1");
      return !o;
    });
  };

  const detail = screen.observed?.detail;
  let title: string;
  let sub: string;
  switch (ls) {
    case "live":
      title = nowInfo?.label ?? "Live";
      sub = "Live now · tap the switch to go back to art";
      break;
    case "entering":
      title = "Going live…";
      sub = detail || nowInfo?.label || "";
      break;
    case "leaving":
      title = "Going back to art…";
      sub = detail || "";
      break;
    case "failed":
      title = "Couldn't go live";
      sub = screen.last_error || detail || "Try again, or open Room for details";
      break;
    default:
      title = "Live is off";
      sub = pageMissing ? "The TV isn't connected, so it can't go live" : `Showing art · goes live with ${nextInfo?.label ?? "the room's scene"}`;
  }
  const badge = BADGE[ls];

  return (
    // contain:inline-size: the home page sits in a Radix ScrollArea (display:table), which otherwise grows to
    // this panel's one-line texts and pushes the switch off a phone's screen.
    <div className={cn(THEME_CLASS, "mb-3 [contain:inline-size]")} data-testid={`home-live-${screenId}`}>
      <RoomThemeStyle />
      <section
        aria-label="Live"
        className={cn(
          "rounded-2xl border bg-card p-3 shadow-sm sm:p-4",
          liveNow ? "border-red-300 ring-1 ring-red-200" : "border-stone-200",
        )}
      >
        <div className="flex items-center gap-3">
          <span
            className={cn(
              "inline-flex h-7 shrink-0 items-center gap-1.5 rounded-full border px-2.5 text-[13px] font-semibold tracking-wide",
              badge.className,
            )}
          >
            {ls === "live" ? (
              <span aria-hidden className="h-2 w-2 rounded-full bg-white motion-safe:animate-pulse" />
            ) : ls === "entering" || ls === "leaving" || busy ? (
              <Spinner className="h-3.5 w-3.5" />
            ) : (
              <Radio className="h-3.5 w-3.5" aria-hidden />
            )}
            {badge.text}
          </span>
          <div className="min-w-0 flex-1">
            <div className="truncate text-base font-semibold leading-snug">{title}</div>
            {sub && <div className="truncate text-[14px] leading-snug text-muted-foreground">{sub}</div>}
          </div>
          <span className="grid h-11 w-14 shrink-0 place-items-center">
            <Switch
              aria-label={wantLive ? "Live is on: switch off to go back to art" : "Live is off: switch on to go live"}
              checked={wantLive}
              disabled={!!busy || ls === "leaving" || (!wantLive && pageMissing)}
              onCheckedChange={(on) => (on ? goLive(lastKey) : backToArt())}
              data-testid={`home-live-switch-${screenId}`}
            />
          </span>
        </div>

        {error && <p className="mt-2 text-[14px] text-rose-700">{error}</p>}
        {!connected && <p className="mt-2 text-[14px] text-muted-foreground">Reconnecting to the room…</p>}

        {liveNow && open && (
          <div className="mt-3 space-y-3">
            <SceneButton
              info={nowInfo}
              caption="Tap to change scene"
              onClick={() => setPicker("cue")}
              disabled={!lib || !!lv.busy}
              testId={`home-scene-${screenId}`}
            />
            <LookControls screenId={screenId} lv={lv} lib={lib} api={api} />
          </div>
        )}

        <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-[14px]">
          {ls === "art" || ls === "failed" ? (
            <button
              type="button"
              onClick={() => setPicker("choose")}
              disabled={!lib || !!busy || pageMissing}
              className="inline-flex h-9 items-center gap-1.5 rounded-full border border-stone-300 bg-white px-3 font-medium hover:bg-stone-50 disabled:opacity-50"
            >
              Choose a scene to go live…
            </button>
          ) : liveNow ? (
            <button
              type="button"
              onClick={toggleOpen}
              className="inline-flex h-9 items-center gap-1 rounded-full px-2 font-medium text-stone-700 hover:bg-stone-100"
            >
              {open ? <ChevronUp className="h-4 w-4" aria-hidden /> : <ChevronDown className="h-4 w-4" aria-hidden />}
              {open ? "Hide live controls" : "Live controls"}
            </button>
          ) : (
            <span />
          )}
          <a
            href={roomPageUrl(screenId)}
            className="inline-flex h-9 items-center gap-1.5 rounded-full px-2 font-medium text-teal-800 hover:bg-teal-50"
          >
            <SlidersHorizontal className="h-4 w-4" aria-hidden /> More in Room
          </a>
        </div>
      </section>

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
    </div>
  );
}
