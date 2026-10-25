// The Now playing card at the top of /music, one compact card (owner, 25 Oct: "vertical space is at a HIGH PREMIUM on a
// mobile ... MOST important: ipad"): the title · composer · performer (no heading: "Now playing" is read out), then
// one line of what it's doing (Getting ready…, Playing 1:23 / 4:32, Stopped, Stepped aside, or the problem) · the
// playlist it's from ("Pop on the piano 3 of 18": a tap opens it) · "Next: …" or "stops after this one"; the shared
// Transport (⏮ ⏯ ⏭ and ⋯ More: Stop, Speed, Sound, then Repeat, Shuffle and Coming up), Play on as the compact pill,
// Score (an icon) and ● Record (`record`: off on Mine, which has its own beside Upload). A thin bar shows progress.
// While the docked player shows at the foot of the screen (dockState's useDocked: music plays, prepares or is paused,
// or the piano records), the dock is the one player and says the piece: the card shrinks to one heading row, "Now
// playing · Pop on the piano 3 of 18" (the playlist opens on a tap), Score, ● Record and a ⋯ More with Repeat,
// Shuffle, Coming up and the playlist (the dock has Stop, Speed and Sound).

import { useState } from "react";
import { FileMusic, ListMusic, MoreHorizontal, Repeat, Repeat1, Search, Shuffle } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useDocked } from "@/components/player/dockState";
import { Transport } from "@/components/player/Transport";
import { cn } from "@/lib/utils";
import { Notice, Spinner } from "@/pages/Room/bits";
import { RecordPill, useRecorder } from "@/pages/Room/PianoRecorder";
import { PlayOn } from "@/pages/Room/PlayOn";
import { CARD_CLASS, THEME_CLASS } from "@/pages/Room/theme";
import { myOutput } from "@/pages/Room/listenStream";
import { usePianoSound, type PianoSound } from "@/pages/Room/usePianoSound";
import { OLD_SHOW_WORDS, percent, playerWords, type PlayerView, type Repeat as RepeatMode } from "./api";
import { hasScore, PRIMARY, SECONDARY, useOpenScore } from "./bits";

const PILL = "inline-flex h-10 shrink-0 items-center gap-1.5 rounded-full px-3.5 text-[15px] font-medium";
import { useMusic } from "./context";
import { PLAYER_BUSY } from "./usePlayer";

/** The player's sound and output as usePianoSound's PianoSound, so the Room page's SoundChoice chips fit. */
export function usePlayerSound(view: PlayerView | null): PianoSound {
  const local = usePianoSound(view?.choices);
  const has = (list: { id: string }[], id: string | null | undefined) => !!id && list.some((x) => x.id === id);
  return {
    ...local,
    instrument: has(local.instruments, view?.instrument) ? view!.instrument : local.instrument,
    output: has(local.outputs, view?.output) || (!!view?.output && view.output === myOutput()) ? view!.output : local.output,
  };
}

const REPEATS: [RepeatMode, string][] = [
  ["off", "Off"],
  ["all", "All"],
  ["one", "One"],
];

