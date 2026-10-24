// The Room page's mini player (owner, 24 Oct, layout A: one compact now-playing bar at the bottom): what the music
// player is playing (title, composer, and where it plays), Pause / Play, Next, Stop, Play on (Room/PlayOn.tsx, compact:
// the page's only Play on), and "Playlist ▾" (a sheet of the saved playlists, tap one to play it, and Open Music).
// Nothing queued: "Nothing playing", Choose a playlist, Open Music. Polls show_service's player through room_service's
// /show pass-through (src/pages/Music/usePlayer.ts: 1 s while it prepares or plays, 5 s otherwise, only while the
// page is visible). An older show_service without the player gets one line, and nothing else.

import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ChevronDown, ListMusic, Music, Pause, Play, SkipForward, Square } from "lucide-react";
import { cn } from "@/lib/utils";
import { MusicApi, OLD_SHOW_WORDS, playerWords, plural, totalWords } from "@/pages/Music/api";
import { usePlaylists } from "@/pages/Music/context";
import { PLAYER_BUSY, usePlayer } from "@/pages/Music/usePlayer";
import type { RoomApi } from "./api";
import { Spinner } from "./bits";
import { ActionSheet } from "./overlays";
import { PlayOn } from "./PlayOn";
import { CARD_CLASS } from "./theme";
import type { NewLogEntry } from "./useRoomConnection";

const PRIMARY = "bg-primary text-primary-foreground hover:bg-primary/90";
const SECONDARY = "border border-stone-300 bg-white hover:bg-stone-50";
const ROW = "flex min-h-12 w-full items-center gap-3 rounded-xl px-3 py-2 text-left text-base hover:bg-stone-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

