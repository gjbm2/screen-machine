// One compact play bar for every view that watches the music play (owner, 23 Oct: "playback controls in score view
// mode don't include speed controls -- silly ... why not use the same generic compact playback component in both
// places and allow user to expand it etc if they want more granular control (choose instrument, speed, etc.)"; "the
// pianola mode and the score following mode are alternative 'watch midi play' modes ... rethink how user should flip
// / navigate between them"). The Music page's player, whichever view it's in:
//
//   ⏮ ◀ ⏯ ▶   the score's (follow.tsx Transport: bars, play from the page in view) with a `follow`, else back to the
//             start, 10 s back, play / pause, 10 s on
//   100%      the speed; a tap opens More
//   Play on   where it's heard (Room/PlayOn.tsx, compact)
//   ⋯ More    the speed (fine nudges), the sound (instrument), and Watch: Score | Pianola (onWatch)
//
// Inside the Music page it shares the page's player (context); elsewhere (the Room page's pianola) it keeps its own.

import { useMemo, useState } from "react";
import { useAppPlayer } from "@/components/player/appPlayer";
import { MoreHorizontal, Pause, Play, Rewind, FastForward, SkipBack } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import { SoundChoice } from "@/pages/Room/PianoCard";
import { PlayOn } from "@/pages/Room/PlayOn";
import { SpeedNudges } from "@/pages/Room/SpeedNudges";
import { THEME_CLASS } from "@/pages/Room/theme";
import { MusicApi } from "./api";
import { useMusicMaybe } from "./context";
import { Transport, type Follow } from "./follow";
import { usePlayerSound } from "./NowPlaying";
import { usePlayer, PLAYER_BUSY, type PlayerHook } from "./usePlayer";

export type Watching = "score" | "pianola";

const ROUND = "grid h-10 w-10 shrink-0 place-items-center rounded-full text-stone-700 hover:bg-stone-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-40";
const PILL = "inline-flex h-9 shrink-0 items-center gap-1 rounded-full border border-stone-200 bg-white px-2.5 text-[15px] font-medium tabular-nums text-stone-700 hover:bg-stone-50";
const SEG = "min-h-9 flex-1 rounded-full px-3 text-[15px] font-medium";

function useBarPlayer(baseUrl: string): PlayerHook {
  const ctx = useMusicMaybe();
  const api = useMemo(() => new MusicApi(baseUrl), [baseUrl]);
  const shared = useAppPlayer(baseUrl);
  const own = usePlayer(api, !ctx && !shared); // only polls when there's no page or app player to share
  return ctx?.player ?? shared ?? own;
}

/** ⏮ ◀ ⏯ ▶ by time, for a view without bars (the pianola). */
function TimeTransport({ player, compact }: { player: PlayerHook; compact?: boolean }) {
  const v = player.view;
  const going = !!v && PLAYER_BUSY.includes(v.state);
  const at = typeof v?.elapsed_s === "number" ? v.elapsed_s : null;
  const seek = (t: number) => void player.act({ action: "seek", start_s: Math.max(0, t) });
  return (
    <div className={cn("flex items-center", compact ? "gap-0" : "gap-1")} role="group" aria-label="Play">
      <button type="button" aria-label="Back to the start" title="Back to the start" disabled={!v?.current} onClick={() => seek(0)} className={ROUND}>
        <SkipBack className="h-5 w-5" aria-hidden />
      </button>
      <button type="button" aria-label="10 seconds back" title="10 seconds back" disabled={at === null} onClick={() => at !== null && seek(at - 10)} className={ROUND}>
        <Rewind className="h-5 w-5" aria-hidden />
      </button>
      <button
        type="button"
        aria-label={going ? "Pause" : "Play"}
        title={going ? "Pause" : "Play"}
        disabled={!v?.current}
        onClick={() => void (going ? player.act({ action: "pause" }) : v?.state === "paused" ? player.act({ action: "resume" }) : player.act({ action: "play" }))}
        className={cn(ROUND, "h-12 w-12 bg-teal-700 text-white hover:bg-teal-800")}
      >
        {going ? <Pause className="h-5 w-5" aria-hidden /> : <Play className="h-5 w-5 translate-x-px" aria-hidden />}
      </button>
      <button type="button" aria-label="10 seconds on" title="10 seconds on" disabled={at === null} onClick={() => at !== null && seek(at + 10)} className={ROUND}>
        <FastForward className="h-5 w-5" aria-hidden />
      </button>
    </div>
  );
}

