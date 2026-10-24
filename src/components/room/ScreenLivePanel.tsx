// A screen tab's Live panel on the generator: is this screen live, and a switch to turn it on and off.
//
// Shown only on the LAN (room_service answers) and only for screens room_service lets go live.
// It says what the Room page says (Room's words.ts: summariseScreen, problemWords), so the two pages never
// disagree. Off: one line, the switch, and "Choose a scene". The switch goes live with the scene the room has
// for this screen (its live scene), else the room's Art Director picks. Going live is never held back when the
// TV page isn't open (live still starts; the TV joins when its page opens): the panel says so instead.
// On: the scene on glass (tap to change it) and the Room page's own look controls. "More in Room" opens Room on
// this screen.

import { useState } from "react";
import { ChevronDown, ChevronUp, SlidersHorizontal } from "lucide-react";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import { explainError, storageGet, storageSet } from "@/pages/Room/api";
import { Dot, Spinner } from "@/pages/Room/bits";
import { LookControls, SceneButton, ScenePicker } from "@/pages/Room/LivePanel";
import { sceneInfo } from "@/pages/Room/liveLib";
import { RoomThemeStyle, THEME_CLASS } from "@/pages/Room/theme";
import { useLiveLook } from "@/pages/Room/useLiveLook";
import { TEXT_CLASSES } from "@/pages/Room/tones";
import type { Desired } from "@/pages/Room/types";
import { problemWords, summariseScreen } from "@/pages/Room/words";
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
} from "./roomLink";

const OPEN_KEY = "sm_home_live_open";
const noop = () => {};

export function ScreenLivePanel({ screenId }: { screenId: string }) {
  const { onLan, canControl, status, connected } = useRoomLink();
  const screen = status?.screens?.[screenId];
  const shown = !!onLan && canControl && isLiveCapable(screen);
  const api = roomApi();
  const lib = useRoomLibrary(shown);
  const ls = liveStateOf(screen);
  const streamReachable = status?.stream_service?.reachable;
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

  if (!shown || !screen) return null;

  // The scene the room has for this screen; none: the room's Art Director picks (as a mode does).
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
  const goLive = (key: string | null) =>
    void run("Going live", () =>
      api.setDesired(screenId, (key ? { live: { scene: sceneSpecOf(key) } } : { live: { auto: true } }) as unknown as Desired),
    );
  const backToArt = () => void run("Going back to art", () => api.setDesired(screenId, "art"));
  const toggleOpen = () => {
    setOpen((o) => {
      storageSet(OPEN_KEY, o ? "0" : "1");
      return !o;
    });
  };

  // The Room page's own words for this screen's state.
  const summary = summariseScreen(screen, [], streamReachable);
  const title = summary.live && nowInfo?.label ? `Live · ${nowInfo.label}` : summary.text;
  const phase = screen.observed?.phase ?? null;
  const sub =
    summary.problem
      ? problemWords(screen.last_error, phase)
      : summary.hint ??
        (summary.live ? "Switch off to go back to art" : !wantLive ? "Switch on to go live; the room picks the scene" : "");

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
          {summary.busy || busy ? <Spinner className="h-4 w-4" /> : <Dot tone={summary.tone} pulse={summary.live} className="h-2.5 w-2.5" />}
          <div className="min-w-0 flex-1">
            <div className={cn("text-base font-semibold leading-snug", TEXT_CLASSES[summary.tone])} data-testid={`home-live-status-${screenId}`}>
              {title}
            </div>
            {sub && <div className="text-[14px] leading-snug text-muted-foreground">{sub}</div>}
          </div>
          <span className="grid h-11 w-14 shrink-0 place-items-center">
            <Switch
              aria-label={wantLive ? "Live is on: switch off to go back to art" : "Live is off: switch on to go live"}
              checked={wantLive}
              disabled={!!busy || ls === "leaving"}
              onCheckedChange={(on) => (on ? goLive(sceneKey) : backToArt())}
              data-testid={`home-live-switch-${screenId}`}
            />
          </span>
        </div>

        {pageMissing && (
          <p className="mt-2 text-[14px] leading-snug text-sky-900">
            The TV page isn't open right now. Live still starts; the TV joins it when its page opens.
          </p>
        )}
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
              disabled={!lib || !!busy}
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
        currentKey={sceneKey}
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