export function NowPlayingRow({ api, addLog }: { api: RoomApi; addLog?: (e: NewLogEntry) => void }) {
  const music = useMemo(() => new MusicApi(api.baseUrl), [api.baseUrl]);
  const player = usePlayer(music);
  const [sheet, setSheet] = useState(false);
  const playlists = usePlaylists(music, sheet);
  const [err, setErr] = useState<string | null>(null);
  const v = player.view;

  const act = async (what: string, body: Parameters<typeof player.act>[0], guess?: Parameters<typeof player.act>[1]) => {
    setErr(null);
    const r = await player.act(body, guess);
    if (r.ok) addLog?.({ type: "fixture", level: "info", summary: `Music: ${what}` });
    else setErr(r.words);
    return r.ok;
  };
  const playList = async (id: string, name: string) => {
    if (await act(`play ${name}`, { action: "play", playlist: id, index: 0 }, { state: "preparing", words: "Getting ready…", progress: null, error: null })) setSheet(false);
  };

  const busy = !!v && PLAYER_BUSY.includes(v.state);
  const queued = (v?.queue?.count ?? 0) > 0;
  const cur = v?.current ?? null;
  const openMusic = (
    <Link to="/music" className={cn("inline-flex h-9 items-center gap-1.5 rounded-full px-3 text-[15px] font-medium", SECONDARY)} data-testid="open-music">
      <Music className="h-4 w-4" aria-hidden /> Open Music
    </Link>
  );

  const paused = v?.state === "paused";
  const shown = cur && (busy || paused) ? cur : null;
  const ROUND = "grid h-11 w-11 shrink-0 place-items-center rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
  const PLAY_GUESS = { state: "preparing", words: "Getting ready…", progress: null, error: null } as const;

  return (
    <section aria-label="Music player" data-testid="now-playing-row">
      <div className={cn(CARD_CLASS, "space-y-2 px-3 py-2.5 sm:px-4")}>
        {player.link === "old" ? (
          <p className="text-base text-muted-foreground">{OLD_SHOW_WORDS}</p>
        ) : !v ? (
          player.link === "unreached" ? (
            <p className="text-base text-amber-800">The music side isn't answering just now. The page keeps trying.</p>
          ) : (
            <p className="flex items-center gap-2 text-base text-muted-foreground">
              <Spinner /> Finding the player…
            </p>
          )
        ) : (
          <>
            <div className="flex items-center gap-2">
              {busy ? (
                <button
                  type="button"
                  aria-label="Pause"
                  title="Pause"
                  onClick={() => void act("pause", { action: "pause" })}
                  className={cn(ROUND, "bg-teal-700 text-white hover:bg-teal-800")}
                >
                  {player.busy === "pause" ? <Spinner /> : <Pause className="h-5 w-5" aria-hidden />}
                </button>
              ) : (paused || queued) ? (
                <button
                  type="button"
                  aria-label="Play"
                  title="Play"
                  onClick={() => void (paused ? act("resume", { action: "resume" }) : act("play", { action: "play" }, PLAY_GUESS))}
                  className={cn(ROUND, "bg-teal-700 text-white hover:bg-teal-800")}
                >
                  {player.busy === "play" || player.busy === "resume" ? <Spinner /> : <Play className="h-5 w-5 translate-x-px" aria-hidden />}
                </button>
              ) : (
                <span aria-hidden className={cn(ROUND, "bg-stone-100 text-stone-500")}>
                  <Music className="h-5 w-5" />
                </span>
              )}
              <div className="min-w-0 flex-1">
                {shown ? (
                  <>
                    <p className="truncate text-base font-semibold leading-snug">{shown.title}</p>
                    <p className="truncate text-sm leading-snug text-muted-foreground">
                      {v.state === "preparing"
                        ? playerWords(v)
                        : [paused ? "Paused" : null, shown.composer, v.next ? `next ${v.next.title}` : null].filter(Boolean).join(" · ") ||
                          playerWords(v)}
                    </p>
                  </>
                ) : (
                  <>
                    <p className="text-base leading-snug text-muted-foreground">Nothing playing</p>
                    {(v.state === "stepped_aside" || v.state === "error") && (
                      <p className={cn("truncate text-sm", v.state === "error" ? "text-amber-800" : "text-muted-foreground")}>{playerWords(v)}</p>
                    )}
                    {cur && queued && (
                      <p className="truncate text-sm">
                        <span className="text-muted-foreground">Play starts </span>
                        <span className="font-medium">{cur.title}</span>
                      </p>
                    )}
                  </>
                )}
              </div>
              {(busy || paused) && (
                <>
                  <button type="button" aria-label="Next" title="Next" onClick={() => void act("next", { action: "next" })} className={cn(ROUND, "text-stone-700 hover:bg-stone-100")}>
                    {player.busy === "next" ? <Spinner /> : <SkipForward className="h-5 w-5" aria-hidden />}
                  </button>
                  <button
                    type="button"
                    aria-label="Stop"
                    title="Stop"
                    onClick={() => void act("stop", { action: "stop" }, { state: "stopped", words: "Stopped", progress: null })}
                    className={cn(ROUND, "text-stone-700 hover:bg-stone-100")}
                  >
                    {player.busy === "stop" ? <Spinner /> : <Square className="h-4 w-4" aria-hidden />}
                  </button>
                </>
              )}
            </div>

            <div className="flex flex-wrap items-center gap-1.5">
              <PlayOn baseUrl={api.baseUrl} compact addLog={addLog} />
              <button
                type="button"
                onClick={() => setSheet(true)}
                className={cn(
                  "inline-flex h-9 items-center gap-1.5 rounded-full px-3 text-[15px] font-medium",
                  !busy && !queued && !paused ? PRIMARY : SECONDARY,
                )}
              >
                <ListMusic className="h-4 w-4" aria-hidden /> {busy || queued || paused ? "Playlist" : "Choose a playlist"}
                <ChevronDown className="h-4 w-4" aria-hidden />
              </button>
              {openMusic}
            </div>
          </>
        )}
        {(err || player.error) && <p className="text-[15px] text-rose-700">{err ?? player.error}</p>}
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
          <Link to="/music" className={ROW} onClick={() => setSheet(false)}>
            <Music className="h-5 w-5 shrink-0 text-stone-600" aria-hidden /> Open Music
            <span className="ml-auto text-sm text-muted-foreground">library, playlists, uploads</span>
          </Link>
          {err && <p className="px-3 pb-1 text-[15px] text-rose-700">{err}</p>}
        </div>
      </ActionSheet>
    </section>
  );
}
