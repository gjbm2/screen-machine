// One screen's strip on its generator tab (owner, 28 Oct: "we DO want to see what's on screen, and see basic screen
// commands and controls", compact and complete): the house pattern of the Room page's ScreenCard.
//
// Row 1: a small picture of what the screen shows now (the art, or the live scene with LIVE), which opens the screen's
//   own page in a new tab and is where a picture is dropped to publish it; one line of status ("Showing art · since
//   14:30", "Live · Gallery"); one action (Go live / Back to art); and More (⋯).
// Row 2: small labelled controls: Look (while live), the schedule (its state, pause/resume, and its details), the
//   picture before / after among the favourites, the light mask, Talk (voice to prompt) and Upload.
// More: choose a scene, the schedule's events and variables, refresh, the bucket's maintenance, every screen's
//   picture, and the links (the screen's page, Room, its schedule, its settings).
// The schedule chip's fold: what the schedule does next, and its events (tap to send one) and variables (view, and
//   change) (ScreenEventsValues.tsx; owner, 28 Oct: "events & variables exposed ... in both places").
//
// variant "controls" (the Room page's screen cards, owner 28 Oct: "the same sort of image control stuff as I can on
// Generator page"): row 2 only, with a smaller More (events and variables, refresh, every screen's picture,
// maintenance). The card already has the picture, the status, Go live / Back to art, the look and the links.
//
// The actions come in as props: BucketGridView passes its own (it has the bucket read for the grid), the Room cards
// pass useScreenStripState's; the shared ones are one implementation (useScreenStripState.ts). The live parts come
// from useScreenLive. Off the LAN, or on a screen that can't go live, the strip shows without them. Talk shows only
// when the page gives it (the Generator's voice prompt). Components here are module-level (one defined in another's
// body remounts on every render).

import { useRef, useState, type ReactNode, type Ref } from "react";
import { format, isSameDay, differenceInCalendarDays } from "date-fns";
import {
  CalendarClock,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Copy,
  ExternalLink,
  Images,
  ListChecks,
  Mic,
  Moon,
  MoreHorizontal,
  Pause,
  Play,
  RefreshCw,
  Send,
  Settings,
  SlidersHorizontal,
  Sparkles,
  Trash2,
  Upload,
} from "lucide-react";
import { AppLink } from "@/components/nav/AppLink";
import { displayUrl, roomPageUrl, schedulerUrl, settingsUrl } from "@/components/nav/appNav";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { storageGet, storageSet } from "@/pages/Room/api";
import { Spinner } from "@/pages/Room/bits";
import { RoomThemeStyle, THEME_CLASS } from "@/pages/Room/theme";
import {
  SchedulerDetails,
  SchedulerStateIcon,
  scheduleState,
  useSchedulerActions,
  type NextAction,
} from "@/components/image-display/SchedulerPanel";
import { liveStateOf, useRoomLinkSelect } from "./roomLink";
import { ArtThumb } from "./ScreenArt";
import { ScreenEventsValues } from "./ScreenEventsValues";
import { LiveLookPanel, LiveScenePicker, useScreenLive, type ScreenLive } from "./ScreenLivePanel";

const LOOK_OPEN_KEY = "sm_strip_look_open";

/** What the strip's picture shows and where it leads. */
export interface StripPicture {
  src: string | null;
  live: boolean;
  problem: boolean;
  busy: boolean;
  /** The screen's own page (/display/<screen>). */
  href: string;
  name: string;
}