/** More's own rows: Repeat, Shuffle, Coming up and (`playlist`) the playlist it plays from. */
function MoreRows({ v, playlist, onPlaylist }: { v: PlayerView; playlist?: string | null; onPlaylist?: () => void }) {
  const { player } = useMusic();
  const upcoming = (v.upcoming ?? []).filter(Boolean);
  const setRepeat = (r: RepeatMode) => void player.act({ action: "set", repeat: r }, { repeat: r });
  return (
    <>
      <div>
        <p className="mb-1.5 inline-flex items-center gap-1.5 text-sm font-medium text-stone-600">
          {v.repeat === "one" ? <Repeat1 className="h-4 w-4" aria-hidden /> : <Repeat className="h-4 w-4" aria-hidden />} Repeat
        </p>
        <div role="radiogroup" aria-label="Repeat" className="grid grid-cols-3 rounded-full border border-stone-200 bg-white p-0.5">
          {REPEATS.map(([r, w]) => (
            <button
              key={r}
              type="button"
              role="radio"
              aria-checked={v.repeat === r}
              onClick={() => v.repeat !== r && setRepeat(r)}
              className={cn(
                "min-h-10 rounded-full px-3 text-[15px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                v.repeat === r ? "bg-teal-600 text-white" : "text-stone-700 hover:bg-stone-100",
              )}
            >
              {w}
            </button>
          ))}
        </div>
      </div>
      <button
        type="button"
        aria-pressed={!!v.shuffle}
        onClick={() => void player.act({ action: "set", shuffle: !v.shuffle }, { shuffle: !v.shuffle })}
        className={cn(
          "inline-flex min-h-11 w-full items-center gap-2 rounded-xl border px-3 text-base font-medium transition-colors",
          v.shuffle ? "border-teal-300 bg-teal-50 text-teal-900" : "border-stone-300 bg-white text-stone-700 hover:bg-stone-50",
        )}
      >
        <Shuffle className="h-4 w-4" aria-hidden /> Shuffle {v.shuffle ? "on" : "off"}
      </button>
      {playlist && onPlaylist && (
        <button
          type="button"
          onClick={onPlaylist}
          className="inline-flex min-h-11 w-full items-center gap-2 rounded-xl border border-stone-300 bg-white px-3 text-left text-base font-medium hover:bg-stone-50"
        >
          <ListMusic className="h-4 w-4 shrink-0" aria-hidden />
          <span className="min-w-0 truncate">Open {playlist}</span>
        </button>
      )}
      {upcoming.length > 0 && (
        <div>
          <p className="mb-1 text-sm font-medium text-stone-600">Coming up</p>
          <ol className="max-h-48 space-y-1 overflow-y-auto text-[15px]">
            {upcoming.map((it, i) => (
              <li key={`${it.id}-${i}`} className="flex gap-2">
                <span className="w-5 shrink-0 text-right tabular-nums text-muted-foreground">{i + 1}</span>
                <span className="min-w-0 truncate">
                  {it.title}
                  {it.composer && <span className="text-muted-foreground"> · {it.composer}</span>}
                </span>
              </li>
            ))}
          </ol>
        </div>
      )}
    </>
  );
}

/** ● Record (the shared recorder). While it records and the dock shows, the dock carries the Stop instead. */
function Record({ baseUrl, docked }: { baseUrl: string; docked: boolean }) {
  const r = useRecorder(baseUrl);
  if (r.recording && docked) return null;
  return <RecordPill baseUrl={baseUrl} />;
}

function ScoreButton({ id }: { id: string }) {
  const openScore = useOpenScore();
  return (
    <button
      type="button"
      onClick={() => openScore(id)}
      title="The piece's score"
      aria-label="Score"
      className="grid h-11 w-11 shrink-0 place-items-center rounded-full text-stone-600 hover:bg-stone-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <FileMusic className="h-5 w-5" aria-hidden />
    </button>
  );
}

