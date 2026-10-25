// Every page's header (owner, 24 Oct: "you just haven't really worked the interface ui through properly throughout"):
// one header for the Room page, Music, Lights setup, Settings, the generator and Schedules, so they read as one app.
// Two rows at most (owner, 25 Oct: "vertical space is at a HIGH PREMIUM on a mobile"), nothing said twice:
//
//   Row 1  A place (Room · Music · Lights · Generator): its tabs, this page highlighted (the highlighted tab is the
//          title; Room, Music and Lights only while the room answers, as in the menu; a place's back link would name
//          another tab, so that tab takes its address). A sub-page (Settings, Schedules; a score draws its own): "‹ back"
//          and its title, no tabs ("‹ Room" beside a Room tab said it twice; the menu has every place).
//          Then the state pills, the Mic and AI switches, the link's status and the menu:
//            - state pills: "Live · Party" while a live mode runs (not where the page's line already says it),
//              "● Recording 2:13" while the piano records (a tap: a sheet with Stop), "PC busy" (a tap says what it
//              costs), and any the page adds;
//            - Mic and AI: the room's two switches (TopToggles.tsx), on every page that can reach the room;
//            - status: nothing while all is well; an amber or red pill when the page's link is down (a tap opens the
//              menu, which says it in full);
//            - the menu button (the app's one menu, AppMenu.tsx).
//   Row 2  One status line: the page's own words (the Room page: "Normal · art on all 3 screens · lights off ·
//          nothing playing") and what the switches add beyond on/off ("mic in use for Auto"); a tap opens the Listening
//          details. On a phone the pills and switches end this row (the tabs, or back and title, fill row 1).
//
// Links are AppLinks (no reload). The TV pages (/display) never use this.

import { useMemo, useState, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { ChevronRight, Menu, Square } from "lucide-react";
import { Button } from "@/components/ui/button";
import { roomApi, useRoomLink } from "@/components/room/roomLink";
import { cn } from "@/lib/utils";
import { resolveRoomUrl } from "@/pages/Room/api";
import { Dot, Spinner } from "@/pages/Room/bits";
import { ActionSheet } from "@/pages/Room/overlays";
import { NewTakePanel, useRecorder, type Recorder } from "@/pages/Room/PianoRecorder";
import { clock } from "@/pages/Room/RecordButton";
import { RoomThemeStyle } from "@/pages/Room/theme";
import { TONE_CLASSES, type Tone } from "@/pages/Room/tones";
import { NoteLine, useShowFeed } from "@/pages/Room/MusicCard";
import { TopToggleButtons, topToggleWords, useTopToggles, type TopToggles } from "@/pages/Room/TopToggles";
import type { PcLoad } from "@/pages/Room/usePcLoad";
import { AppLink } from "./AppLink";
import { PLACES, roomPageUrl, type AppPage } from "./appNav";
import { BackLink, placeName } from "./BackLink";
import { useRoomGlance } from "./useRoomGlance";

/** The page's link to what it talks to, as Room's connection.ts words it. */
export interface HeaderStatus {
  tone: Tone;
  /** One word: "Reconnecting", "Offline". */
  word: string;
  /** The full sentence (the tooltip; the menu says it too). */
  sentence?: string;
  pulse?: boolean;
}

export interface PageHeaderProps {
  title: ReactNode;
  /** This page, for the places row and the menu. */
  current: AppPage;
  /** Opens the page's menu (the page renders its AppMenu). */
  onMenu: () => void;
  /** The Mic and AI switches: the page's own (it already has the show feed), "auto" (the header runs its own while
   *  the room answers), or none. */
  toggles?: TopToggles | "auto" | null;
  /** Where the status line (and a long press on a switch) goes: the Listening details; by default /room#listening. */
  onDetails?: () => void;
  /** The page's own words for row 2, before the switches' (the Room page: "Normal · art on all 3 screens · …"). */
  line?: string | null;
  /** The page's link; shown only when it isn't fine. */
  status?: HeaderStatus | null;
  /** "PC busy" (the Room page reads it). */
  pc?: PcLoad | null;
  /** More state pills of the page's own (after Live and Recording). */
  pills?: ReactNode;
  /** Buttons of the page's own, just before the menu button (e.g. the generator's console). */
  actions?: ReactNode;
  /** A sub-page's one back control: where it goes when there is no history (BackLink's `to`), and its words. */
  back?: { to: string; label?: string } | null;
  /** The places tabs (default on). */
  places?: boolean;
  /** Smaller title, for the desktop power pages (the generator, Schedules). */
  compact?: boolean;
  /** Under the rows (e.g. the Room page's "PC busy" sentence). */
  children?: ReactNode;
  className?: string;
}

const PILL =
  "inline-flex h-9 items-center gap-2 rounded-full border px-3 text-[15px] font-medium shadow-sm group-hover:brightness-[0.98] group-focus-visible:ring-2 group-focus-visible:ring-ring";

/** The status pill's colours: amber or red when something is wrong, plain while connecting. */
const TONE_PILL: Record<Tone, string> = { ...TONE_CLASSES, ok: "border-stone-200/80 bg-card text-stone-700", muted: "border-stone-200/80 bg-card text-stone-700" };

const noop = () => undefined;

/** "auto": the header's own show feed and switches, only while this page may use the room API. */
function OwnToggles({ render }: { render: (t: TopToggles | null) => ReactNode }) {
  const room = useRoomLink();
  const api = roomApi();
  const feed = useShowFeed(api, room.canControl, noop);
  const t = useTopToggles(api, feed, noop);
  return <>{render(room.canControl ? t : null)}</>;
}

export function PageHeader(props: PageHeaderProps) {
  if (props.toggles === "auto") return <OwnToggles render={(t) => <HeaderBody {...props} toggles={t} />} />;
  return <HeaderBody {...props} toggles={props.toggles ?? null} />;
}

/** "Live · Party": a tap goes to the modes (on the Room page, back to its top, where they are). */
function LivePill({ mode, current }: { mode: string; current: AppPage }) {
  const inner = (
    <span className={cn(PILL, "border-red-200 bg-red-50 text-red-800")}>
      <span aria-hidden className="h-2 w-2 shrink-0 rounded-full bg-red-600 motion-safe:animate-pulse" />
      Live · {mode}
    </span>
  );
  const cls = "group grid h-11 shrink-0 place-items-center rounded-full px-0.5 focus-visible:outline-none";
  if (current === "room")
    return (
      <button type="button" className={cls} data-testid="header-live" onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })} aria-label={`Live: ${mode}. The modes`}>
        {inner}
      </button>
    );
  return (
    <AppLink href={`${roomPageUrl()}#modes`} className={cls} data-testid="header-live" aria-label={`Live: ${mode}. Open the Room page's modes`}>
      {inner}
    </AppLink>
  );
}