export interface ScreenStripProps {
  screenId: string;
  name: string;
  /** "full" (the Generator): the whole strip; "controls" (the Room page's cards): row 2 and a smaller More. */
  variant?: "full" | "controls";
  /** Its room (rooms.json id), for the room's shared variables; default: the room link's. */
  room?: string | null;
  /** A destination with no screen (no picture, no screen page). */
  headless?: boolean;
  hasBucket: boolean;
  /** The published art's thumbnail and when it went up (ISO); the full strip only. */
  artThumb?: string | null;
  publishedAt?: string | null;
  /** Draws the picture (BucketGridView wraps it as its publish drop target); default: the plain picture. */
  renderPicture?: (pic: StripPicture) => ReactNode;
  /** Put on the head (rows 1-2) while the tab bar is pinned, so it stays in view; the folds scroll under it. */
  stickyClassName?: string;
  scheduler: { isRunning?: boolean; isPaused?: boolean; nextAction?: NextAction | null };
  refreshScheduler: (destination: string) => void;
  /** Favourites to step through (0: Previous / Next are off; null: not known, so they stay on). */
  favourites: number | null;
  stepPicture: (mode: "Previous" | "Next") => void;
  stepping: boolean;
  maskOn: boolean;
  setMask: (on: boolean) => void;
  /** Talk (voice to the generator's prompt): shown only when given. */
  talking?: boolean;
  toggleTalk?: () => void;
  onUpload: () => void;
  onRefresh: () => void;
  refreshing: boolean;
  onPurge: () => void;
  onReindex: () => void;
  onExtract: () => void;
  onAllPictures: () => void;
  loadingAllPictures: boolean;
}

// ── Pieces ────────────────────────────────────────────────────────────────────

const CHIP =
  "inline-flex h-9 shrink-0 items-center gap-1 whitespace-nowrap rounded-xl border border-stone-300 bg-white px-2 text-[13px] font-medium sm:gap-1.5 sm:px-2.5 sm:text-[14px] text-stone-800 hover:bg-stone-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50";
const CHIP_ON = "border-teal-700 bg-teal-700 text-white hover:bg-teal-800";
const ITEM = "min-h-10 gap-2 rounded-lg text-[15px]";

/** The picture of what the screen shows; opens its page in a new tab. Exported for BucketGridView's drop target. */
export function ScreenPicture({
  pic,
  nodeRef,
  dropOver = false,
  dragging = false,
  className,
}: {
  pic: StripPicture;
  nodeRef?: Ref<HTMLAnchorElement>;
  /** A picture is being dragged over it (drop publishes it). */
  dropOver?: boolean;
  /** A picture is being dragged somewhere on the page. */
  dragging?: boolean;
  className?: string;
}) {
  const label = `Open what ${pic.name} is showing, in a new tab`;
  return (
    <a
      ref={nodeRef}
      href={pic.href}
      target="_blank"
      rel="noreferrer"
      title={dragging ? "Drop a picture here to put it on the screen" : label}
      aria-label={label}
      data-testid={`strip-picture-${pic.name}`}
      className={cn(
        "group relative block shrink-0 self-start rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        dropOver && "ring-2 ring-teal-600 ring-offset-2",
        className,
      )}
    >
      <ArtThumb
        src={pic.src}
        live={pic.live}
        problem={pic.problem}
        className={cn("aspect-video h-auto w-24 sm:w-32", pic.busy && "ring-2 ring-amber-300", dropOver && "opacity-70")}
      />
      {dragging ? (
        <span className="absolute inset-0 flex flex-col items-center justify-center gap-0.5 rounded-lg bg-black/45 text-[12px] font-medium text-white">
          {dropOver && <Send className="h-4 w-4" aria-hidden />}
          {dropOver ? "Put it up" : "Drop here"}
        </span>
      ) : (
        <span className="absolute right-1 top-1 grid h-6 w-6 place-items-center rounded-full bg-white/90 text-stone-700 shadow-sm group-hover:bg-white">
          <ExternalLink className="h-3.5 w-3.5" aria-hidden />
        </span>
      )}
    </a>
  );
}

/** A chip with a tooltip; a disabled one sits in a span so the tooltip still says why. */
function Tip({ tip, children, disabled }: { tip: string; children: ReactNode; disabled?: boolean }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>{disabled ? <span tabIndex={0} className="inline-flex rounded-xl">{children}</span> : children}</TooltipTrigger>
      <TooltipContent side="bottom" className={THEME_CLASS}>
        {tip}
      </TooltipContent>
    </Tooltip>
  );
}

function sinceWords(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const now = new Date();
  if (isSameDay(d, now)) return `since ${format(d, "HH:mm")}`;
  if (differenceInCalendarDays(now, d) < 7) return `since ${format(d, "EEE HH:mm")}`;
  return `since ${format(d, "d MMM")}`;
}

