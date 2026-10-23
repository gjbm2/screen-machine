// /music: the piano's music (owner, 22 Oct): find a piece in the library by title, composer, genre or pin, play it
// now or queue it, keep playlists, and add your own MIDI files (uploads, recordings, finds from the web). Pieces play
// once and then the next in the queue: no endless repeat unless Repeat says so. At the top, Now playing (what's on,
// Back · Play/Stop · Next, Repeat, Shuffle, the sound and where it plays); under it the tabs Browse · Playlists ·
// Mine · From the web; at the bottom, folded away, the test tracks and patterns (the Room page's old Test music).
//
// Everything is show_service's (library, playlists and the player), through room_service's /show pass-through, at the
// Room page's address (?room=, the remembered one, same-origin /rs over HTTPS, else this host's :8700). No auth. The
// player is polled every 1 s while it prepares or plays and every 5 s otherwise, only while the page is visible. An
// older show_service without these routes gets one line: "Music needs the newer show service". Same light, warm skin
// and menu as /room.
//
// ?score=<piece id> shows that piece's score (Score.tsx) in place of Now playing, the tabs and the test tracks (they
// stay as they were underneath, and the scroll position comes back with them). Any page can link to /music?score=<id>.

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { ArrowLeft, Menu } from "lucide-react";
import { AppMenu } from "@/components/nav/AppMenu";
import { useRoomLink } from "@/components/room/roomLink";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { loadToken, resolveRoomUrl, RoomApi, storageGet, storageSet } from "@/pages/Room/api";
import { Dot, Unroll } from "@/pages/Room/bits";
import { TestShowSection } from "@/pages/Room/TestShow";
import { ListenHere } from "@/pages/Room/ListenHere";
import { RoomThemeStyle, THEME_CLASS } from "@/pages/Room/theme";
import type { Tone } from "@/pages/Room/tones";
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
const TABS: [MusicTab, string][] = [
  ["browse", "Browse"],
  ["playlists", "Playlists"],
  ["mine", "Mine"],
  ["web", "From the web"],
];

