// The Now playing card at the top of /music, compact so the tabs show on the first screen of a phone (owner, 24 Oct:
// "you just haven't really worked the interface ui through properly throughout"):
//   heading   "Now playing", the playlist it's from ("Pop on the piano · 3 of 18": a tap opens it), ● Record
//   card      the title, composer · performer and Score; what it's doing in words (Getting ready… with a bar,
//             Playing 1:23 / 4:32, Stopped, Stepped aside, or the problem); the shared Transport (⏮ ⏯ ⏭ and ⋯ More:
//             Stop, Speed, Sound, then Repeat, Shuffle and Coming up) beside Play on as the compact pill; "Next: …".
// While the docked player shows at the foot of the screen (dockState's useDocked: music plays, prepares or is paused,
// or the piano records), the dock is the one player: the card shrinks to one heading row, "Now playing · Pop on the
// piano 3 of 18", Score, ● Record and a ⋯ More with Repeat, Shuffle, Coming up and the playlist (the dock has Stop,
// Speed and Sound).

import { useState, type ReactNode } from "react";
import { FileMusic, ListMusic, MoreHorizontal, Repeat, Repeat1, Search, Shuffle } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useDocked } from "@/components/player/dockState";
import { Transport } from "@/components/player/Transport";
import { cn } from "@/lib/utils";
import { Notice, Pill, Spinner } from "@/pages/Room/bits";
import { RecordPill, useRecorder } from "@/pages/Room/PianoRecorder";
import { PlayOn } from "@/pages/Room/PlayOn";
import { CARD_CLASS, TAP_BUTTON_CLASS, THEME_CLASS } from "@/pages/Room/theme";
import { myOutput } from "@/pages/Room/listenStream";
import { usePianoSound, type PianoSound } from "@/pages/Room/usePianoSound";
import { OLD_SHOW_WORDS, percent, playerWords, type PlayerView, type Repeat as RepeatMode } from "./api";
import { CHIP, CHIP_OFF, hasScore, PRIMARY, SECONDARY, useOpenScore } from "./bits";
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

function ScoreButton({ id, compact }: { id: string; compact?: boolean }) {
  const openScore = useOpenScore();
  return (
    <button
      type="button"
      onClick={() => openScore(id)}
      title="The piece's score"
      aria-label={compact ? "Score" : undefined}
      className={cn(CHIP, CHIP_OFF, "min-h-9 shrink-0 px-3 text-sm text-stone-600", compact && "max-sm:px-2.5")}
    >
      <FileMusic className="h-4 w-4" aria-hidden />
      <span className={cn(compact && "max-sm:sr-only")}>Score</span>
    </button>
  );
}

