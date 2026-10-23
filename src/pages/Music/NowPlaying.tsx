// The Now playing card at the top of /music: what's on (title, composer · performer), what it's doing in words
// (Getting ready… with a bar, Playing 1:23 / 4:32, Stopped, Stepped aside: something else started the
// piano, or the problem), what's next, Back · Play/Stop · Next, Repeat (Off / All / One), Shuffle, Play on (where it's
// heard, in view: the Room page's PlayOnChoice, with This device) and the sound (the player's own, server side; also
// remembered as the Room page's piano choice so both agree).
// Beside the title, Score opens the piece's written-out score.

import { useEffect, useRef, useState } from "react";
import { FileMusic, Gauge, Repeat, Repeat1, Shuffle, SkipBack, SkipForward, Square, Play, Search, ListMusic } from "lucide-react";
import { cn } from "@/lib/utils";
import { Notice, Pill, Spinner, Unroll } from "@/pages/Room/bits";
import { SoundChoice } from "@/pages/Room/PianoCard";
import { PlayOnChoice } from "@/pages/Room/PlayOn";
import { CARD_CLASS, TAP_BUTTON_CLASS } from "@/pages/Room/theme";
import { usePianoSound, type PianoSound } from "@/pages/Room/usePianoSound";
import { OLD_SHOW_WORDS, percent, playerWords, plural, type PlayerView, type Repeat as RepeatMode } from "./api";
import { CHIP, CHIP_OFF, hasScore, PRIMARY, SECONDARY, useOpenScore } from "./bits";
import { useMusic } from "./context";
import { PLAYER_BUSY } from "./usePlayer";

const ROUND = "grid h-12 w-12 shrink-0 place-items-center rounded-full border border-stone-300 bg-white text-stone-700 hover:bg-stone-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

/** Playback speed by nudges (owner, 23 Oct: "FINE GRAINED. Nudges should be SLIGHTLY faster and SLIGHTLY slower
 *  e.g. +1%, +3% +5% +10% +50% and vice versa"; "implicit log scaling is obviously what's useful"): each nudge
 *  multiplies the current speed (faster n% = x(1 + n/100), slower n% = the inverse, so +3% then -3% is back where
 *  it was), shown as % of the piece's own tempo, 25-400% (the player's range). Sent once the taps stop (one
 *  restart, carrying on from where the piece is). */
const NUDGES = [1, 3, 5, 10, 50];
const SPEED_MIN = 0.25;
const SPEED_MAX = 4;
const SPEED_SETTLE_MS = 600;
const speedWords = (s: number) => {
  const pct = Math.round(s * 1000) / 10;
  return `${Number.isInteger(pct) ? pct : pct.toFixed(1)}%`;
};
const snapSpeed = (s: number) => {
  const v = Math.min(SPEED_MAX, Math.max(SPEED_MIN, Math.round(s * 1000) / 1000));
  return Math.abs(v - 1) < 0.004 ? 1 : v;                // back near the piece's own tempo: exactly 100%
};

function SpeedControl({ speed, onSpeed }: { speed: number; onSpeed: (s: number) => void }) {
  const [draft, setDraft] = useState<number | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);
  const shown = draft ?? speed;
  const change = (s: number, now = false) => {
    const v = snapSpeed(s);
    setDraft(v);
    clearTimeout(timer.current);
    const send = () => {
      setDraft(null);
      if (Math.abs(v - speed) > 1e-6) onSpeed(v);
    };
    if (now) send();
    else timer.current = setTimeout(send, SPEED_SETTLE_MS);
  };
  const nudge = "min-h-10 min-w-12 rounded-full border border-stone-200 bg-white px-2.5 text-[15px] font-medium tabular-nums text-stone-700 hover:bg-stone-50 disabled:opacity-40";
  const row = (sign: 1 | -1) => (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="w-14 text-sm text-muted-foreground">{sign > 0 ? "Faster" : "Slower"}</span>
      {NUDGES.map((n) => (
        <button
          key={n}
          type="button"
          className={nudge}
          aria-label={`${sign > 0 ? "Faster" : "Slower"} by ${n}%`}
          disabled={sign > 0 ? shown >= SPEED_MAX - 1e-6 : shown <= SPEED_MIN + 1e-6}
          onClick={() => change(sign > 0 ? shown * (1 + n / 100) : shown / (1 + n / 100))}
        >
          {sign > 0 ? "+" : "−"}
          {n}%
        </button>
      ))}
    </div>
  );
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <span className="inline-flex items-center gap-1.5 text-[15px] font-medium">
          <Gauge className="h-4 w-4" aria-hidden /> Speed
        </span>
        <span className="min-w-14 text-base font-semibold tabular-nums" aria-live="polite">{speedWords(shown)}</span>
        {Math.abs(shown - 1) > 1e-6 && (
          <button type="button" onClick={() => change(1, true)}
            className="min-h-9 rounded-full border border-stone-200 bg-white px-3 text-[15px] font-medium text-stone-700 hover:bg-stone-50">
            Normal
          </button>
        )}
      </div>
      {row(-1)}
      {row(1)}
    </div>
  );
}

