// One transport for every player (the dock's mini player, Music's Now playing, the score's bar): the music player's
//   ⏮   the previous piece, or back to the start of this one when it is more than 3 s in
//   ⏯   Play / Pause (paused: carry on; stopped: play the queue)
//   ⏭   the next piece
//   ⋯   More: Stop, Speed (SpeedNudges) and Sound (SoundChoice), then whatever the page adds (`more`: Watch, Repeat,
//       Shuffle, Coming up)
// The score passes its bar steps (`steps`, either side of Play) and its own Play (`play`: play from the page in view);
// `pieceSteps={false}` drops ⏮ ⏭ there. Every action goes through the shared PlayerHook (components/player/appPlayer).

import { useState, type ReactNode } from "react";
import { MoreHorizontal, Pause, Play, SkipBack, SkipForward, Square } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import { usePlayerSound } from "@/pages/Music/NowPlaying";
import { PLAYER_BUSY, type PlayerHook } from "@/pages/Music/usePlayer";
import { Spinner } from "@/pages/Room/bits";
import { SoundChoice } from "@/pages/Room/PianoCard";
import { SpeedNudges } from "@/pages/Room/SpeedNudges";
import { THEME_CLASS } from "@/pages/Room/theme";

/** ⏮ goes back to the start of the piece, not to the one before, once it is this far in. */
const RESTART_AFTER_S = 3;
const ROUND =
  "grid h-11 w-11 shrink-0 place-items-center rounded-full text-stone-700 hover:bg-stone-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-40";
const PLAY = "bg-teal-700 text-white hover:bg-teal-800 disabled:bg-stone-300";
const PLAY_GUESS = { state: "preparing", words: "Getting ready…", progress: null, error: null } as const;

/** Where the piece is now, in seconds (the poll's reading, carried on by the clock while it plays). */
function elapsedNow(player: PlayerHook): number | null {
  const v = player.view;
  if (!v || typeof v.elapsed_s !== "number") return null;
  if (v.state !== "playing" || typeof v.at_ms !== "number") return v.elapsed_s;
  return v.elapsed_s + ((Date.now() - v.at_ms) / 1000) * (v.speed ?? 1);
}

export interface TransportPlay {
  going: boolean;
  starting?: boolean;
  label: string;
  onClick: () => void;
}

export function Transport({
  player,
  compact,
  steps,
  play,
  pieceSteps = true,
  more,
  onDone,
  className,
}: {
  player: PlayerHook;
  /** Smaller gaps (a bar). */
  compact?: boolean;
  /** The score's bar steps: drawn either side of Play. */
  steps?: { back: ReactNode; on: ReactNode };
  /** Play / Pause done another way (the score: play from the page in view). */
  play?: TransportPlay;
  /** ⏮ previous and ⏭ next (off on the score, which steps by bars). */
  pieceSteps?: boolean;
  /** More's own rows, after Stop, Speed and Sound. */
  more?: ReactNode;
  /** Told how each action went: what it was, and the problem in words when it failed. */
  onDone?: (what: string, problem: string | null) => void;
  className?: string;
}) {
  const v = player.view;
  const [open, setOpen] = useState(false);
  const sound = usePlayerSound(v);
  const going = !!v && PLAYER_BUSY.includes(v.state);
  const paused = v?.state === "paused";
  const queued = (v?.queue?.count ?? 0) > 0;
  const has = !!v?.current || queued;
  const speed = typeof v?.speed === "number" ? v.speed : null; // null: an older show_service (no control)

  const act = async (what: string, body: Parameters<PlayerHook["act"]>[0], guess?: Parameters<PlayerHook["act"]>[1]) => {
    const r = await player.act(body, guess);
    onDone?.(what, r.ok ? null : r.words);
  };
  const back = () => {
    const at = elapsedNow(player);
    if (at !== null && at > RESTART_AFTER_S) void act("back to the start", { action: "seek", start_s: 0 });
    else void act("previous", { action: "prev" });
  };
  const toggle = () => {
    if (going) void act("pause", { action: "pause" });
    else if (paused) void act("resume", { action: "resume" });
    else void act("play", { action: "play" }, PLAY_GUESS);
  };
  const setInstrument = (id: string) => {
    sound.setInstrument(id);
    void act("sound", { action: "set", instrument: id }, { instrument: id });
  };

  const p: TransportPlay = play ?? { going, label: going ? "Pause" : paused ? "Carry on" : "Play", onClick: toggle };
  const playBusy = !play && (player.busy === "play" || player.busy === "resume" || player.busy === "pause");

  return (
    <div className={cn("flex shrink-0 items-center", compact ? "gap-0" : "gap-0.5", className)} role="group" aria-label="Play">
      {pieceSteps && (
        <button type="button" aria-label="Previous" title="Previous (back to the start once it's playing)" disabled={!has} onClick={back} className={ROUND}>
          {player.busy === "prev" || player.busy === "seek" ? <Spinner /> : <SkipBack className="h-5 w-5" aria-hidden />}
        </button>
      )}
      {steps?.back}
      <button
        type="button"
        aria-label={p.label}
        title={p.label}
        disabled={!play && !has}
        onClick={p.onClick}
        className={cn(ROUND, "relative h-12 w-12", PLAY)}
        data-testid="transport-play"
      >
        {p.starting && <span aria-hidden className="absolute inset-0 rounded-full border-2 border-white/70 border-t-transparent motion-safe:animate-spin" />}
        {playBusy ? <Spinner /> : p.going ? <Pause className="h-5 w-5" aria-hidden /> : <Play className="h-5 w-5 translate-x-px" aria-hidden />}
      </button>
      {steps?.on}
      {pieceSteps && (
        <button type="button" aria-label="Next" title="Next" disabled={!has} onClick={() => void act("next", { action: "next" })} className={ROUND}>
          {player.busy === "next" ? <Spinner /> : <SkipForward className="h-5 w-5" aria-hidden />}
        </button>
      )}
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <button
            type="button"
            className={cn(ROUND, speed !== null && speed !== 1 && "flex w-auto items-center justify-center gap-0.5 px-2 text-[15px] font-medium tabular-nums")}
            aria-label={`More: stop, speed and sound${speed !== null && speed !== 1 ? ` (speed ${Math.round(speed * 100)}%)` : ""}`}
            title="Stop, speed and sound"
            onClick={(e) => e.stopPropagation()}
            data-testid="transport-more"
          >
            {speed !== null && speed !== 1 && <span>{Math.round(speed * 100)}%</span>}
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
          onClick={(e) => e.stopPropagation()}
        >
          <button
            type="button"
            onClick={() => {
              setOpen(false);
              void act("stop", { action: "stop" }, { state: "stopped", words: "Stopped", progress: null });
            }}
            className="inline-flex min-h-11 w-full items-center gap-2 rounded-xl border border-stone-300 bg-white px-3 text-base font-medium hover:bg-stone-50"
          >
            {player.busy === "stop" ? <Spinner /> : <Square className="h-4 w-4" aria-hidden />} Stop
          </button>
          {speed !== null && (
            <div>
              {/* SpeedNudges carries its own "Speed" label */}
              <SpeedNudges speed={speed} onSpeed={(s) => void act("speed", { action: "set", speed: s }, { speed: s })} />
            </div>
          )}
          {sound.available && (
            <div>
              <p className="mb-1.5 text-sm font-medium text-stone-600">Sound</p>
              <SoundChoice sound={sound} onInstrument={setInstrument} bare />
            </div>
          )}
          {more}
        </PopoverContent>
      </Popover>
    </div>
  );
}