export function NowPlaying() {
  const { api, player, setTab, openPlaylist } = useMusic();
  const docked = useDocked();
  const v = player.view;

  if (player.link === "old") {
    return (
      <Notice tone="muted" className="text-base">
        {OLD_SHOW_WORDS}
      </Notice>
    );
  }
  if (!v) {
    return (
      <div className={cn(CARD_CLASS, "px-4 py-4")}>
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

  const busy = PLAYER_BUSY.includes(v.state);
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
    return <DockedRow v={v} where={where} pos={pos} scoreId={scoreId} onPlaylist={toPlaylist} baseUrl={api.baseUrl} />;
  }

  let wherePill: ReactNode = null;
  if (where) {
    const pill = (
      <Pill tone={busy ? "live" : "muted"} pulse={v.state === "playing"} className="max-w-full truncate">
        {where}
        {pos ? ` · ${pos}` : ""}
      </Pill>
    );
    wherePill = toPlaylist ? (
      <button type="button" onClick={toPlaylist} title="Open the playlist" className="min-w-0 max-w-full rounded-full">
        {pill}
      </button>
    ) : (
      pill
    );
  }

  return (
    <section aria-labelledby="music-now" data-testid="now-playing">
      <div className="mb-2 flex min-w-0 items-center gap-2">
        <h2 id="music-now" className="shrink-0 text-lg font-semibold text-stone-800">
          Now playing
        </h2>
        <div className="flex min-w-0 flex-1">{wherePill}</div>
        <Record baseUrl={api.baseUrl} docked={false} />
      </div>
      <div className={cn(CARD_CLASS, "space-y-3 px-3 py-3 sm:px-4")}>
        <div className="flex items-start gap-2">
          <div className="min-w-0 flex-1">
            {cur ? (
              <>
                <p className="text-xl font-semibold leading-snug" data-testid="now-title">
                  {cur.title}
                </p>
                {who && <p className="text-base text-muted-foreground">{who}</p>}
              </>
            ) : (
              <p className="text-xl font-semibold leading-snug text-stone-700">Nothing playing</p>
            )}
            <p
              role="status"
              className={cn(
                "mt-1 flex items-center gap-2 text-base",
                v.state === "error" ? "text-amber-800" : v.state === "playing" ? "text-teal-800" : "text-muted-foreground",
              )}
            >
              {v.state === "preparing" && <Spinner />}
              {v.state === "playing" && <span aria-hidden className="h-2 w-2 rounded-full bg-teal-600 motion-safe:animate-pulse" />}
              {playerWords(v)}
            </p>
          </div>
          {scoreId && <ScoreButton id={scoreId} />}
        </div>
        {v.state === "preparing" && (
          <div className="h-1.5 overflow-hidden rounded-full bg-stone-100" aria-hidden>
            <div className="h-full rounded-full bg-teal-600 transition-[width] duration-500" style={{ width: `${pct ?? 4}%` }} />
          </div>
        )}
        {v.state === "playing" && typeof v.elapsed_s === "number" && (v.duration_s ?? cur?.duration_s) ? (
          <div className="h-1 overflow-hidden rounded-full bg-stone-100" aria-hidden>
            <div
              className="h-full rounded-full bg-teal-600/70 transition-[width] duration-1000 ease-linear"
              style={{ width: `${Math.min(100, (100 * v.elapsed_s) / (v.duration_s ?? cur?.duration_s ?? 1))}%` }}
            />
          </div>
        ) : null}
        {v.state === "error" && v.error && v.words && v.words !== v.error && <p className="text-sm text-muted-foreground">{v.words}</p>}

        {queued || cur ? (
          <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
            <Transport player={player} more={<MoreRows v={v} />} className="-ml-1.5" />
            <PlayOn baseUrl={api.baseUrl} compact />
          </div>
        ) : (
          <div className="space-y-2">
            <p className="text-base text-muted-foreground">Pick a piece below, or play one of your playlists.</p>
            <div className="flex flex-wrap items-center gap-2">
              <button type="button" onClick={() => setTab("browse")} className={cn(TAP_BUTTON_CLASS, PRIMARY, "inline-flex items-center gap-2 font-semibold")}>
                <Search className="h-4 w-4" aria-hidden /> Find a piece
              </button>
              <button type="button" onClick={() => setTab("playlists")} className={cn(TAP_BUTTON_CLASS, SECONDARY, "inline-flex items-center gap-2")}>
                <ListMusic className="h-4 w-4" aria-hidden /> Playlists
              </button>
              <PlayOn baseUrl={api.baseUrl} compact className="ml-auto" />
            </div>
          </div>
        )}

        {v.next && (
          <p className="truncate text-base">
            <span className="text-muted-foreground">Next: </span>
            <span className="font-medium">{v.next.title}</span>
            {v.next.composer && <span className="text-muted-foreground"> · {v.next.composer}</span>}
          </p>
        )}
        {!v.next && cur && queued && v.repeat === "off" && <p className="text-sm text-muted-foreground">It stops after this one.</p>}

        {player.error && <p className="text-[15px] text-rose-700">{player.error}</p>}
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
}: {
  v: PlayerView;
  where: string | null;
  pos: string | null;
  scoreId: string | null;
  onPlaylist?: () => void;
  baseUrl: string;
}) {
  const { player } = useMusic();
  const [open, setOpen] = useState(false);
  const what = v.current?.title ?? where ?? "Nothing playing";
  return (
    <section aria-labelledby="music-now" data-testid="now-playing" data-docked="">
      <div className="flex min-w-0 items-center gap-2">
        <h2 id="music-now" className="min-w-0 flex-1 truncate text-lg font-semibold text-stone-800">
          {/* at phone width the piece is what fits: "Now playing" is read out, not shown */}
          <span className="max-sm:sr-only">Now playing · </span>
          <span className="sm:font-normal sm:text-stone-600">{what}</span>
          {pos && <span className="font-normal text-muted-foreground"> {pos}</span>}
        </h2>
        {scoreId && <ScoreButton id={scoreId} compact />}
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
