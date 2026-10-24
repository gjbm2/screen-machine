// One screen, compact: what's on it (the art's thumbnail, or the live scene), a friendly name and what
// it's doing in plain words, and the way to its tab in the generator. While
// live, its core controls sit inline (scene, the basic settings, Back to art).
// Everything else unrolls under "More": going live with a choice of scene,
// transitions and quality, every setting, snapshots, and the technical details.

import { memo, useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";
import { ArtThumb, GeneratorLink, usePublishedArt } from "@/components/room/ScreenArt";
import { EventsList, useRoomEvents } from "@/components/room/EventsList";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";
import { RoomApi } from "./api";
import { Dot, Notice, Unroll } from "./bits";
import { CoreControls, GoLivePanel, LiveSettingsPanel, type LiveBits } from "./LiveControls";
import { hasFeature, LOOK_SCOPE_FEATURE, sceneInfo, type LookScope } from "./liveLib";
import { ScreenDetails } from "./ScreenDetails";
import { DetailsSwitch, LiveDetails } from "./LiveDetails";
import { Snapshots } from "./Snapshots";
import { CARD_CLASS } from "./theme";
import { TEXT_CLASSES } from "./tones";
import type { Catalog, Library, ScreenStatus } from "./types";
import { useLiveLook } from "./useLiveLook";
import type { NewLogEntry, StreamEvent } from "./useRoomConnection";
import { liveOf, useScreenControls } from "./useScreenControls";
import { friendlyName, problemWords, summariseScreen } from "./words";

export interface ScreenCardProps {
  screenId: string;
  screen: ScreenStatus;
  catalog: Catalog | null;
  catalogError: string | null;
  /** The live controls' library (null: not loaded, or room_service has no live routes). */
  library: Library | null;
  subscribeStream: (fn: (e: StreamEvent) => void) => () => void;
  api: RoomApi;
  connected: boolean;
  streamReachable: boolean | undefined;
  /** A running mode change still has a step for this screen (it is the one taking it live or back). */
  inModeChange?: boolean;
  /** The page was opened for this screen (/room?screen=<id>): start unrolled and highlighted. */
  focused?: boolean;
  /** Its room's live card (RoomLive.tsx), when the room has one: the room-wide controls live there, not here. */
  scope?: LookScope | null;
  /** Its room (rooms.json id): its schedule's events unroll under More. */
  room?: string;
  addLog: (e: NewLogEntry) => void;
  reportError: (e: unknown) => void;
}

export const ScreenCard = memo(function ScreenCard({
  screenId,
  screen,
  catalog,
  catalogError,
  library,
  subscribeStream,
  api,
  connected,
  streamReachable,
  inModeChange = false,
  focused = false,
  scope = null,
  room,
  addLog,
  reportError,
}: ScreenCardProps) {
  const sc = useScreenControls({ screenId, screen, catalog, api, connected, addLog, reportError });
  const [open, setOpen] = useState(focused);
  useEffect(() => {
    if (focused) setOpen(true);
  }, [focused]);

  const obs = screen.observed ?? {};
  const lv = useLiveLook({
    api,
    screenId,
    sessionId: sc.liveNow ? obs.session ?? null : null,
    enabled: !!library,
    subscribe: subscribeStream,
    addLog,
    reportError,
    // Its look controls change this screen only once the renderer can (look_scope); until then they say "both screens".
    scoped: !!scope?.hosted && scope.shared.length > 0 && hasFeature(library, LOOK_SCOPE_FEATURE),
  });
  const live: LiveBits | undefined = library ? { lib: library, api, lv, scope } : undefined;
  // The scene on glass, as the renderer says (Auto changes it by itself).
  const glassName = lv.look?.current_scene;
  const onGlass = useMemo(
    () => (sc.liveNow && library ? sceneInfo(library, api, glassName) : null),
    [sc.liveNow, library, api, glassName],
  );
  const { followScene } = sc;
  useEffect(() => {
    if (onGlass) followScene(onGlass.key);
  }, [onGlass, followScene]);
  const summary = useMemo(() => {
    const s = summariseScreen(screen, sc.scenes, streamReachable);
    return onGlass && s.live ? { ...s, text: `Live · ${onGlass.label}` } : s;
  }, [screen, sc.scenes, onGlass, streamReachable]);

  // What's on the TV: the art it reports (re-read when it changes), or the scene while live.
  const art = usePublishedArt(screenId, obs.kiosk_state?.base_url);

  const phase = obs.phase ?? null;
  const desiredLive = liveOf(screen);

  // A cut's snapshot phase produces one new PNG: the anchor on entry (next phase
  // "starting"), the target on exit (next phase "exiting"). Polling can miss the
  // snapshot phase, so arriving at live or idle without having fetched counts too.
  const [snapRevs, setSnapRevs] = useState({ anchor: 0, target: 0 });
  const prevPhase = useRef(phase);
  const lastRest = useRef<string | null>(phase === "live" || phase === "idle" ? phase : null);
  const fetchedSinceRest = useRef(false);
  useEffect(() => {
    const prev = prevPhase.current;
    prevPhase.current = phase;
    if (prev === phase) return;
    const bump = (anchor: boolean, target: boolean) => {
      fetchedSinceRest.current = true;
      setSnapRevs((r) => ({ anchor: r.anchor + (anchor ? 1 : 0), target: r.target + (target ? 1 : 0) }));
    };
    if (prev === "snapshot") bump(phase !== "exiting", phase !== "starting");
    if (phase === "live" || phase === "idle") {
      if (!fetchedSinceRest.current && lastRest.current && lastRest.current !== phase) {
        bump(phase === "live", phase === "idle");
      }
      lastRest.current = phase;
      fetchedSinceRest.current = false;
    }
  }, [phase]);

  // Inline: the live controls; or a way back to art after a failure, or out of a go-live under
  // way, a mode change's included (audit S12: Cancel stays visible; a hand change during the
  // switch wins and the mode's row reads "set by hand during the switch").
  const canStop = sc.canBackToArt || sc.busy === "back to art";
  const showCore = sc.liveNow || (canStop && (phase === "failed" || !!desiredLive));
  const name = screen.name || friendlyName(screenId);

  return (
    <section
      data-screen={screenId}
      aria-label={name}
      data-focused={focused || undefined}
      className={cn(
        CARD_CLASS,
        "scroll-mt-3",
        summary.live && "border-teal-200",
        summary.problem && "border-rose-200",
        focused && "ring-2 ring-teal-600/45 ring-offset-2 ring-offset-background",
      )}
    >
      <Collapsible open={open} onOpenChange={setOpen}>
        <CollapsibleTrigger
          data-testid={`more-${screenId}`}
          className="flex min-h-[4.5rem] w-full items-center gap-3 rounded-2xl px-4 py-3 text-left hover:bg-stone-50/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
        >
          <ArtThumb
            src={summary.live ? onGlass?.thumb : art?.thumb}
            live={summary.live}
            problem={summary.problem}
            className={cn(summary.busy && "ring-2 ring-amber-300")}
          />
          <span className="min-w-0 flex-1">
            <span className="block truncate text-[17px] font-semibold leading-snug">{name}</span>
            {/* The status wraps rather than truncates, so it stays readable on a narrow phone. */}
            <span className="mt-0.5 flex min-w-0 items-start gap-2 text-base leading-snug">
              <Dot tone={summary.tone} pulse={summary.busy} className="mt-[7px]" />
              <span className="line-clamp-2 min-w-0">
                <span className={TEXT_CLASSES[summary.tone]} data-testid={`status-${screenId}`}>
                  {summary.text}
                </span>
                {summary.hint && <span className="text-muted-foreground"> · {summary.hint}</span>}
              </span>
            </span>
          </span>
          <span className="flex shrink-0 items-center gap-1 text-[15px] text-muted-foreground">
            <span>{open ? "Less" : "More"}</span>
            <ChevronDown
              aria-hidden
              className={cn("h-5 w-5 transition-transform motion-reduce:transition-none", open && "rotate-180")}
            />
          </span>
        </CollapsibleTrigger>

        <GeneratorLink screenId={screenId} prompt={summary.live ? null : art?.prompt} />
        <DetailsSwitch api={api} screenId={screenId} screen={screen} />

        {showCore && <CoreControls screenId={screenId} sc={sc} live={sc.liveNow ? live : undefined} />}
        {sc.actionError && (
          <div className="px-4 pb-4">
            <Notice tone="bad">{sc.actionError}</Notice>
          </div>
        )}

        <CollapsibleContent>
          <div className="space-y-5 border-t border-stone-200/80 px-4 pb-5 pt-4">
            {(phase === "failed" || screen.last_error) && (
              <Notice tone="bad">
                {/* One sentence; the raw error is under Technical details. */}
                {problemWords(screen.last_error, phase)}
                {desiredLive ? " It's trying again." : ""}
              </Notice>
            )}
            {obs.page_connected === false && (
              <Notice tone="info">
                The TV page isn't open right now. Live still starts; the TV joins it when its page opens.
              </Notice>
            )}
            {catalogError && (
              <Notice tone={catalog ? "warn" : "bad"}>
                {catalog ? "Couldn't refresh the list of scenes; showing the last one." : "The list of scenes isn't available."}{" "}
                <span className="opacity-80">{catalogError}</span>
              </Notice>
            )}
            {streamReachable === false && sc.liveNow && (
              <Notice tone="warn">The live pictures aren't answering right now. Back to art still works.</Notice>
            )}

            {sc.liveNow ? (
              <LiveSettingsPanel screenId={screenId} sc={sc} live={live} />
            ) : sc.alreadyLive ? (
              <Notice tone="muted">This screen is on its way to live. Its settings appear here once it's live.</Notice>
            ) : (
              <GoLivePanel
                screenId={screenId}
                sc={sc}
                streamReachable={streamReachable}
                live={library ? { lib: library, api } : undefined}
              />
            )}

            <Snapshots api={api} screenId={screenId} revs={snapRevs} exists={screen.snapshots} onError={reportError} />

            {room && <ScreenEvents room={room} screenId={screenId} addLog={addLog} />}

            <Unroll title="Technical details" testId={`details-${screenId}`}>
              <LiveDetails api={api} screenId={screenId} screen={screen} />
              <ScreenDetails screenId={screenId} screen={screen} scenes={sc.scenes} transitions={sc.transitions} />
            </Unroll>
          </div>
        </CollapsibleContent>
      </Collapsible>
    </section>
  );
});

/** The screen's schedule events (from its schedule, none named here), unrolled on request. */
function ScreenEvents({ room, screenId, addLog }: { room: string; screenId: string; addLog: (e: NewLogEntry) => void }) {
  const events = useRoomEvents(room, screenId);
  if (!events?.length) return null;
  return (
    <Unroll title="Events" hint={`${events.length}`} testId={`events-${screenId}`}>
      <EventsList
        room={room}
        destination={screenId}
        className="px-1 pb-1"
        onSent={(ev) => addLog({ type: "schedule", screen: screenId, level: "info", summary: `${ev.label} sent to ${screenId}` })}
      />
    </Unroll>
  );
}
