// One screen, compact (space pass, owner 25 Oct: "vertical space is at a HIGH PREMIUM"): the picture of what's on it
// (the art's thumbnail, or the live scene), which opens its live page; its name; what it's doing in one line
// ("Showing art", "Live · Gallery"); and one action (Go live / Back to art / Cancel) beside More. Under them, the
// Generator's picture controls (owner, 28 Oct: "the same sort of image control stuff as I can on Generator page --
// prob. using same component"): its screen strip's controls row (ScreenStrip variant "controls"): the schedule (its
// fold has what it does next, its events and its variables), the picture before / after, the light mask, Upload, and
// a small More (events and variables, refresh, every screen's picture, maintenance). Everything else unrolls under
// More: the scene (picked for going live, or changed while live) and its settings, the look controls, "Details on the
// screen", snapshots, the technical details, and the links to its tab in the generator (with the art's prompt), its
// schedule (with what it's doing) and its settings.
// The Screens fold lays the cards out in a grid (2-3 across on an iPad, one per row on a phone).

import { memo, useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronRight, ExternalLink } from "lucide-react";
import { AppLink } from "@/components/nav/AppLink";
import { displayUrl, settingsUrl } from "@/components/nav/appNav";
import { BucketMaintenanceDialog } from "@/components/image-display/BucketMaintenanceDialog";
import { BucketUploadDialog } from "@/components/image-display/BucketUploadDialog";
import { ArtThumb, GeneratorLink, ScheduleLink, usePublishedArt } from "@/components/room/ScreenArt";
import { ScreenStrip } from "@/components/room/ScreenStrip";
import { useScreenStripState } from "@/components/room/useScreenStripState";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";
import { RoomApi } from "./api";
import { Dot, Notice, Unroll } from "./bits";
import { GoLiveNotices, LiveSettingsPanel, PrimaryAction, ScenePanel, type LiveBits } from "./LiveControls";
import { hasFeature, LOOK_SCOPE_FEATURE, sceneInfo, type LookScope } from "./liveLib";
import { ScreenDetails } from "./ScreenDetails";
import { DetailsSwitch, LiveDetails, TransitionDetails } from "./LiveDetails";
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
  /** Its room (rooms.json id): the room's shared variables this screen uses show in its schedule's fold. */
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

  // The face's action: Back to art while live; or a way back to art after a failure, or out of a go-live under
  // way, a mode change's included (audit S12: Cancel stays visible; a hand change during the
  // switch wins and the mode's row reads "set by hand during the switch").
  const canStop = sc.canBackToArt || sc.busy === "back to art";
  const showCore = sc.liveNow || (canStop && (phase === "failed" || !!desiredLive));
  // Showing art, with live allowed: going live is one tap on the face.
  const showGoLive = !showCore && !sc.liveNow && !sc.alreadyLive && sc.liveDisabled !== "policy";
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
        <div className="flex gap-3 p-3">
          {/* The picture opens the screen's own live page (/display/<screen>, the viewer role: it only watches), what
              the TV shows right now, in a new tab (owner, 25 Oct: "you have a TRIVIAL way to show user current live
              view -- the ACTUAL LIVE HTML PAGE ... should be easily retrievable"). */}
          <a
            href={displayUrl(screenId)}
            target="_blank"
            rel="noreferrer"
            title={`Open what ${name} is showing, live`}
            aria-label={`Open what ${name} is showing, live`}
            data-testid={`live-page-${screenId}`}
            className="group relative shrink-0 self-start rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <ArtThumb
              src={summary.live ? onGlass?.thumb : art?.thumb}
              live={summary.live}
              problem={summary.problem}
              className={cn("aspect-video h-auto w-40", summary.busy && "ring-2 ring-amber-300")}
            />
            <span className="absolute right-1 top-1 grid h-6 w-6 place-items-center rounded-full bg-white/90 text-stone-700 shadow-sm group-hover:bg-white">
              <ExternalLink className="h-3.5 w-3.5" aria-hidden />
            </span>
          </a>
          <div className="flex min-w-0 flex-1 flex-col justify-between gap-1">
            <div className="min-w-0">
              <p className="truncate text-base font-semibold leading-snug">{name}</p>
              <p
                className="flex min-w-0 items-center gap-1.5 text-[15px] leading-snug"
                title={summary.hint ? `${summary.text} · ${summary.hint}` : summary.text}
              >
                <Dot tone={summary.tone} pulse={summary.busy} />
                <span className="min-w-0 truncate">
                  <span className={TEXT_CLASSES[summary.tone]} data-testid={`status-${screenId}`}>
                    {summary.text}
                  </span>
                  {summary.hint && <span className="text-muted-foreground"> · {summary.hint}</span>}
                </span>
              </p>
            </div>
            <div className="flex items-center gap-2">
              {showCore && <PrimaryAction screenId={screenId} sc={sc} kind="stop" />}
              {showGoLive && <PrimaryAction screenId={screenId} sc={sc} kind="golive" />}
              <CollapsibleTrigger
                data-testid={`more-${screenId}`}
                aria-label={open ? `Less about ${name}` : `More about ${name}`}
                title={open ? "Less" : "More: scene, settings, snapshots, schedule"}
                className={cn(
                  "grid h-11 w-11 shrink-0 place-items-center rounded-xl border border-stone-200 bg-white text-stone-600 hover:bg-stone-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  open && "bg-stone-100",
                )}
              >
                <ChevronDown aria-hidden className={cn("h-5 w-5 transition-transform motion-reduce:transition-none", open && "rotate-180")} />
              </CollapsibleTrigger>
            </div>
          </div>
        </div>

        <div className="-mt-1 px-3 pb-3">
          <ScreenImageControls screenId={screenId} name={name} room={room} />
        </div>

        {showGoLive && <GoLiveNotices sc={sc} streamReachable={streamReachable} />}
        {sc.actionError && (
          <div className="px-3 pb-3">
            <Notice tone="bad">{sc.actionError}</Notice>
          </div>
        )}

        <CollapsibleContent>
          <div className="space-y-3 border-t border-stone-200/80 px-3 pb-3 pt-3">
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
              <ScenePanel screenId={screenId} sc={sc} live={library ? { lib: library, api, scope } : undefined} />
            )}

            <DetailsSwitch api={api} screenId={screenId} screen={screen} />

            <Snapshots api={api} screenId={screenId} revs={snapRevs} exists={screen.snapshots} onError={reportError} />

            <Unroll title="Technical details" testId={`details-${screenId}`}>
              <LiveDetails api={api} screenId={screenId} screen={screen} />
              <TransitionDetails screenId={screenId} inSpec={sc.form.inSpec} outSpec={sc.form.outSpec} choices={sc.transitions} />
              <ScreenDetails screenId={screenId} screen={screen} scenes={sc.scenes} transitions={sc.transitions} />
            </Unroll>

            <div>
              <GeneratorLink screenId={screenId} prompt={summary.live ? null : art?.prompt} />
              <ScheduleLink screenId={screenId} />
              <AppLink href={settingsUrl(`screen-${screenId}`)} state={{ from: "Room" }} className={cn(FOOT_LINK, "text-stone-600")}>
                Screen settings <ChevronRight className="h-4 w-4" aria-hidden />
              </AppLink>
            </div>
          </div>
        </CollapsibleContent>
      </Collapsible>
    </section>
  );
});

