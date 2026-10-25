// The mini player, one row (owner, 25 Oct: "vertical space is at a HIGH PREMIUM on a mobile"): what the music player is
// playing (title, composer), the shared Transport (components/player/Transport.tsx: ⏯ ⏭, ⏮ from 640 px up, and ⋯ More
// with Stop, Speed and Sound), then in that ⋯: Play on (Room/PlayOn.tsx), Playlist (a sheet of the saved playlists,
// tap one to play it, Open Music, and Upload or find a piece), Score when the piece has one, and Open Music. Idle it
// says "Nothing playing" (or what else is on, with its Stop), with Play when there's a queue (else "Choose a
// playlist") and a ⋯ of its own. Two places use it:
//   - MiniPlayer `docked`: the docked player (src/components/player/DockedPlayer.tsx; owner, 24 Oct: "when music is
//     playing through the app, that apparatus should anchor bottom throughout the app"), with this device's line
//     (ListenHere's ListenLine and Stop sound here) when the sound plays here too, so there's one bar, never two. On
//     the Room page it is docked at all times: idle, it says "Nothing playing" (or "Stopped · <playlist>, 3 of 18"),
//     what else is on (ListenHere's useWhatsOn: a test piece with Stop, someone at the piano, what the room hears),
//     Play when there's a queue, and Playlist;
//   - NowPlayingRow: an in-page row, for a page the dock doesn't cover (never the Room page: the dock is its player).
// Both read the app's one player poll (components/player/appPlayer.tsx: 1 s while it prepares or plays, 5 s otherwise,
// only while the page is visible); NowPlayingRow keeps its own when the page talks to another address. An older
// show_service without the player gets one line, and nothing else. Open Music never shows on /music itself.

import { useMemo, useState, type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { ChevronRight, FileMusic, ListMusic, MoreHorizontal, Music, Play, Square, Upload, X } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useAppPlayer } from "@/components/player/appPlayer";
import { useDocked } from "@/components/player/dockState";
import { Transport } from "@/components/player/Transport";
import { cn } from "@/lib/utils";
import { MusicApi, OLD_SHOW_WORDS, playerWords, plural, totalWords } from "@/pages/Music/api";
import { hasScore, SCORE_PARAM } from "@/pages/Music/bits";
import { usePlaylists } from "@/pages/Music/context";
import { PLAYER_BUSY, usePlayer, type PlayerHook } from "@/pages/Music/usePlayer";
import type { RoomApi } from "./api";
import { Spinner } from "./bits";
import { ListenLine, useWhatsOn } from "./ListenHere";
import { listenOff, setListenView, useListenHere } from "./listenStream";
import { ActionSheet } from "./overlays";
import { PlayOn } from "./PlayOn";
import { CARD_CLASS, THEME_CLASS } from "./theme";
import type { NewLogEntry } from "./useRoomConnection";

const PRIMARY = "bg-primary text-primary-foreground hover:bg-primary/90";
const SECONDARY = "border border-stone-300 bg-white hover:bg-stone-50";
const PILL = "inline-flex h-9 items-center gap-1.5 rounded-full px-3 text-[15px] font-medium";
const MORE_ROW =
  "inline-flex min-h-11 w-full items-center gap-2 rounded-xl border border-stone-300 bg-white px-3 text-left text-base font-medium hover:bg-stone-50";
const ROW = "flex min-h-12 w-full items-center gap-3 rounded-xl px-3 py-2 text-left text-base hover:bg-stone-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
const ROUND = "grid h-11 w-11 shrink-0 place-items-center rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
const PLAY_GUESS = { state: "preparing", words: "Getting ready…", progress: null, error: null } as const;

/** The page a link was followed from, by name (the score's back says "← Room", "← Music"). */
export function pageName(pathname: string): string {
  const top = pathname.split("/")[1] ?? "";
  return (
    ({ room: "Room", music: "Music", lights: "Lights", scheduler: "Schedules", settings: "Settings", "": "Generator" } as Record<string, string>)[top] ??
    "Room"
  );
}