/** "● Recording 2:13", whoever started it, on every page; a tap opens RecordingSheet. */
function RecordingPill({ r, onOpen }: { r: Recorder; onOpen: () => void }) {
  if (!r.recording) return null;
  return (
    <button
      type="button"
      onClick={onOpen}
      data-testid="header-recording"
      aria-label={`Recording, ${clock(r.elapsed)}. Open to stop`}
      className="group grid h-11 shrink-0 place-items-center rounded-full px-0.5 focus-visible:outline-none"
    >
      <span className={cn(PILL, "border-rose-300 bg-rose-50 font-semibold text-rose-900")}>
        <span aria-hidden className="h-2.5 w-2.5 shrink-0 rounded-full bg-rose-600 motion-safe:animate-pulse" />
        Recording <span className="font-mono tabular-nums">{clock(r.elapsed)}</span>
      </span>
    </button>
  );
}

/** The recording pill's sheet: what is being recorded and Stop; once stopped, the new take (Play it · Score · Keep ·
 *  Discard, PianoRecorder's NewTakePanel). */
function RecordingSheet({ r, baseUrl, open, onClose }: { r: Recorder; baseUrl: string; open: boolean; onClose: () => void }) {
  const [stopped, setStopped] = useState(false);
  const close = () => {
    setStopped(false);
    onClose();
  };
  const showTake = stopped && !!r.fresh;
  const shown = open && (r.recording || showTake);
  return (
    <>
      {shown && <RoomThemeStyle />}
      <ActionSheet
        open={shown}
        onOpenChange={(o) => !o && close()}
        title={showTake ? "New recording" : `Recording · ${clock(r.elapsed)}`}
        testId="header-recording-sheet"
      >
        {showTake ? (
          <NewTakePanel baseUrl={baseUrl} heading={false} onDone={close} className="border-0 bg-transparent p-0 pb-1" />
        ) : (
          <div className="space-y-3 pb-1">
            <p className="text-[15px] text-muted-foreground">
              The piano is being recorded · {r.notes} note{r.notes === 1 ? "" : "s"} so far.
            </p>
            {r.err && <p className="text-[15px] text-rose-800">{r.err}</p>}
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                onClick={async () => {
                  const t = await r.stop();
                  if (t) setStopped(true);
                  else close();
                }}
                disabled={r.busy === "record"}
                className="h-11 rounded-xl bg-rose-700 px-5 text-base text-white hover:bg-rose-800"
                data-testid="header-recording-stop"
              >
                {r.busy === "record" ? <Spinner /> : <Square className="h-4 w-4 fill-current" aria-hidden />} Stop
              </Button>
              <Button type="button" variant="outline" onClick={close} className="h-11 rounded-xl bg-white px-5 text-base">
                Keep recording
              </Button>
            </div>
          </div>
        )}
      </ActionSheet>
    </>
  );
}