export function NowPlaying({ record = true, className }: { record?: boolean; className?: string }) {
  const { api, player, setTab, openPlaylist } = useMusic();
  const docked = useDocked();
  const v = player.view;

  if (player.link === "old") {
    return (
      <Notice tone="muted" className={cn("text-base", className)}>
        {OLD_SHOW_WORDS}
      </Notice>
    );
  }
  if (!v) {
    return (
      <div className={cn(CARD_CLASS, "px-4 py-3", className)}>
        {player.link === "unreached" ? (
          <p className="text-base text-amber-800">The music side isn't answering just now. The page keeps trying.</p>
        ) : (
          <p className="flex items-center gap-2 text-base text-muted-foreground">
            <Spinner /> Finding the player…
          </p>
        )}
      </div>
    );
  }

  const queued = (v.queue?.count ?? 0) > 0;
  const pct = v.state === "preparing" ? percent(v.progress) : null;
  const cur = v.current;
  const where = v.queue?.playlist?.name ?? v.queue?.name ?? null;
  const pos = queued && v.queue.count > 1 ? `${Math.min(v.queue.index + 1, v.queue.count)} of ${v.queue.count}` : null;
  const who = cur ? [cur.composer, cur.performer].filter(Boolean).join(" · ") : "";
  const plId = v.queue?.playlist?.id ?? null;
  const toPlaylist = plId ? () => openPlaylist(plId) : undefined;
  const scoreId = cur?.id && hasScore(cur) ? cur.id : null;

  // The dock is the player: one heading row.
  if (docked) {
    return <DockedRow v={v} where={where} pos={pos} scoreId={scoreId} onPlaylist={toPlaylist} baseUrl={api.baseUrl} className={className} />;
  }

  // the plain queue's name says nothing unless it has a place to give ("Queue 2 of 5"); a playlist always says its name
  const whereWords = where && (plId || pos) ? `${where}${pos ? ` ${pos}` : ""}` : null;
  const stops = !v.next && cur && queued && v.repeat === "off" && PLAYER_BUSY.includes(v.state);
  const dur = v.duration_s ?? cur?.duration_s;

  // One card, compact (owner, 25 Oct: "vertical space is at a HIGH PREMIUM on a mobile"): from 640 px one row
  // (⏮ ⏯ ⏭ ⋯ · the piece and what it's doing · Play on · Score · Record); on a phone, and in the side column of a
  // wide screen, two (the piece, Score and Record; then the transport and Play on).
  return (
    <section aria-labelledby="music-now" data-testid="now-playing" className={className}>
      <h2 id="music-now" className="sr-only">
        Now playing
      </h2>
      <div className={cn(CARD_CLASS, "px-3 py-2 sm:px-4")}>
        <div
          className={cn(
            "grid items-center gap-x-2",
            "grid-cols-[minmax(0,1fr)_auto] [grid-template-areas:'t_a''s_a''p_o']",
            "sm:grid-cols-[auto_minmax(0,1fr)_auto_auto] sm:[grid-template-areas:'p_t_t_t''p_s_o_a']",
            "lg:grid-cols-[minmax(0,1fr)_auto] lg:[grid-template-areas:'t_a''s_a''p_o']",
          )}
        >
          <p
            className="min-w-0 self-end truncate text-base font-semibold leading-snug [grid-area:t] lg:line-clamp-2 lg:whitespace-normal"
            data-testid="now-title"
            title={cur ? [cur.title, who].filter(Boolean).join(" · ") : undefined}
          >
            {cur ? cur.title : <span className="text-stone-700">Nothing playing</span>}
            {who && <span className="font-normal text-muted-foreground"> · {who}</span>}
          </p>
          <p
            role="status"
            className={cn(
              "flex min-w-0 items-center gap-1.5 self-start text-sm leading-snug [grid-area:s] max-sm:mb-1.5 sm:self-center lg:mb-1.5 lg:self-start",
              v.state === "error" ? "text-amber-800" : v.state === "playing" ? "text-teal-800" : "text-muted-foreground",
            )}
          >
            {v.state === "preparing" && <Spinner className="h-3.5 w-3.5 shrink-0" />}
            {v.state === "playing" && <span aria-hidden className="h-2 w-2 shrink-0 rounded-full bg-teal-600 motion-safe:animate-pulse" />}
            <span className="shrink-0">{queued || cur ? playerWords(v) : "Pick a piece below, or a playlist"}</span>
            {whereWords &&
              (toPlaylist ? (
                <button
                  type="button"
                  onClick={toPlaylist}
                  title="Open the playlist"
                  className="min-w-0 truncate font-medium text-teal-800 underline-offset-2 hover:underline"
                >
                  · {whereWords}
                </button>
              ) : (
                <span className="min-w-0 truncate text-muted-foreground">· {whereWords}</span>
              ))}
            {v.next ? (
              <span className="min-w-0 truncate text-muted-foreground" title={`Next: ${v.next.title}${v.next.composer ? ` · ${v.next.composer}` : ""}`}>
                · Next: <span className="font-medium text-stone-700">{v.next.title}</span>
                {v.next.composer && ` · ${v.next.composer}`}
              </span>
            ) : stops ? (
              <span className="min-w-0 truncate text-muted-foreground">· stops after this one</span>
            ) : null}
          </p>
          <div className="flex items-center gap-1 [grid-area:a]">
            {scoreId && <ScoreButton id={scoreId} />}
            {record && <Record baseUrl={api.baseUrl} docked={false} />}
          </div>
          <div className="flex items-center gap-1.5 [grid-area:p]">
            {queued || cur ? (
              <Transport player={player} more={<MoreRows v={v} />} className="-ml-1.5" />
            ) : (
              <>
                <button type="button" onClick={() => setTab("browse")} className={cn(PILL, PRIMARY)}>
                  <Search className="h-4 w-4" aria-hidden /> Find a piece
                </button>
                <button type="button" onClick={() => setTab("playlists")} className={cn(PILL, SECONDARY)}>
                  <ListMusic className="h-4 w-4" aria-hidden /> Playlists
                </button>
              </>
            )}
          </div>
          <div className="justify-self-end [grid-area:o]">
            <PlayOn baseUrl={api.baseUrl} compact />
          </div>
        </div>
        {v.state === "preparing" && (
          <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-stone-100" aria-hidden>
            <div className="h-full rounded-full bg-teal-600 transition-[width] duration-500" style={{ width: `${pct ?? 4}%` }} />
          </div>
        )}
        {v.state === "playing" && typeof v.elapsed_s === "number" && dur ? (
          <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-stone-100" aria-hidden>
            <div
              className="h-full rounded-full bg-teal-600/70 transition-[width] duration-1000 ease-linear"
              style={{ width: `${Math.min(100, (100 * v.elapsed_s) / dur)}%` }}
            />
          </div>
        ) : null}
        {v.state === "error" && v.error && v.words && v.words !== v.error && <p className="mt-1 text-sm text-muted-foreground">{v.words}</p>}
        {player.error && <p className="mt-1 text-[15px] text-rose-700">{player.error}</p>}
      </div>
    </section>
  );
}