/** The Room page's in-page row at the end of the page, while the dock isn't showing (nothing plays). */
export function NowPlayingRow({ api, addLog }: { api: RoomApi; addLog?: (e: NewLogEntry) => void }) {
  const shared = useAppPlayer(api.baseUrl);
  const music = useMemo(() => new MusicApi(api.baseUrl), [api.baseUrl]);
  const off = useDocked();
  const own = usePlayer(music, !shared && !off);
  if (off) return null;
  return <MiniPlayer player={shared ?? own} baseUrl={api.baseUrl} addLog={addLog} />;
}

export function MiniPlayer({
  player,
  baseUrl,
  addLog,
  docked,
  openMusicLink = true,
  top,
}: {
  player: PlayerHook;
  /** room_service's address (the page's API base). */
  baseUrl: string;
  addLog?: (e: NewLogEntry) => void;
  /** At the bottom of the screen: a stronger shadow, and this device's line when the sound plays here. */
  docked?: boolean;
  /** Open Music (never on /music itself, whatever this says). */
  openMusicLink?: boolean;
  /** A line above the player (the dock's "Recording 1:23 · Stop"). */
  top?: ReactNode;
}) {
  const { pathname, search } = useLocation();
  const music = useMemo(() => new MusicApi(baseUrl), [baseUrl]);
  const here = useListenHere();
  const [sheet, setSheet] = useState(false);
  const [more, setMore] = useState(false);
  const playlists = usePlaylists(music, sheet);
  const [err, setErr] = useState<string | null>(null);
  const v = player.view;
  const onMusic = pathname.startsWith("/music");
  const showOpenMusic = openMusicLink && !onMusic;
  const from = pageName(pathname);

  const busy = !!v && PLAYER_BUSY.includes(v.state);
  const paused = v?.state === "paused";
  const active = busy || paused;
  const queued = (v?.queue?.count ?? 0) > 0;
  const cur = v?.current ?? null;
  // the idle line: what else is on (only read while the dock shows the idle player)
  const whatsOn = useWhatsOn(baseUrl, !!docked && !!v && !active);

  const act = async (what: string, body: Parameters<typeof player.act>[0], guess?: Parameters<typeof player.act>[1]) => {
    setErr(null);
    const r = await player.act(body, guess);
    if (r.ok) addLog?.({ type: "fixture", level: "info", summary: `Music: ${what}` });
    else setErr(r.words);
    return r.ok;
  };
  const playList = async (id: string, name: string) => {
    if (await act(`play ${name}`, { action: "play", playlist: id, index: 0 }, PLAY_GUESS)) setSheet(false);
  };

  // Score: this piece's score on /music, remembering the page it came from
  const scoreTo = (id: string) => {
    const p = new URLSearchParams(onMusic ? search : "");
    p.set(SCORE_PARAM, id);
    return { pathname: "/music", search: `?${p.toString()}` };
  };
  const shown = cur && active ? cur : null;
  const where = v?.queue?.playlist?.name ?? v?.queue?.name ?? null;
  const pos = v && queued && v.queue.count > 1 ? `${Math.min(v.queue.index + 1, v.queue.count)} of ${v.queue.count}` : null;
  const stoppedWords = v?.state === "stopped" && where ? `Stopped · ${where}${pos ? `, ${pos}` : ""}` : null;

  // ⋯'s own rows (after the Transport's Stop, Speed and Sound): Play on, Playlist, Score, Open Music
  const moreRows = (close: () => void, playlistInRow: boolean) => (
    <>
      <PlayOn baseUrl={baseUrl} addLog={addLog} />
      <div className="space-y-1">
        {!playlistInRow && (
          <button
            type="button"
            onClick={() => {
              close();
              setSheet(true);
            }}
            className={MORE_ROW}
          >
            <ListMusic className="h-4 w-4" aria-hidden /> {active || queued ? "Playlist" : "Choose a playlist"}
          </button>
        )}
        {shown?.id && hasScore(shown) && (
          <Link
            to={scoreTo(shown.id)}
            state={{ score: true, from }}
            onClick={close}
            className={MORE_ROW}
            aria-label={`Score of ${shown.title}`}
            data-testid="dock-score"
          >
            <FileMusic className="h-4 w-4" aria-hidden /> Score
          </Link>
        )}
        {showOpenMusic && (
          <Link to="/music" onClick={close} className={MORE_ROW} data-testid="open-music">
            <Music className="h-4 w-4" aria-hidden /> Open Music
          </Link>
        )}
      </div>
    </>
  );
  const idleNoQueue = !!v && !active && !queued;

  return (
    <section aria-label="Music player" data-testid={docked ? "docked-player-card" : "now-playing-row"}>
      <div
        className={cn(
          CARD_CLASS,
          "space-y-1.5 px-3 py-1 sm:px-4",
          docked && "shadow-[0_2px_4px_rgba(68,48,28,0.08),0_12px_32px_-8px_rgba(68,48,28,0.28)]",
        )}
      >
        {top}
        {player.link === "old" ? (
          <p className="py-2 text-base text-muted-foreground">{OLD_SHOW_WORDS}</p>
        ) : !v ? (
          player.link === "unreached" ? (
            <p className="py-2 text-base text-amber-800">The music side isn't answering just now. The page keeps trying.</p>
          ) : (
            <p className="flex items-center gap-2 py-2 text-base text-muted-foreground">
              <Spinner /> Finding the player…
            </p>
          )
        ) : (
          <>
            {/* one row (owner, 25 Oct: "vertical space is at a HIGH PREMIUM on a mobile"): what plays, ⏯ ⏭, ⋯ */}
            <div className="flex min-h-11 items-center gap-2">
              <div className="min-w-0 flex-1">
                {shown ? (
                  <>
                    <p className="truncate text-base font-semibold leading-tight">{shown.title}</p>
                    <p className="truncate text-sm leading-tight text-muted-foreground">
                      {v.state === "preparing"
                        ? playerWords(v)
                        : [paused ? "Paused" : null, shown.composer, v.next ? `next ${v.next.title}` : null].filter(Boolean).join(" · ") ||
                          playerWords(v)}
                    </p>
                  </>
                ) : (
                  <>
                    <p className="truncate text-base leading-tight text-muted-foreground">{stoppedWords ?? "Nothing playing"}</p>
                    {/* idle: what else is on (a test piece, with its Stop; someone at the piano; what the room hears) */}
                    {whatsOn ? (
                      <p className="flex min-w-0 items-center gap-1.5 text-sm leading-tight text-stone-700" data-testid="dock-whats-on">
                        <span aria-hidden className="h-2 w-2 shrink-0 rounded-full bg-teal-600 motion-safe:animate-pulse" />
                        <span className="min-w-0 truncate">{whatsOn.line}</span>
                      </p>
                    ) : v.state === "stepped_aside" || v.state === "error" ? (
                      <p className={cn("truncate text-sm leading-tight", v.state === "error" ? "text-amber-800" : "text-muted-foreground")}>{playerWords(v)}</p>
                    ) : cur && queued ? (
                      <p className="truncate text-sm leading-tight">
                        <span className="text-muted-foreground">Play starts </span>
                        <span className="font-medium">{cur.title}</span>
                      </p>
                    ) : null}
                  </>
                )}
              </div>
              {!active && whatsOn?.stop && (
                <button type="button" onClick={() => void whatsOn.stop?.()} className={cn(PILL, SECONDARY, "shrink-0")}>
                  <Square className="h-3.5 w-3.5" aria-hidden /> Stop
                </button>
              )}
              {active ? (
                <Transport
                  player={player}
                  compact
                  slimPrev
                  more={(close) => moreRows(close, false)}
                  onDone={(what, problem) => (problem ? setErr(problem) : addLog?.({ type: "fixture", level: "info", summary: `Music: ${what}` }))}
                />
              ) : (
                <>
                  {queued ? (
                    <button
                      type="button"
                      aria-label="Play"
                      title="Play"
                      onClick={() => void act("play", { action: "play" }, PLAY_GUESS)}
                      className={cn(ROUND, "bg-teal-700 text-white hover:bg-teal-800")}
                    >
                      {player.busy === "play" ? <Spinner /> : <Play className="h-5 w-5 translate-x-px" aria-hidden />}
                    </button>
                  ) : (
                    <button type="button" onClick={() => setSheet(true)} className={cn(PILL, PRIMARY, "shrink-0")}>
                      <ListMusic className="h-4 w-4" aria-hidden /> <span className="sm:hidden">Playlist</span>
                      <span className="max-sm:hidden">Choose a playlist</span>
                    </button>
                  )}
                  <Popover open={more} onOpenChange={setMore}>
                    <PopoverTrigger asChild>
                      <button
                        type="button"
                        aria-label="More: play on, playlists, Music"
                        title="Play on, playlists, Music"
                        className={cn(ROUND, "text-stone-700 hover:bg-stone-100")}
                        data-testid="dock-more"
                      >
                        <MoreHorizontal className="h-5 w-5" aria-hidden />
                      </button>
                    </PopoverTrigger>
                    <PopoverContent
                      align="end"
                      collisionPadding={12}
                      className={cn(
                        THEME_CLASS,
                        "z-[70] max-h-[var(--radix-popover-content-available-height)] w-[min(22rem,calc(100vw-2rem))] space-y-3 overflow-y-auto rounded-2xl p-3",
                      )}
                    >
                      {moreRows(() => setMore(false), idleNoQueue)}
                    </PopoverContent>
                  </Popover>
                </>
              )}
            </div>

            {/* this device (was ListenHere's own bar): its line, Open the music screen, Stop sound here */}
            {docked && here.on && (
              <div className="flex items-center gap-2 border-t border-stone-200/80 pt-1.5" data-testid="docked-listen-here">
                <ListenLine className="min-w-0 flex-1" onOpen={() => setListenView(true)} />
                <button
                  type="button"
                  aria-label="Stop sound here"
                  title="Stop sound here"
                  onClick={listenOff}
                  className="grid h-11 w-11 shrink-0 place-items-center rounded-xl border border-stone-300 bg-white text-stone-700 hover:bg-stone-50"
                >
                  <X className="h-5 w-5" aria-hidden />
                </button>
              </div>
            )}
          </>
        )}
        {(err || player.error) && <p className="pb-1 text-[15px] text-rose-700">{err ?? player.error}</p>}
      </div>

      <ActionSheet open={sheet} onOpenChange={setSheet} title="Play a playlist" description="It plays from the top, one piece after another." testId="room-playlists">
        <div className="space-y-1 pb-1">
          {playlists.list === null && !playlists.error && (
            <p className="flex items-center gap-2 px-3 py-2 text-base text-muted-foreground">
              <Spinner /> Finding the playlists…
            </p>
          )}
          {playlists.error && !playlists.list && <p className="px-3 py-2 text-base text-amber-800">{playlists.error}</p>}
          {playlists.list?.length === 0 && <p className="px-3 py-2 text-base text-muted-foreground">No playlists yet. Make one on the Music page.</p>}
          {(playlists.list ?? []).map((pl) => {
            const on = busy && v?.queue?.playlist?.id === pl.id;
            return (
              <button key={pl.id} type="button" className={cn(ROW, on && "bg-teal-50 text-teal-900")} onClick={() => void playList(pl.id, pl.name)}>
                <Play className="h-5 w-5 shrink-0 text-stone-600" aria-hidden />
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium">{pl.name}</span>
                  <span className="block text-sm text-muted-foreground">
                    {on ? "Playing · " : ""}
                    {[plural(pl.count, "piece"), totalWords(pl.duration_s)].filter(Boolean).join(" · ")}
                  </span>
                </span>
              </button>
            );
          })}
          {showOpenMusic && (
            <Link to="/music" className={ROW} onClick={() => setSheet(false)}>
              <Music className="h-5 w-5 shrink-0 text-stone-600" aria-hidden /> Open Music
              <span className="ml-auto text-sm text-muted-foreground">library, playlists, uploads</span>
            </Link>
          )}
          <Link to="/music?tab=mine" className={ROW} onClick={() => setSheet(false)} data-testid="upload-or-find">
            <Upload className="h-5 w-5 shrink-0 text-stone-600" aria-hidden /> Upload or find a piece
            <ChevronRight className="ml-auto h-5 w-5 shrink-0 text-stone-400" aria-hidden />
          </Link>
          {err && <p className="px-3 pb-1 text-[15px] text-rose-700">{err}</p>}
        </div>
      </ActionSheet>
    </section>
  );
}