function storedTab(): MusicTab {
  const t = storageGet(TAB_KEY);
  return TABS.some(([id]) => id === t) ? (t as MusicTab) : "browse";
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

const PILL =
  "inline-flex h-9 items-center gap-2 rounded-full border border-stone-200/80 bg-card px-3 text-[15px] font-medium text-stone-700 shadow-sm";

const noop = () => undefined;

/** The Room page's Test music, unchanged (mounted only while unfolded, so it costs nothing until opened). */
function TestTracks({ api }: { api: RoomApi }) {
  const room = useRoomLink();
  const liveNow = Object.values(room.status?.screens ?? {}).some((sc) => typeof sc.desired === "object");
  return <TestShowSection api={api} connected liveNow={liveNow} addLog={noop} reportError={noop} />;
}

export default function MusicPage() {
  usePageTitle("Music · screen/machine");
  const baseUrl = useMemo(() => resolveRoomUrl(window.location.search), []);
  const api = useMemo(() => new MusicApi(baseUrl), [baseUrl]);
  const roomApi = useMemo(() => new RoomApi(baseUrl, loadToken(baseUrl).token), [baseUrl]);
  const player = usePlayer(api);
  const old = player.link === "old";
  const playlists = usePlaylists(api, !old);
  const [msg, flash] = useFlash();
  const [pins, setPins] = useState<Record<string, boolean>>({});
  const [libraryRev, setLibraryRev] = useState(0);
  const [tab, setTabState] = useState<MusicTab>(storedTab);
  const [visited, setVisited] = useState<MusicTab[]>(() => [storedTab()]);
  const [openPl, setOpenPl] = useState<string | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [params, setParams] = useSearchParams();
  const scoreId = params.get(SCORE_PARAM);
  const location = useLocation();
  const navigate = useNavigate();

  // Opened here (useOpenScore marks the entry): Back is the browser's Back; a link straight to a score drops the param.
  const closeScore = useCallback(() => {
    if ((location.state as { score?: boolean } | null)?.score) navigate(-1);
    else
      setParams((p) => {
        const next = new URLSearchParams(p);
        next.delete(SCORE_PARAM);
        return next;
      });
  }, [location.state, navigate, setParams]);

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

  const setTab = useCallback((t: MusicTab) => {
    setTabState(t);
    setVisited((v) => (v.includes(t) ? v : [...v, t]));
    storageSet(TAB_KEY, t);
  }, []);

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
        setOpenPl(id);
        setTab("playlists");
      },
    }),
    [api, player, playlists, flash, pins, libraryRev, setTab],
  );

  const link: { tone: Tone; word: string } =
    player.link === "ok"
      ? { tone: "ok", word: "Connected" }
      : player.link === "old"
        ? { tone: "warn", word: "Needs updating" }
        : player.link === "unreached"
          ? player.view
            ? { tone: "warn", word: "Reconnecting" }
            : { tone: "bad", word: "Offline" }
          : { tone: "muted", word: "Connecting" };

  return (
    <div className={cn(THEME_CLASS, "min-h-screen bg-background text-base text-foreground antialiased")}>
      <RoomThemeStyle />
      <MusicProvider value={ctx}>
        {/* a score is read wider (its pages are laid out for the width they get) */}
        <div className={cn("mx-auto w-full px-4 pb-24 sm:px-6", scoreId ? "max-w-6xl" : "max-w-3xl")}>
          <header className="pb-1 pt-3">
            <div className="flex items-center gap-1">
              <h1 className="mr-auto text-[28px] font-semibold leading-tight tracking-tight">Music</h1>
              <span className={PILL} data-testid="music-link">
                <Dot tone={link.tone} pulse={link.tone === "warn"} className="h-2.5 w-2.5" />
                {link.word}
              </span>
              <Button
                variant="ghost"
                size="icon"
                onClick={() => setMenuOpen(true)}
                aria-label="Menu"
                data-testid="menu-button"
                className="-mr-2 h-11 w-11 rounded-full hover:bg-stone-200/60 [&_svg]:size-6"
              >
                <Menu />
              </Button>
            </div>
            <nav aria-label="Back" className="flex flex-wrap items-center gap-x-1 text-[15px]">
              <Link to="/room" className="group grid h-11 place-items-center rounded-full px-0.5 focus-visible:outline-none">
                <span className={cn(PILL, "group-hover:bg-stone-50 group-focus-visible:ring-2 group-focus-visible:ring-ring")}>
                  <ArrowLeft className="h-4 w-4 text-teal-700" aria-hidden /> Room
                </span>
              </Link>
            </nav>
          </header>

          <main className="space-y-5 pt-2">
            {scoreId && <ScoreView key={scoreId} pieceId={scoreId} onBack={closeScore} />}

            <div hidden={!!scoreId} className="space-y-5">
              <NowPlaying />

              {!old && (
                <>
                  <div
                    role="tablist"
                    aria-label="Music"
                    className="sticky top-0 z-20 grid grid-cols-4 rounded-full border border-stone-200 bg-card p-1 shadow-sm"
                    data-testid="music-tabs"
                  >
                    {TABS.map(([t, words]) => (
                      <button
                        key={t}
                        type="button"
                        role="tab"
                        aria-selected={tab === t}
                        onClick={() => {
                          if (t === "playlists" && tab === "playlists") setOpenPl(null);
                          setTab(t);
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
                      <Playlists openId={openPl} onOpen={setOpenPl} />
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

              <Unroll
                title="Test tracks and patterns"
                hint="recorded test music, the test piano"
                className="border-t border-stone-200/80 pt-3"
                testId="music-test-tracks"
              >
                <TestTracks api={roomApi} />
              </Unroll>
            </div>
          </main>
        </div>

        {msg && (
          <div
            role="status"
            className="fixed inset-x-0 bottom-[max(1rem,env(safe-area-inset-bottom))] z-50 mx-auto w-fit max-w-[calc(100vw-2rem)] rounded-full bg-stone-900/90 px-4 py-2.5 text-[15px] font-medium text-white shadow-lg"
          >
            {msg}
          </div>
        )}
      </MusicProvider>

      <ListenHere api={roomApi} onScore={!!scoreId} />

      <AppMenu open={menuOpen} onOpenChange={setMenuOpen} current="music" />
    </div>
  );
}
