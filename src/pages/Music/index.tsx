// /music: the piano's music (owner, 22 Oct): find a piece in the library by title, composer, genre or pin, play it
// now or queue it, keep playlists, and add your own MIDI files (uploads, recordings, finds from the web). Pieces play
// once and then the next in the queue: no endless repeat unless Repeat says so.
//   - The app's header (PageHeader): Music, the state pills, Mic and AI ("Listening details ›" goes to the Room page's
//     Listening), the link's state when it isn't fine, the menu, and the places row (Room · Music · Lights · Generator).
//   - Now playing (NowPlaying.tsx: compact; one heading row while the docked player shows), with ● Record.
//   - The tabs Browse · Playlists · Mine · Web. ?tab=browse|playlists|mine|web opens one (ahead of the one this device
//     remembers, music.tab); changing tab keeps the address in step. Opening a playlist adds an entry
//     (?tab=playlists&pl=<id>), so Back (and "All playlists") comes back to the list. When something outside the tab
//     bar changes the tab (Find a piece, a playlist pill), the tabs scroll into view.
//   - The menu's Music section: whether the music side answers, and Test music (recorded tracks and the test piano)
//     in a sheet.
//
// Everything is show_service's (library, playlists and the player), through room_service's /show pass-through, at the
// Room page's address (?room=, the remembered one, same-origin /rs over HTTPS, else this host's :8700). No auth. The
// player is polled every 1 s while it prepares or plays and every 5 s otherwise, only while the page is visible. An
// older show_service without these routes gets one line: "Music needs the newer show service". Same light, warm skin
// and menu as /room.
//
// ?score=<piece id> shows that piece's score (Score.tsx, with its own slim header) in place of the header, Now playing
// and the tabs (they stay as they were underneath, and the scroll position comes back with them). Any page can link to
// /music?score=<id>; closing it is Back when there is an entry to go back to, else it drops ?score.

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { ChevronRight, FlaskConical } from "lucide-react";
import { AppMenu } from "@/components/nav/AppMenu";
import { PageHeader, type HeaderStatus } from "@/components/nav/PageHeader";
import { useAppPlayer } from "@/components/player/appPlayer";
import { useRoomLink } from "@/components/room/roomLink";
import { cn } from "@/lib/utils";
import { loadToken, resolveRoomUrl, RoomApi, storageGet, storageSet } from "@/pages/Room/api";
import { Dot } from "@/pages/Room/bits";
import { ActionSheet } from "@/pages/Room/overlays";
import { TestShowSection } from "@/pages/Room/TestShow";
import { ListenHere } from "@/pages/Room/ListenHere";
import { useShowFeed } from "@/pages/Room/MusicCard";
import { useTopToggles } from "@/pages/Room/TopToggles";
import { RoomThemeStyle, THEME_CLASS } from "@/pages/Room/theme";
import { MusicApi } from "./api";
import { SCORE_PARAM } from "./bits";
import { Browse } from "./Browse";
import { MusicProvider, useFlash, usePlaylists, type MusicCtx, type MusicTab } from "./context";
import { FromWeb } from "./FromWeb";
import { Mine } from "./Mine";
import { NowPlaying } from "./NowPlaying";
import { Playlists } from "./Playlists";
import { ScoreView } from "./Score";
import { usePlayer } from "./usePlayer";

const TAB_KEY = "music.tab";
const TAB_PARAM = "tab";
const PLAYLIST_PARAM = "pl";
const TABS: [MusicTab, string][] = [
  ["browse", "Browse"],
  ["playlists", "Playlists"],
  ["mine", "Mine"],
  ["web", "Web"],
];

function asTab(t: string | null): MusicTab | null {
  return TABS.some(([id]) => id === t) ? (t as MusicTab) : null;
}

function storedTab(): MusicTab {
  return asTab(storageGet(TAB_KEY)) ?? "browse";
}