const FOOT_LINK = "inline-flex min-h-11 items-center gap-1 rounded-xl px-1 text-[15px] font-medium text-teal-800 hover:underline";

/** The Generator's picture controls for this screen: its strip's controls row, with what it reads and does
 *  (useScreenStripState), and the same Upload and Maintenance dialogs. Talk (the generator's voice prompt) is left out. */
function ScreenImageControls({ screenId, name, room }: { screenId: string; name: string; room?: string }) {
  const st = useScreenStripState(screenId);
  const { actions } = st;
  return (
    <>
      <ScreenStrip
        variant="controls"
        screenId={screenId}
        name={name}
        room={room}
        headless={st.headless}
        hasBucket={st.hasBucket}
        scheduler={st.scheduler}
        refreshScheduler={st.refreshScheduler}
        favourites={null}
        stepPicture={(mode) => void actions.stepPicture(mode)}
        stepping={actions.stepping}
        maskOn={actions.maskOn}
        setMask={(on) => void actions.setMask(on)}
        onUpload={() => st.setUploadOpen(true)}
        onRefresh={() => void st.refresh()}
        refreshing={st.refreshing}
        onPurge={() => st.setMaintenanceOpen(true)}
        onReindex={() => void actions.reindex()}
        onExtract={() => void actions.extract()}
        onAllPictures={() => void actions.openAllPictures()}
        loadingAllPictures={actions.loadingAllPictures}
      />
      {st.hasBucket && (
        <>
          <BucketUploadDialog open={st.uploadOpen} onOpenChange={st.setUploadOpen} destination={screenId} name={name} />
          <BucketMaintenanceDialog
            isOpen={st.maintenanceOpen}
            onClose={() => st.setMaintenanceOpen(false)}
            destination={screenId}
            onActionComplete={() => void st.refresh()}
          />
        </>
      )}
    </>
  );
}