export function PlayBar({
  baseUrl,
  follow,
  pageInView = 1,
  compact,
  watching,
  onWatch,
  className,
}: {
  /** room_service's address (the page's API base). */
  baseUrl: string;
  /** The score's follow-along: bar steps and play from the page in view. Without it, the time transport. */
  follow?: Follow | null;
  pageInView?: number;
  compact?: boolean;
  /** The view this bar is in, and how to go to the other one (no Watch switch without it). */
  watching?: Watching;
  onWatch?: (w: Watching) => void;
  className?: string;
}) {
  const player = useBarPlayer(baseUrl);
  const v = player.view;
  const sound = usePlayerSound(v);
  const [open, setOpen] = useState(false);
  const speed = typeof v?.speed === "number" ? v.speed : null;
  const setSpeed = (s: number) => void player.act({ action: "set", speed: s }, { speed: s });
  const setInstrument = (id: string) => {
    sound.setInstrument(id);
    void player.act({ action: "set", instrument: id });
  };

  return (
    <div className={cn("flex min-w-0 flex-wrap items-center gap-1.5", className)}>
      {follow ? <Transport follow={follow} pageInView={pageInView} compact={compact} /> : <TimeTransport player={player} compact={compact} />}
      {/* its own clicks stay its own (a full-screen bar's click hands focus to the page, which would close it) */}
      <span className="contents" onClick={(e) => e.stopPropagation()}>
        <PlayOn baseUrl={baseUrl} compact className={compact ? "h-9 max-w-[9rem] px-2.5" : undefined} />
      </span>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <button
            type="button"
            className={PILL}
            aria-label={`${speed !== null ? `Speed ${Math.round(speed * 100)}%. ` : ""}More: speed, sound and view`}
            title="Speed, sound and view"
            onClick={(e) => e.stopPropagation()}
          >
            {speed !== null && <span>{Math.round(speed * 100)}%</span>}
            <MoreHorizontal className="h-5 w-5" aria-hidden />
          </button>
        </PopoverTrigger>
        <PopoverContent
          align="end"
          className={cn(THEME_CLASS, "z-[70] w-[min(22rem,calc(100vw-2rem))] space-y-4 rounded-2xl p-3")}
          onClick={(e) => e.stopPropagation()}
        >
          {watching && onWatch && (
            <div>
              <p className="mb-1.5 text-sm font-medium text-stone-600">Watch</p>
              <div className="flex gap-1 rounded-full border border-stone-200 bg-stone-50 p-1" role="radiogroup" aria-label="Watch">
                {(["score", "pianola"] as const).map((w) => (
                  <button
                    key={w}
                    type="button"
                    role="radio"
                    aria-checked={watching === w}
                    onClick={() => {
                      setOpen(false);
                      if (w !== watching) onWatch(w);
                    }}
                    className={cn(SEG, watching === w ? "bg-white text-teal-900 shadow-sm" : "text-stone-600 hover:bg-white/60")}
                  >
                    {w === "score" ? "Score" : "Pianola"}
                  </button>
                ))}
              </div>
            </div>
          )}
          {speed !== null && (
            <div>
              <SpeedNudges speed={speed} onSpeed={setSpeed} />
            </div>
          )}
          {sound.available && (
            <div>
              <SoundChoice sound={sound} onInstrument={setInstrument} />
            </div>
          )}
        </PopoverContent>
      </Popover>
    </div>
  );
}
