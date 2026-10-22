// The Room page's Music player card (in the place of the old Test music): what the music player is playing and
// what's next, Stop · Next (Play when it's stopped with something queued), "Playlist ▾" (a sheet of the saved
// playlists, tap one to play it, and Open Music), and Open Music (/music: the library, playlists, uploads and the test
// tracks). Nothing queued: "Nothing playing", Choose a playlist, Open Music. Polls show_service's player through
// room_service's /show pass-through (src/pages/Music/usePlayer.ts: 1 s while it prepares or plays, 5 s otherwise,
// only while the page is visible). An older show_service without the player gets one line, and nothing else.

import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ChevronDown, ListMusic, Music, Play, SkipForward, Square } from "lucide-react";
import { cn } from "@/lib/utils";
import { MusicApi, OLD_SHOW_WORDS, playerWords, plural, totalWords } from "@/pages/Music/api";
import { usePlaylists } from "@/pages/Music/context";
import { PLAYER_BUSY, usePlayer } from "@/pages/Music/usePlayer";
import type { RoomApi } from "./api";
import { Pill, Spinner } from "./bits";
import { ActionSheet } from "./overlays";
import { CARD_CLASS, TAP_BUTTON_CLASS } from "./theme";
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
    <Link to="/music" className={cn(TAP_BUTTON_CLASS, SECONDARY, "inline-flex items-center gap-2 px-4")} data-testid="open-music">
      <Music className="h-4 w-4" aria-hidden /> Open Music
    </Link>
  );

  return (
    <section aria-labelledby="room-music-player" data-testid="now-playing-row">
      <div className="mb-2 flex items-center justify-between gap-2">
        <h2 id="room-music-player" className="text-lg font-semibold text-stone-800">
          Music player
        </h2>
        {v && busy && (
          <Pill tone="live" pulse={v.state === "playing"}>
            {v.state === "preparing" ? "Getting ready" : "Playing"}
          </Pill>
        )}
      </div>
      <div className={cn(CARD_CLASS, "space-y-3 px-3 py-3 sm:px-4")}>
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
            <div className="min-w-0">
              {cur && busy ? (
                <>
                  <p className="text-base leading-snug">
                    <span className="text-muted-foreground">Now playing </span>
                    <span className="font-semibold">{cur.title}</span>
                    {cur.composer && <span className="text-muted-foreground"> · {cur.composer}</span>}
                  </p>
                  {v.state === "preparing" && (
                    <p className="flex items-center gap-2 text-[15px] text-muted-foreground">
                      <Spinner /> {playerWords(v)}
                    </p>
                  )}
                  {v.next && (
                    <p className="truncate text-[15px]">
                      <span className="text-muted-foreground">Next </span>
                      <span className="font-medium">{v.next.title}</span>
                    </p>
                  )}
                </>
              ) : (
                <>
                  <p className="text-base text-muted-foreground">Nothing playing</p>
                  {(v.state === "stepped_aside" || v.state === "error") && (
                    <p className={cn("text-[15px]", v.state === "error" ? "text-amber-800" : "text-muted-foreground")}>{playerWords(v)}</p>
                  )}
                  {cur && queued && (
                    <p className="truncate text-[15px]">
                      <span className="text-muted-foreground">Play starts </span>
                      <span className="font-medium">{cur.title}</span>
                    </p>
                  )}
                </>
              )}
            </div>

            <div className="flex flex-wrap gap-2">
              {busy ? (
                <>
                  <button
                    type="button"
                    onClick={() => void act("stop", { action: "stop" }, { state: "stopped", words: "Stopped", progress: null })}
                    className={cn(TAP_BUTTON_CLASS, SECONDARY, "inline-flex items-center gap-2 font-semibold")}
                  >
                    {player.busy === "stop" ? <Spinner /> : <Square className="h-4 w-4" aria-hidden />} Stop
                  </button>
                  <button type="button" onClick={() => void act("next", { action: "next" })} className={cn(TAP_BUTTON_CLASS, SECONDARY, "inline-flex items-center gap-2")}>
                    {player.busy === "next" ? <Spinner /> : <SkipForward className="h-4 w-4" aria-hidden />} Next
                  </button>
                </>
              ) : (
                queued && (
                  <button
                    type="button"
                    onClick={() => void act("play", { action: "play" }, { state: "preparing", words: "Getting ready…", progress: null, error: null })}
                    className={cn(TAP_BUTTON_CLASS, PRIMARY, "inline-flex items-center gap-2 font-semibold")}
                  >
                    {player.busy === "play" ? <Spinner /> : <Play className="h-4 w-4" aria-hidden />} Play
                  </button>
                )
              )}
              <button
                type="button"
                onClick={() => setSheet(true)}
                className={cn(TAP_BUTTON_CLASS, !busy && !queued ? PRIMARY : SECONDARY, "inline-flex items-center gap-2 px-4", !busy && !queued && "font-semibold")}
              >
                <ListMusic className="h-4 w-4" aria-hidden /> {busy || queued ? "Playlist" : "Choose a playlist"}
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