/** Scroll so the anchor (just above the sticky tab bar) sits under the top bars, unless it is in view already. */
function scrollToTabs(anchor: HTMLElement | null, bar: HTMLElement | null) {
  if (!anchor) return;
  const top = bar ? parseFloat(getComputedStyle(bar).top) || 0 : 0;
  const y = anchor.getBoundingClientRect().top;
  if (y >= top && y < window.innerHeight * 0.6) return;
  window.scrollTo({ top: Math.max(0, y + window.scrollY - top - 8), behavior: "smooth" });
}

/** The tab title while on this page, put back on the way out. */
function usePageTitle(title: string) {
  useEffect(() => {
    const prev = document.title;
    document.title = title;
    return () => {
      document.title = prev;
    };
  }, [title]);
}

const noop = () => undefined;

/** The Room page's Test music (mounted only while its sheet is open, so it costs nothing until then). */
function TestTracks({ api }: { api: RoomApi }) {
  const room = useRoomLink();
  const liveNow = Object.values(room.status?.screens ?? {}).some((sc) => typeof sc.desired === "object");
  return <TestShowSection api={api} connected liveNow={liveNow} addLog={noop} reportError={noop} bare />;
}

export default function MusicPage() {
  usePageTitle("Music · screen/machine");
  const baseUrl = useMemo(() => resolveRoomUrl(window.location.search), []);
  const api = useMemo(() => new MusicApi(baseUrl), [baseUrl]);
  const roomApi = useMemo(() => new RoomApi(baseUrl, loadToken(baseUrl).token), [baseUrl]);
  // the app's player poll (shared with the docked player) when it's for this address, else the page's own
  const shared = useAppPlayer(baseUrl);
  const own = usePlayer(api, !shared);
  const player = shared ?? own;
  const old = player.link === "old";
  const playlists = usePlaylists(api, !old);
  const [msg, flash] = useFlash();
  const [pins, setPins] = useState<Record<string, boolean>>({});
  const [libraryRev, setLibraryRev] = useState(0);
  const [params, setParams] = useSearchParams();
  const scoreId = params.get(SCORE_PARAM);
  const location = useLocation();
  const navigate = useNavigate();
  // the tab: the address's (?tab=), else the one this device remembers
  const [storedTabState, setTabState] = useState<MusicTab>(() => asTab(params.get(TAB_PARAM)) ?? storedTab());
  const tab = asTab(params.get(TAB_PARAM)) ?? storedTabState;
  const [visited, setVisited] = useState<MusicTab[]>(() => [tab]);
  const openPl = tab === "playlists" ? params.get(PLAYLIST_PARAM) : null;
  const [menuOpen, setMenuOpen] = useState(false);
  const [testOpen, setTestOpen] = useState(false);
  // The top bar's Mic and AI switches, as on /room (owner, 24 Oct); their details are in the Room page's Listening.
  const showFeed = useShowFeed(roomApi, true, noop);
  const toggles = useTopToggles(roomApi, showFeed, noop);
  const tabsAnchor = useRef<HTMLDivElement>(null);
  const tabBar = useRef<HTMLDivElement>(null);

  // the tab in view is remembered on this device and its panel kept (search, scroll) once visited
  useEffect(() => {
    storageSet(TAB_KEY, tab);
    setVisited((v) => (v.includes(tab) ? v : [...v, tab]));
  }, [tab]);

  // Back is the browser's Back whenever there is an entry to go back to; a score opened straight drops the param.
  const closeScore = useCallback(() => {
    if (location.key !== "default") navigate(-1);
    else
      setParams((p) => {
        const next = new URLSearchParams(p);
        next.delete(SCORE_PARAM);
        return next;
      });
  }, [location.key, navigate, setParams]);

  // The page's scroll position is kept while a score is open and put back when it closes.
  const lastY = useRef(0);
  const inScore = useRef(scoreId);
  useEffect(() => {
    const onScroll = () => {
      if (!inScore.current) lastY.current = window.scrollY;
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);
  useLayoutEffect(() => {
    const was = inScore.current;
    inScore.current = scoreId;
    if (scoreId && !was) window.scrollTo(0, 0);
    else if (!scoreId && was) window.scrollTo(0, lastY.current);
  }, [scoreId]);

  const toListening = useCallback(() => navigate("/room#listening"), [navigate]);

  // a tab change keeps the address in step (no new entry); an open playlist stays open behind its tab
  const selectTab = useCallback(
    (t: MusicTab) => {
      setTabState(t);
      setParams(
        (p) => {
          const next = new URLSearchParams(p);
          next.set(TAB_PARAM, t);
          return next;
        },
        { replace: true, state: location.state },
      );
    },
    [location.state, setParams],
  );
  const bringTabsIntoView = useCallback(() => {
    requestAnimationFrame(() => scrollToTabs(tabsAnchor.current, tabBar.current));
  }, []);
  // from outside the tab bar (Find a piece, a sheet): the tabs come into view too
  const setTab = useCallback(
    (t: MusicTab) => {
      selectTab(t);
      bringTabsIntoView();
    },
    [selectTab, bringTabsIntoView],
  );
  // Opening a playlist is an entry of its own, so Back (and "All playlists") comes back to the list.
  const onOpenPlaylist = useCallback(
    (id: string | null) => {
      if (id) {
        setTabState("playlists");
        setParams(
          (p) => {
            const next = new URLSearchParams(p);
            next.set(TAB_PARAM, "playlists");
            next.set(PLAYLIST_PARAM, id);
            return next;
          },
          { state: { pl: true } },
        );
        return;
      }
      if ((location.state as { pl?: boolean } | null)?.pl && location.key !== "default") navigate(-1);
      else
        setParams(
          (p) => {
            const next = new URLSearchParams(p);
            next.delete(PLAYLIST_PARAM);
            return next;
          },
          { replace: true },
        );
    },
    [location.key, location.state, navigate, setParams],
  );

  const ctx = useMemo<MusicCtx>(
    () => ({
      api,
      player,
      playlists,
      flash,
      pinnedOf: (id, fallback) => (id in pins ? pins[id] : fallback),
      setPinned: (id, on) => setPins((p) => ({ ...p, [id]: on })),
      libraryRev,
      bumpLibrary: () => setLibraryRev((n) => n + 1),
      setTab,
      openPlaylist: (id) => {
        if (id !== openPl) onOpenPlaylist(id);
        bringTabsIntoView();
      },
    }),
    [api, player, playlists, flash, pins, libraryRev, setTab, openPl, onOpenPlaylist, bringTabsIntoView],
  );

  const link: HeaderStatus =
    player.link === "ok"
      ? { tone: "ok", word: "Connected", sentence: "The music side is answering." }
      : player.link === "old"
        ? { tone: "warn", word: "Needs updating", sentence: "Music needs the newer show service." }
        : player.link === "unreached"
          ? player.view
            ? { tone: "warn", word: "Reconnecting", sentence: "The music side isn't answering just now. The page keeps trying.", pulse: true }
            : { tone: "bad", word: "Offline", sentence: "The music side isn't answering. The page keeps trying." }
          : { tone: "muted", word: "Connecting", sentence: "Finding the music side…" };

  return (
    <div className={cn(THEME_CLASS, "min-h-screen bg-background text-base text-foreground antialiased")}>
      <RoomThemeStyle />
      <MusicProvider value={ctx}>
        {/* a score is read wider (its pages are laid out for the width they get) */}
        <div className={cn("mx-auto w-full px-4 pb-24 sm:px-6", scoreId ? "max-w-6xl" : "max-w-3xl")}>
          {/* a score draws its own slim header */}
          {!scoreId && (
            <PageHeader
              title="Music"
              current="music"
              onMenu={() => setMenuOpen(true)}
              toggles={toggles}
              onDetails={toListening}
              status={link}
            />
          )}

          <main className="space-y-5 pt-2">
            {scoreId && <ScoreView key={scoreId} pieceId={scoreId} onBack={closeScore} />}

            <div hidden={!!scoreId} className="space-y-5">
              <NowPlaying />

              {!old && (
                <>
                  <div ref={tabsAnchor} aria-hidden className="-mb-5 h-0" />
                  <div
                    ref={tabBar}
                    role="tablist"
                    aria-label="Music"
                    className="sticky top-[var(--app-top)] z-20 grid grid-cols-4 rounded-full border border-stone-200 bg-card p-1 shadow-sm"
                    data-testid="music-tabs"
                  >
                    {TABS.map(([t, words]) => (
                      <button
                        key={t}
                        type="button"
                        role="tab"
                        aria-selected={tab === t}
                        onClick={() => {
                          if (t === "playlists" && tab === "playlists" && openPl) onOpenPlaylist(null);
                          else if (t !== tab) selectTab(t);
                        }}
                        className={cn(
                          "min-h-10 rounded-full px-1 text-[14px] font-medium leading-tight transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:text-[15px]",
                          tab === t ? "bg-teal-600 text-white" : "text-stone-700 hover:bg-stone-100",
                        )}
                      >
                        {words}
                      </button>
                    ))}
                  </div>

                  {/* A tab stays as it was (search, scroll) when you come back to it. */}
                  {visited.includes("browse") && (
                    <div role="tabpanel" hidden={tab !== "browse"}>
                      <Browse />
                    </div>
                  )}
                  {visited.includes("playlists") && (
                    <div role="tabpanel" hidden={tab !== "playlists"}>
                      <Playlists openId={openPl} onOpen={onOpenPlaylist} />
                    </div>
                  )}
                  {visited.includes("mine") && (
                    <div role="tabpanel" hidden={tab !== "mine"}>
                      <Mine />
                    </div>
                  )}
                  {visited.includes("web") && (
                    <div role="tabpanel" hidden={tab !== "web"}>
                      <FromWeb />
                    </div>
                  )}
                </>
              )}
            </div>
          </main>
        </div>

        {msg && (
          <div
            role="status"
            className="fixed inset-x-0 bottom-[max(calc(var(--dock-h,0px)_+_1rem),env(safe-area-inset-bottom))] z-50 mx-auto w-fit max-w-[calc(100vw-2rem)] rounded-full bg-stone-900/90 px-4 py-2.5 text-[15px] font-medium text-white shadow-lg"
          >
            {msg}
          </div>
        )}
      </MusicProvider>

      <ListenHere api={roomApi} onScore={!!scoreId} />

      <AppMenu open={menuOpen} onOpenChange={setMenuOpen} current="music" pageTitle="Music">
        <p className="flex items-start gap-2 px-1 text-[15px] leading-snug text-stone-700" data-testid="music-link">
          <Dot tone={link.tone} pulse={link.pulse} className="mt-1.5 h-2.5 w-2.5 shrink-0" />
          {link.sentence}
        </p>
        <button
          type="button"
          onClick={() => {
            setMenuOpen(false);
            setTestOpen(true);
          }}
          className="flex min-h-12 w-full items-center gap-3 rounded-xl px-3 py-2 text-left hover:bg-stone-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          data-testid="menu-test-music"
        >
          <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-stone-100 text-stone-600">
            <FlaskConical className="h-5 w-5" aria-hidden />
          </span>
          <span className="min-w-0 flex-1">
            <span className="block text-base font-semibold leading-snug">Test music</span>
            <span className="block text-sm leading-snug text-muted-foreground">Recorded tracks or the test piano, to try the room</span>
          </span>
          <ChevronRight className="h-4 w-4 shrink-0 text-stone-400" aria-hidden />
        </button>
      </AppMenu>

      <ActionSheet
        open={testOpen}
        onOpenChange={setTestOpen}
        title="Test music"
        description="Recorded tracks or the test piano."
        testId="music-test-sheet"
      >
        {testOpen && <TestTracks api={roomApi} />}
      </ActionSheet>
    </div>
  );
}