/** While the dock shows: "Now playing · Pop on the piano 3 of 18", Score, ● Record, ⋯ More. */
function DockedRow({
  v,
  where,
  pos,
  scoreId,
  onPlaylist,
  baseUrl,
  className,
}: {
  v: PlayerView;
  where: string | null;
  pos: string | null;
  scoreId: string | null;
  onPlaylist?: () => void;
  baseUrl: string;
  className?: string;
}) {
  const { player } = useMusic();
  const [open, setOpen] = useState(false);
  // the piece's title is the dock's to say; this row says where it plays from
  const what = where && (v.queue?.playlist || pos) ? where : null;
  return (
    <section aria-labelledby="music-now" data-testid="now-playing" data-docked="" className={className}>
      <div className="flex min-w-0 items-center gap-2">
        <h2 id="music-now" className="min-w-0 flex-1 truncate text-lg font-semibold text-stone-800">
          Now playing
          {what && (
            <>
              <span className="font-normal text-stone-600"> · </span>
              {onPlaylist ? (
                <button type="button" onClick={onPlaylist} title="Open the playlist" className="font-normal text-teal-800 underline-offset-2 hover:underline">
                  {what}
                </button>
              ) : (
                <span className="font-normal text-stone-600">{what}</span>
              )}
              {pos && <span className="font-normal text-muted-foreground"> {pos}</span>}
            </>
          )}
        </h2>
        {scoreId && <ScoreButton id={scoreId} />}
        <Record baseUrl={baseUrl} docked />
        <Popover open={open} onOpenChange={setOpen}>
          <PopoverTrigger asChild>
            <button
              type="button"
              aria-label="More: repeat, shuffle, coming up"
              title="Repeat, shuffle, coming up"
              className="grid h-11 w-11 shrink-0 place-items-center rounded-full text-stone-700 hover:bg-stone-200/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              data-testid="now-more"
            >
              <MoreHorizontal className="h-5 w-5" aria-hidden />
            </button>
          </PopoverTrigger>
          <PopoverContent
            align="end"
            collisionPadding={12}
            className={cn(
              THEME_CLASS,
              "z-[70] max-h-[var(--radix-popover-content-available-height)] w-[min(22rem,calc(100vw-2rem))] space-y-4 overflow-y-auto rounded-2xl p-3",
            )}
          >
            <MoreRows
              v={v}
              playlist={where}
              onPlaylist={
                onPlaylist &&
                (() => {
                  setOpen(false);
                  onPlaylist();
                })
              }
            />
          </PopoverContent>
        </Popover>
      </div>
      {player.error && <p className="text-[15px] text-rose-700">{player.error}</p>}
    </section>
  );
}