/** The player's sound and output as usePianoSound's PianoSound, so the Room page's SoundChoice chips fit. */
export function usePlayerSound(view: PlayerView | null): PianoSound {
  const local = usePianoSound(view?.choices);
  const has = (list: { id: string }[], id: string | null | undefined) => !!id && list.some((x) => x.id === id);
  return {
    ...local,
    instrument: has(local.instruments, view?.instrument) ? view!.instrument : local.instrument,
    output: has(local.outputs, view?.output) ? view!.output : local.output,
  };
}

export function NowPlaying() {
  const { player, setTab, openPlaylist } = useMusic();
  const v = player.view;
  const sound = usePlayerSound(v);
  const openScore = useOpenScore();

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
  const upcoming = (v.upcoming ?? []).filter(Boolean);
  const who = cur ? [cur.composer, cur.performer].filter(Boolean).join(" · ") : "";

  const setSound = (o: { instrument?: string; output?: string }) => {
    if (o.instrument) sound.setInstrument(o.instrument);
    if (o.output) sound.setOutput(o.output);
    void player.act({ action: "set", ...o }, o);
  };
  const setRepeat = (r: RepeatMode) => void player.act({ action: "set", repeat: r }, { repeat: r });
  const speed = typeof v.speed === "number" ? v.speed : null;          // null: an older show_service (no control)
  const setSpeed = (s: number) => void player.act({ action: "set", speed: s }, { speed: s });
  const soundHint = sound.instruments.find((i) => i.id === sound.instrument)?.label ?? "";

  return (
    <section aria-labelledby="music-now" data-testid="now-playing">
      <div className="mb-2 flex items-center justify-between gap-2">
        <h2 id="music-now" className="text-lg font-semibold text-stone-800">
          Now playing
        </h2>
        {where &&
          (v.queue?.playlist ? (
            <button type="button" onClick={() => openPlaylist(v.queue.playlist!.id)} title="Open the playlist" className="min-w-0 max-w-[65%] rounded-full">
              <Pill tone={busy ? "live" : "muted"} pulse={v.state === "playing"} className="max-w-full truncate">
                {where}
                {pos ? ` · ${pos}` : ""}
              </Pill>
            </button>
          ) : (
            <Pill tone={busy ? "live" : "muted"} pulse={v.state === "playing"} className="max-w-[65%] truncate">
              {where}
              {pos ? ` · ${pos}` : ""}
            </Pill>
          ))}
      </div>
      <div className={cn(CARD_CLASS, "space-y-4 px-3 py-4 sm:px-4")}>
        <div className="min-w-0">
          {cur ? (
            <div className="flex items-start gap-2">
              <div className="min-w-0 flex-1">
                <p className="text-xl font-semibold leading-snug" data-testid="now-title">
                  {cur.title}
                </p>
                {who && <p className="text-base text-muted-foreground">{who}</p>}
              </div>
              {cur.id && hasScore(cur) && (
                <button
                  type="button"
                  onClick={() => openScore(cur.id)}
                  title="The piece's score"
                  className={cn(CHIP, CHIP_OFF, "min-h-9 shrink-0 px-3 text-sm text-stone-600")}
                >
                  <FileMusic className="h-4 w-4" aria-hidden /> Score
                </button>
              )}
            </div>
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
          {v.state === "preparing" && (
            <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-stone-100" aria-hidden>
              <div className="h-full rounded-full bg-teal-600 transition-[width] duration-500" style={{ width: `${pct ?? 4}%` }} />
            </div>
          )}
          {v.state === "playing" && typeof v.elapsed_s === "number" && (v.duration_s ?? cur?.duration_s) ? (
            <div className="mt-2 h-1 overflow-hidden rounded-full bg-stone-100" aria-hidden>
              <div
                className="h-full rounded-full bg-teal-600/70 transition-[width] duration-1000 ease-linear"
                style={{ width: `${Math.min(100, (100 * v.elapsed_s) / (v.duration_s ?? cur?.duration_s ?? 1))}%` }}
              />
            </div>
          ) : null}
          {v.state === "error" && v.error && v.words && v.words !== v.error && <p className="text-sm text-muted-foreground">{v.words}</p>}
        </div>

        {/* Back · Play/Stop · Next */}
        {queued || cur ? (
          <div className="flex items-center gap-3">
            <button type="button" aria-label="Back" title="Back" onClick={() => void player.act({ action: "prev" })} className={ROUND}>
              {player.busy === "prev" ? <Spinner className="h-5 w-5" /> : <SkipBack className="h-5 w-5" aria-hidden />}
            </button>
            <button
              type="button"
              data-testid="play-stop"
              onClick={() =>
                void (busy
                  ? player.act({ action: "stop" }, { state: "stopped", words: "Stopped", progress: null })
                  : player.act({ action: "play" }, { state: "preparing", words: "Getting ready…", progress: null, error: null }))
              }
              className={cn(TAP_BUTTON_CLASS, "inline-flex h-12 min-w-32 flex-1 items-center justify-center gap-2 font-semibold sm:flex-none", busy ? SECONDARY : PRIMARY)}
            >
              {player.busy === "play" || player.busy === "stop" ? (
                <Spinner />
              ) : busy ? (
                <Square className="h-4 w-4" aria-hidden />
              ) : (
                <Play className="h-4 w-4" aria-hidden />
              )}
              {busy ? "Stop" : "Play"}
            </button>
            <button type="button" aria-label="Next" title="Next" onClick={() => void player.act({ action: "next" })} className={ROUND}>
              {player.busy === "next" ? <Spinner className="h-5 w-5" /> : <SkipForward className="h-5 w-5" aria-hidden />}
            </button>
          </div>
        ) : (
          <div className="space-y-2">
            <p className="text-base text-muted-foreground">Pick a piece below, or play one of your playlists.</p>
            <div className="flex flex-wrap gap-2">
              <button type="button" onClick={() => setTab("browse")} className={cn(TAP_BUTTON_CLASS, PRIMARY, "inline-flex items-center gap-2 font-semibold")}>
                <Search className="h-4 w-4" aria-hidden /> Find a piece
              </button>
              <button type="button" onClick={() => setTab("playlists")} className={cn(TAP_BUTTON_CLASS, SECONDARY, "inline-flex items-center gap-2")}>
                <ListMusic className="h-4 w-4" aria-hidden /> Playlists
              </button>
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
        {!v.next && cur && queued && v.repeat === "off" && (
          <p className="text-sm text-muted-foreground">It stops after this one.</p>
        )}

        {/* Repeat and Shuffle */}
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <div className="flex items-center gap-2">
            <span className="inline-flex items-center gap-1.5 text-[15px] font-medium">
              {v.repeat === "one" ? <Repeat1 className="h-4 w-4" aria-hidden /> : <Repeat className="h-4 w-4" aria-hidden />} Repeat
            </span>
            <div role="radiogroup" aria-label="Repeat" className="inline-flex rounded-full border border-stone-200 bg-white p-0.5">
              {(
                [
                  ["off", "Off"],
                  ["all", "All"],
                  ["one", "One"],
                ] as [RepeatMode, string][]
              ).map(([r, w]) => (
                <button
                  key={r}
                  type="button"
                  role="radio"
                  aria-checked={v.repeat === r}
                  onClick={() => v.repeat !== r && setRepeat(r)}
                  className={cn(
                    "min-h-9 min-w-12 rounded-full px-3 text-[15px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
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
              "inline-flex min-h-10 items-center gap-1.5 rounded-full border px-3.5 text-[15px] font-medium transition-colors",
              v.shuffle ? "border-teal-300 bg-teal-50 text-teal-900" : "border-stone-200 bg-white text-stone-700 hover:bg-stone-50",
            )}
          >
            <Shuffle className="h-4 w-4" aria-hidden /> Shuffle {v.shuffle ? "on" : "off"}
          </button>
        </div>

        {speed !== null && <SpeedControl speed={speed} onSpeed={setSpeed} />}

        <PlayOnChoice sound={sound} onOutput={(id) => setSound({ output: id })} />

        {sound.available && (
          <Unroll title="Sound" hint={soundHint} testId="music-sound">
            <div className="pb-1">
              <SoundChoice sound={sound} onInstrument={(id) => setSound({ instrument: id })} />
              {busy && <p className="mt-2 text-sm text-muted-foreground">A change carries on from where the piece is.</p>}
            </div>
          </Unroll>
        )}

        {upcoming.length > 1 && (
          <Unroll title="Coming up" hint={plural(upcoming.length, "piece")}>
            <ol className="space-y-1 pb-1 text-base">
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
          </Unroll>
        )}

        {player.error && <p className="text-[15px] text-rose-700">{player.error}</p>}
      </div>
    </section>
  );
}