function HeaderBody({
  title,
  current,
  onMenu,
  toggles,
  onDetails,
  line,
  status,
  pc,
  pills,
  actions,
  back,
  places = true,
  compact = false,
  children,
  className,
}: Omit<PageHeaderProps, "toggles"> & { toggles: TopToggles | null }) {
  const glance = useRoomGlance();
  // the piano's recorder: the same store (one poll per address) as the dock and the Piano fold
  const recBase = useMemo(() => resolveRoomUrl(window.location.search), []);
  const rec = useRecorder(recBase);
  const [recOpen, setRecOpen] = useState(false);
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const [pcOpen, setPcOpen] = useState(false);
  const details = onDetails ?? (() => navigate("/room#listening"));
  const pcBusy = !!pc?.busy;
  const statusShown = !!status && status.tone !== "ok";

  const visiblePlaces = places ? PLACES.filter((p) => !p.lan || glance.onLan || p.page === current) : [];
  // the tabs are on the places themselves, where this page's highlighted tab is the title (no second "Room" beside
  // the Room tab); a sub-page has its back link and title instead ("‹ Room" beside a Room tab said it twice), and the
  // menu has every place
  const isPlace = visiblePlaces.length > 1 && visiblePlaces.some((p) => p.page === current);
  const showPlaces = isPlace;
  // a place's back link would name another tab ("‹ Room" beside the Room tab): that tab takes the back link's address
  const backPlace = isPlace && back ? visiblePlaces.find((p) => p.page !== current && placeName(back.to) === p.label)?.page : undefined;
  const showBack = !!back && !isPlace;
  // the page's line already names a live mode ("Party · live on all 3 screens"): no "Live · Party" pill beside it
  const liveShown = !!glance.liveMode && !line;

  const statePills = (
    <>
      {liveShown && glance.liveMode && <LivePill mode={glance.liveMode} current={current} />}
      <RecordingPill r={rec} onOpen={() => setRecOpen(true)} />
      {pcBusy && (
        <button
          type="button"
          data-testid="pc-busy-badge"
          onClick={() => setPcOpen((o) => !o)}
          aria-expanded={pcOpen}
          title={pc?.sentence}
          className="group grid h-11 shrink-0 place-items-center rounded-full px-0.5 focus-visible:outline-none"
        >
          <span className={cn(PILL, TONE_PILL.warn)}>
            <span aria-hidden className="h-2 w-2 rounded-full bg-amber-500" />
            PC busy
          </span>
        </button>
      )}
      {pills}
    </>
  );
  const anyPill = liveShown || rec.recording || pcBusy || !!pills;

  // Row 2's words: the page's line, then what the switches add beyond on/off ("mic in use for Auto"); a tap opens
  // the Listening details. A failed or retrying switch tap's words take the switches' part.
  const toggleWords = toggles && !toggles.note ? topToggleWords(toggles) : null;
  const words = [line, toggleWords].filter(Boolean).join(" · ");
  const hasLine = !!words || !!toggles?.note;

  const placesNav = showPlaces && (
    <nav aria-label="Places" data-testid="places" className="-ml-1 flex min-w-0 shrink items-center overflow-x-auto text-sm [scrollbar-width:none] sm:gap-0.5 sm:text-[15px]">
      {visiblePlaces.map((p) => {
        const here = p.page === current;
        const href = p.page === backPlace && back ? back.to : p.href();
        return (
          <AppLink
            key={p.page}
            href={href}
            aria-current={here ? "page" : undefined}
            data-testid={`place-${p.page}`}
            onClick={(e) => {
              // the page itself: back to its top (and out of a sub-view like a score)
              if (here && pathname === href.split(/[?#]/)[0] && !window.location.search) {
                e.preventDefault();
                window.scrollTo({ top: 0, behavior: "smooth" });
              }
            }}
            className={cn(
              "inline-flex h-11 shrink-0 items-center rounded-full focus-visible:outline-none sm:px-0.5 [&:focus-visible>span]:ring-2 [&:focus-visible>span]:ring-ring",
            )}
          >
            <span
              className={cn(
                "inline-flex h-9 items-center rounded-full px-2 font-medium sm:px-3",
                here ? "bg-teal-700 font-semibold text-white" : "text-stone-700 hover:bg-stone-200/60",
              )}
            >
              {p.label}
            </span>
          </AppLink>
        );
      })}
    </nav>
  );

  const switches = toggles && <TopToggleButtons t={toggles} onDetails={details} />;

  return (
    <header className={cn("pt-2", className)} data-testid="page-header">
      {/* Row 1: a place: its tabs; a sub-page: back and title. The pills and switches sit here from 640 px (on a phone
          they end row 2, so this row never wraps). */}
      <div className="flex min-w-0 items-center gap-x-1">
        {showBack && back && <BackLink to={back.to} label={back.label} className="max-sm:pr-1" />}
        <h1
          className={cn(
            "min-w-0 truncate font-semibold leading-tight tracking-tight",
            isPlace ? "sr-only" : compact || showBack ? "text-[22px]" : "text-[28px]",
            !isPlace && "mr-2",
          )}
        >
          {title}
        </h1>
        {showPlaces && <div className="min-w-0">{placesNav}</div>}
        <span aria-hidden className="flex-1" />
        {anyPill && <span className="flex shrink-0 items-center max-sm:hidden">{statePills}</span>}
        {switches && <span className="shrink-0 max-sm:hidden">{switches}</span>}
        {statusShown && status && (
          <button
            type="button"
            onClick={onMenu}
            aria-label={`${status.word}${status.sentence ? `: ${status.sentence}` : ""} Open the menu.`}
            title={status.sentence}
            data-testid="conn-pill"
            className="group grid h-11 shrink-0 place-items-center rounded-full px-0.5 focus-visible:outline-none"
          >
            <span className={cn(PILL, TONE_PILL[status.tone])}>
              <Dot tone={status.tone} pulse={status.pulse} className="h-2.5 w-2.5" />
              <span data-testid="conn-state" className="max-sm:sr-only">
                {status.word}
              </span>
            </span>
          </button>
        )}
        {actions}
        <Button
          variant="ghost"
          size="icon"
          onClick={onMenu}
          aria-label="Menu"
          data-testid="menu-button"
          className="-mr-2 h-11 w-11 shrink-0 rounded-full hover:bg-stone-200/60 [&_svg]:size-6"
        >
          <Menu />
        </Button>
      </div>

      {/* Row 2: one status line (tap = Listening details); on a phone the pills and switches end it */}
      {(hasLine || anyPill || switches) && (
        <div className="flex min-w-0 items-center gap-x-1">
          {toggles?.note ? (
            <NoteLine note={toggles.note} className="min-w-0 flex-1 py-1 text-sm" />
          ) : words && toggles ? (
            <button
              type="button"
              onClick={details}
              data-testid="header-line"
              title="Listening details"
              className="group flex min-h-11 min-w-0 flex-1 items-center gap-0.5 rounded-lg text-left text-[15px] leading-snug text-stone-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring max-sm:text-sm"
            >
              <span data-testid="room-line" role="status" className="min-w-0 group-hover:underline max-sm:line-clamp-2 sm:truncate">
                {words}
              </span>
              <ChevronRight aria-hidden className="h-4 w-4 shrink-0 text-teal-800" />
              <span className="sr-only">Listening details</span>
            </button>
          ) : words ? (
            <p data-testid="room-line" role="status" className="min-w-0 flex-1 py-1 text-[15px] leading-snug text-stone-700 max-sm:text-sm sm:truncate">
              {words}
            </p>
          ) : (
            <span className="flex-1" />
          )}
          {anyPill && <span className="flex shrink-0 items-center sm:hidden">{statePills}</span>}
          {switches && <span className="shrink-0 sm:hidden">{switches}</span>}
        </div>
      )}

      {pcBusy && pcOpen && (
        <p data-testid="pc-busy-sentence" className="pb-1 text-[15px] leading-snug text-amber-900">
          {pc?.sentence}
        </p>
      )}

      {children}

      <RecordingSheet r={rec} baseUrl={recBase} open={recOpen} onClose={() => setRecOpen(false)} />
    </header>
  );
}