interface Status {
  text: string;
  hint: string | null;
  /** A coloured dot while something's happening (live, going, failed); none for plain art. */
  dot: string | null;
  pulse?: boolean;
}

function statusOf(live: ScreenLive, since: string | null): Status {
  const art: Status = { text: "Showing art", hint: since, dot: null };
  if (!live.shown) return art;
  switch (live.ls) {
    case "live":
      return { text: `Live · ${live.nowInfo?.label ?? "a scene"}`, hint: null, dot: "bg-red-600", pulse: true };
    case "entering":
      return { text: "Going live…", hint: live.detail || live.nowInfo?.label || null, dot: "bg-amber-500", pulse: true };
    case "leaving":
      return { text: "Going back to art…", hint: live.detail, dot: "bg-amber-500", pulse: true };
    case "failed":
      return { text: "Couldn't go live", hint: live.lastError || live.detail, dot: "bg-rose-600" };
    default:
      return live.pageMissing ? { ...art, hint: "the TV isn't connected" } : art;
  }
}

/** Go live (with the scene it last showed), or Back to art. */
function PrimaryButton({ live }: { live: ScreenLive }) {
  const { ls, want, busy, pageMissing } = live;
  const toArt = ls === "live" || ls === "entering" || ls === "leaving" || (ls === "failed" && want);
  if (toArt) {
    return (
      <button
        type="button"
        onClick={live.backToArt}
        disabled={!!busy || ls === "leaving"}
        data-testid={`strip-back-${live.screenId}`}
        className="inline-flex h-9 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-xl border border-stone-300 bg-white px-3 text-[14px] font-semibold text-stone-800 hover:bg-stone-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
      >
        {(busy || ls === "leaving") && <Spinner className="h-3.5 w-3.5" />}
        Back to art
      </button>
    );
  }
  const scene = live.nextInfo?.label ?? "the room's scene";
  return (
    <button
      type="button"
      onClick={() => live.goLive(live.lastKey)}
      disabled={!!busy || pageMissing}
      title={pageMissing ? "The TV isn't connected, so it can't go live" : `Go live with ${scene}`}
      data-testid={`strip-golive-${live.screenId}`}
      className="inline-flex h-9 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-xl bg-teal-700 px-3 text-[14px] font-semibold text-white hover:bg-teal-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
    >
      {busy ? <Spinner className="h-3.5 w-3.5" /> : <Play className="h-4 w-4" aria-hidden />}
      Go live
    </button>
  );
}

/** More: with `live` (the full strip) the scene and the links too; without (the Room cards) the strip's own extras. */
function MoreMenu({ p, live, onEvents }: { p: ScreenStripProps; live: ScreenLive | null; onEvents: () => void }) {
  const canChoose = !!live && live.shown && (live.ls === "art" || live.ls === "failed" || live.liveNow);
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        aria-label={`More for ${p.name}`}
        title="More"
        data-testid={`strip-more-${p.screenId}`}
        className="grid h-9 w-10 shrink-0 place-items-center rounded-xl border border-stone-300 bg-white text-stone-700 hover:bg-stone-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring data-[state=open]:bg-stone-100"
      >
        <MoreHorizontal className="h-5 w-5" aria-hidden />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className={cn(
          THEME_CLASS,
          "max-h-[var(--radix-dropdown-menu-content-available-height)] w-64 overflow-y-auto rounded-xl p-1.5",
        )}>
        {canChoose && live && (
          <DropdownMenuItem
            className={ITEM}
            disabled={!live.lib || (!live.liveNow && live.pageMissing)}
            onSelect={() => live.setPicker(live.liveNow ? "cue" : "choose")}
          >
            <Sparkles className="h-4 w-4 text-teal-700" aria-hidden />
            {live.liveNow ? "Change scene…" : "Go live with a scene…"}
          </DropdownMenuItem>
        )}
        <DropdownMenuItem className={ITEM} onSelect={onEvents} data-testid={`strip-events-${p.screenId}`}>
          <ListChecks className="h-4 w-4" aria-hidden /> Events &amp; variables
        </DropdownMenuItem>
        <DropdownMenuItem className={ITEM} disabled={p.refreshing} onSelect={p.onRefresh}>
          <RefreshCw className={cn("h-4 w-4", p.refreshing && "animate-spin")} aria-hidden /> Refresh
        </DropdownMenuItem>
        <DropdownMenuItem className={ITEM} disabled={p.loadingAllPictures} onSelect={p.onAllPictures}>
          <Images className="h-4 w-4" aria-hidden /> Every screen's picture
        </DropdownMenuItem>
        {p.hasBucket && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuLabel className="px-2 py-1 text-[12px] font-semibold uppercase tracking-wide text-muted-foreground">
              Maintenance
            </DropdownMenuLabel>
            <DropdownMenuItem className={ITEM} onSelect={p.onPurge}>
              <Trash2 className="h-4 w-4" aria-hidden /> Purge non-favourites…
            </DropdownMenuItem>
            <DropdownMenuItem className={ITEM} onSelect={p.onReindex}>
              <RefreshCw className="h-4 w-4" aria-hidden /> Re-index
            </DropdownMenuItem>
            <DropdownMenuItem className={ITEM} onSelect={p.onExtract}>
              <Copy className="h-4 w-4" aria-hidden /> Extract JSON
            </DropdownMenuItem>
          </>
        )}
        {live && <DropdownMenuSeparator />}
        {live && !p.headless && (
          <DropdownMenuItem asChild className={ITEM}>
            <a href={displayUrl(p.screenId)} target="_blank" rel="noreferrer">
              <ExternalLink className="h-4 w-4" aria-hidden /> Open the screen's page
            </a>
          </DropdownMenuItem>
        )}
        {live?.onLan && live.inRoom && (
          <DropdownMenuItem asChild className={ITEM}>
            <AppLink href={roomPageUrl(p.screenId)} state={{ from: "Generator" }}>
              <SlidersHorizontal className="h-4 w-4" aria-hidden /> Open in Room
            </AppLink>
          </DropdownMenuItem>
        )}
        {live && (
          <DropdownMenuItem asChild className={ITEM}>
            <AppLink href={schedulerUrl(p.screenId)} state={{ from: "Generator" }}>
              <CalendarClock className="h-4 w-4" aria-hidden /> Schedule page
            </AppLink>
          </DropdownMenuItem>
        )}
        {live?.inRoom && (
          <DropdownMenuItem asChild className={ITEM}>
            <AppLink href={settingsUrl(`screen-${p.screenId}`)} state={{ from: "Generator" }}>
              <Settings className="h-4 w-4" aria-hidden /> Screen settings
            </AppLink>
          </DropdownMenuItem>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** The schedule: its state (tap for its details), and pause / resume (or start, when it's off). */
function ScheduleChip({
  p,
  open,
  onToggle,
  actions,
}: {
  p: ScreenStripProps;
  open: boolean;
  onToggle: () => void;
  actions: ReturnType<typeof useSchedulerActions>;
}) {
  const state = scheduleState(p.scheduler.isRunning, p.scheduler.isPaused);
  const words = state === "on" ? "Schedule on" : state === "paused" ? "Schedule paused" : "Schedule off";
  const act = state === "on" ? "pause" : state === "paused" ? "unpause" : "start";
  const actWords = act === "pause" ? "Pause the schedule" : act === "unpause" ? "Resume the schedule" : "Start the schedule";
  return (
    <span className="inline-flex shrink-0 items-center overflow-hidden rounded-xl border border-stone-300 bg-white">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        title={open ? "Hide the schedule's details" : "The schedule's details"}
        data-testid={`strip-schedule-${p.screenId}`}
        className={cn(
          "inline-flex h-9 items-center gap-1 whitespace-nowrap pl-2 pr-1 text-[13px] font-medium text-stone-800 sm:gap-1.5 sm:pl-2.5 sm:pr-1.5 sm:text-[14px] hover:bg-stone-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
          open && "bg-stone-100",
        )}
      >
        <SchedulerStateIcon state={state} />
        {words}
        <ChevronDown aria-hidden className={cn("h-3.5 w-3.5 text-stone-500 transition-transform motion-reduce:transition-none", open && "rotate-180")} />
      </button>
      <button
        type="button"
        onClick={() => void actions.run(act)}
        aria-label={actWords}
        title={actWords}
        className="grid h-9 w-8 place-items-center border-l border-stone-200 sm:w-9 text-stone-700 hover:bg-stone-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
      >
        {act === "pause" ? <Pause className="h-4 w-4" aria-hidden /> : <Play className="h-4 w-4" aria-hidden />}
      </button>
    </span>
  );
}

/** The picture before / after this one among the bucket's favourites. */
function PictureStepper({ p }: { p: ScreenStripProps }) {
  // favourites null: not known (the Room cards), so they stay on and the server says when there's nothing to step to
  const off = p.favourites === 0 || p.stepping;
  const why = p.favourites === 0 ? "No favourites to step through" : null;
  const btn =
    "grid h-9 w-8 place-items-center text-stone-700 hover:bg-stone-50 sm:w-9 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-40";
  return (
    <span
      className="inline-flex shrink-0 items-center overflow-hidden rounded-xl border border-stone-300 bg-white"
      title={why ?? undefined}
      role="group"
      aria-label="Picture on the screen"
    >
      <button type="button" className={btn} disabled={off} onClick={() => p.stepPicture("Previous")} aria-label="Previous favourite" title="Previous favourite">
        <ChevronLeft className="h-5 w-5" aria-hidden />
      </button>
      <span className="text-[13px] font-medium text-stone-600 sm:px-0.5 sm:text-[14px]">{p.stepping ? <Spinner className="mx-3 h-3.5 w-3.5" /> : "Picture"}</span>
      <button type="button" className={btn} disabled={off} onClick={() => p.stepPicture("Next")} aria-label="Next favourite" title="Next favourite">
        <ChevronRight className="h-5 w-5" aria-hidden />
      </button>
    </span>
  );
}

const FOLD = "rounded-b-2xl border border-t-0 border-stone-200 bg-card px-3 pb-3 pt-3";

/** Row 2's controls: Look (while live, full strip only), the schedule, Previous / Next, the light mask, Talk, Upload. */
function StripControls({
  p,
  className,
  look,
  scheduleOpen,
  toggleSchedule,
  actions,
  maskBlocked,
  end,
}: {
  p: ScreenStripProps;
  className?: string;
  look?: ReactNode;
  scheduleOpen: boolean;
  toggleSchedule: () => void;
  actions: ReturnType<typeof useSchedulerActions>;
  maskBlocked: boolean;
  /** At the row's end (the Room cards' More). */
  end?: ReactNode;
}) {
  const maskTip = maskBlocked
    ? "Live runs at full brightness; the light mask is for art"
    : p.maskOn
      ? "Light mask on: the screen dims and warms with the room's light. Tap to turn it off"
      : "Light mask off: tap to dim and warm the screen with the room's light";
  return (
    <div className={cn("flex min-w-0 flex-wrap items-center gap-1.5", className)}>
      {look}
      <ScheduleChip p={p} open={scheduleOpen} onToggle={toggleSchedule} actions={actions} />
      {p.hasBucket && !p.headless && <PictureStepper p={p} />}
      <Tip tip={maskTip} disabled={maskBlocked}>
        <button
          type="button"
          aria-pressed={p.maskOn && !maskBlocked}
          disabled={maskBlocked}
          onClick={() => p.setMask(!p.maskOn)}
          data-testid={`strip-mask-${p.screenId}`}
          className={cn(CHIP, p.maskOn && !maskBlocked && CHIP_ON)}
        >
          <Moon className="h-4 w-4" aria-hidden /> Light mask
        </button>
      </Tip>
      {p.toggleTalk && (
        <Tip tip={p.talking ? "Stop recording" : "Talk: say what to make, by voice"}>
          <button
            type="button"
            aria-pressed={!!p.talking}
            onClick={p.toggleTalk}
            className={cn(CHIP, p.talking && "border-red-600 bg-red-600 text-white hover:bg-red-700")}
          >
            <Mic className={cn("h-4 w-4", p.talking && "motion-safe:animate-pulse")} aria-hidden />
            {p.talking ? "Stop talking" : "Talk"}
          </button>
        </Tip>
      )}
      {p.hasBucket && (
        <button type="button" onClick={p.onUpload} className={CHIP} title="Upload pictures to this screen's bucket">
          <Upload className="h-4 w-4" aria-hidden /> Upload
        </button>
      )}
      {end}
    </div>
  );
}

/** The schedule chip's fold: what it does next, then its events and variables. */
function ScheduleFold({ p, actions }: { p: ScreenStripProps; actions: ReturnType<typeof useSchedulerActions> }) {
  return (
    <>
      <SchedulerDetails
        destination={p.screenId}
        isRunning={p.scheduler.isRunning}
        isPaused={p.scheduler.isPaused}
        nextAction={p.scheduler.nextAction}
        actions={actions}
      />
      <div className="mt-3 border-t border-stone-200 pt-3">
        <ScreenEventsValues screenId={p.screenId} room={p.room} />
      </div>
    </>
  );
}

/** The schedule fold's open state; "Events & variables" (More) opens it and brings it into view. */
function useScheduleFold() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const showEvents = () => {
    setOpen(true);
    // after the menu closes and the fold renders
    setTimeout(() => {
      const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
      ref.current?.scrollIntoView({ block: "nearest", behavior: reduce ? "auto" : "smooth" });
    }, 80);
  };
  return { open, toggle: () => setOpen((o) => !o), showEvents, ref };
}

// ── The strip ─────────────────────────────────────────────────────────────────

export function ScreenStrip(p: ScreenStripProps) {
  return p.variant === "controls" ? <ControlsStrip p={p} /> : <FullStrip p={p} />;
}

/** The Room cards' strip: row 2 with a smaller More, and the schedule's fold under it (no frame: the card is one). */
function ControlsStrip({ p }: { p: ScreenStripProps }) {
  const actions = useSchedulerActions(p.screenId, p.refreshScheduler);
  const fold = useScheduleFold();
  // Live runs at full brightness: the light mask is for art.
  const maskBlocked = useRoomLinkSelect((s) => liveStateOf(s.status?.screens?.[p.screenId]) === "live");
  return (
    <div className={THEME_CLASS} data-testid={`screen-strip-${p.screenId}`}>
      <RoomThemeStyle />
      <StripControls
        p={p}
        scheduleOpen={fold.open}
        toggleSchedule={fold.toggle}
        actions={actions}
        maskBlocked={maskBlocked}
        end={<MoreMenu p={p} live={null} onEvents={fold.showEvents} />}
      />
      {fold.open && (
        <div ref={fold.ref} className="mt-3 scroll-mt-3 border-t border-stone-200/80 pt-3">
          <ScheduleFold p={p} actions={actions} />
        </div>
      )}
    </div>
  );
}

function FullStrip({ p }: { p: ScreenStripProps }) {
  const { screenId, name, headless = false } = p;
  const live = useScreenLive(screenId);
  const actions = useSchedulerActions(screenId, p.refreshScheduler);
  const fold = useScheduleFold();
  const scheduleOpen = fold.open;
  const [lookWanted, setLookWanted] = useState(() => storageGet(LOOK_OPEN_KEY) === "1");
  const lookOpen = live.shown && live.liveNow && lookWanted;
  const toggleLook = () =>
    setLookWanted((o) => {
      storageSet(LOOK_OPEN_KEY, o ? "0" : "1");
      return !o;
    });

  const status = statusOf(live, sinceWords(p.publishedAt ?? null));
  const liveShown = live.shown && live.liveNow;
  const pic: StripPicture = {
    src: liveShown ? live.nowInfo?.thumb ?? null : p.artThumb ?? null,
    live: liveShown,
    problem: live.shown && live.ls === "failed",
    busy: live.shown && (live.ls === "entering" || live.ls === "leaving"),
    href: displayUrl(screenId),
    name,
  };
  const folded = scheduleOpen || lookOpen;
  const maskBlocked = live.shown && live.liveNow;

  return (
    // contain:inline-size: the home page sits in a Radix ScrollArea (display:table), which otherwise grows to the
    // strip's one-line texts and pushes its buttons off a phone's screen.
    <div className={cn(THEME_CLASS, "mb-2 [contain:inline-size]")} data-testid={`screen-strip-${screenId}`}>
      <RoomThemeStyle />
      <div className={p.stickyClassName}>
        <section
          aria-label={`${name}: what's on it`}
          className={cn(
            "rounded-2xl border bg-card p-2 shadow-sm",
            liveShown ? "border-red-300" : "border-stone-200",
            folded && "rounded-b-none",
          )}
        >
          <div className={cn("grid items-center gap-x-3 gap-y-2", headless ? "grid-cols-1" : "grid-cols-[auto_minmax(0,1fr)]")}>
            {!headless && (
              <div className="sm:row-span-2">{p.renderPicture ? p.renderPicture(pic) : <ScreenPicture pic={pic} />}</div>
            )}

            <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1.5">
              <p
                // With Go live / Back to art, a phone puts the status on its own line above the buttons; without, the
                // More button sits beside it.
                className={cn(
                  "min-w-0 flex-1 text-[14px] leading-snug sm:text-[15px]",
                  live.shown && "basis-full sm:basis-auto",
                )}
                title={status.hint ? `${status.text} · ${status.hint}` : status.text}
                data-testid={`strip-status-${screenId}`}
              >
                {status.dot && (
                  <span
                    aria-hidden
                    className={cn("mr-1.5 inline-block h-2 w-2 rounded-full align-middle", status.dot, status.pulse && "motion-safe:animate-pulse")}
                  />
                )}
                <span className="font-semibold">{status.text}</span>
                {status.hint && (
                  <span className="text-[13px] text-muted-foreground sm:text-[15px]">
                    {" · "}
                    <span className={cn(status.hint.length < 24 && "whitespace-nowrap")}>{status.hint}</span>
                  </span>
                )}
                {live.shown && !live.connected && <span className="text-muted-foreground"> · reconnecting…</span>}
              </p>
              <div className="flex items-center gap-2">
                {live.shown && <PrimaryButton live={live} />}
                <MoreMenu p={p} live={live} onEvents={fold.showEvents} />
              </div>
            </div>

            <StripControls
              p={p}
              className={cn(!headless && "col-span-2 sm:col-span-1 sm:col-start-2")}
              scheduleOpen={scheduleOpen}
              toggleSchedule={fold.toggle}
              actions={actions}
              maskBlocked={maskBlocked}
              look={
                liveShown && (
                  <button
                    type="button"
                    onClick={toggleLook}
                    aria-expanded={lookOpen}
                    title={lookOpen ? "Hide the live look" : "The live look: scene, Auto, energy, colours, effects"}
                    data-testid={`strip-look-${screenId}`}
                    className={cn(CHIP, lookOpen && "bg-stone-100")}
                  >
                    <SlidersHorizontal className="h-4 w-4" aria-hidden /> Look
                    <ChevronDown aria-hidden className={cn("h-3.5 w-3.5 text-stone-500 transition-transform motion-reduce:transition-none", lookOpen && "rotate-180")} />
                  </button>
                )
              }
            />
          </div>
          {live.shown && live.error && <p className="mt-2 text-[14px] text-rose-700">{live.error}</p>}
        </section>
      </div>

      {scheduleOpen && (
        <div ref={fold.ref} className={cn(FOLD, "scroll-mt-28", lookOpen && "rounded-b-none")}>
          <ScheduleFold p={p} actions={actions} />
        </div>
      )}
      {lookOpen && (
        <div className={cn(FOLD, scheduleOpen && "rounded-t-none border-t")}>
          <LiveLookPanel live={live} />
        </div>
      )}

      {live.shown && <LiveScenePicker live={live} />}
    </div>
  );
}
