// "This device" on a page (the Room page and /music each mount one ListenHere): the stream's line in words
// (ListenLine, also under Play on and on the music screen), a bar along the bottom while the sound plays here (Open the
// music screen, Stop), the live music screen itself (LiveMusicView.tsx), and what they need:
//   - the chosen sound, for the stream's ?instrument= (usePianoSound against GET /show/api/v1/test's list: read once at
//     once, every 60 s, every 4 s while on), handed to listenHere.ts with where show_service is (the page's API base);
//   - what's playing, while on: the music player's piece (GET /show/api/v1/player), else the test piano's (Test music
//     or a recording), else "The piano" while the real one sends notes;
//   - the notes, while on: WS /show/v1/show (music.note, music.pedal, music.notes; every source, as PianoRow), drawn
//     as late as this device hears them (deviceAudio.ts knows the exact frame it plays: deviceLag.ts), else 0.5 s
//     until the first reading. The first note after the sound is
//     turned on opens the screen; once closed it stays closed until it's turned on again.
// nowWords is that what's-playing logic on its own; useWhatsOn reads it for the docked player while the music player
// is idle (a test piece with Stop, else "Someone's playing the piano", else "Hearing: <what the room hears>").

import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Maximize2, Volume2, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { useDocked } from "@/components/player/dockState";
import { MusicApi, type PlayerView } from "@/pages/Music/api";
import { loadToken, RoomApi } from "./api";
import { Spinner } from "./bits";
import { useDeviceLag } from "./deviceLag";
import {
  behindS,
  configureListen,
  fetchDevices,
  listenFlush,
  listenOff,
  myOutput,
  listenSnap,
  listenTap,
  setListenView,
  setRoomOutput,
  useListenHere,
  type ListenStatus,
} from "./listenStream";
import { LiveMusicView } from "./LiveMusicView";
import { PlayBar } from "@/pages/Music/PlayBar";
import { NoteFeed } from "./noteFeed";
import { CARD_CLASS, TAP_BUTTON_CLASS } from "./theme";
import type { ShowCard, ShowTestSource, ShowTestView } from "./types";
import { usePianoSound } from "./usePianoSound";

const POLL_ON_MS = 4_000;
const POLL_OFF_MS = 60_000;
const RETRY_MS = 2_000;
const LAG_EVERY_MS = 2_000;
/** The real piano counts as what's playing this long after its last note. */
const LIVE_FOR_MS = 15_000;
const PLAYER_BUSY = ["preparing", "playing"];
const PIANO_BUSY = ["preparing", "starting", "playing"];

const BUSY_WORDS: Partial<Record<ListenStatus, string>> = {
  connecting: "Getting the sound ready…",
};
const STOPPED_WORDS: Partial<Record<ListenStatus, string>> = {
  failed: "The sound stopped.",
};

/** What's playing, in words: the music player's piece, else the test piano's (Test music or a recording), else the
 *  real piano while it sends notes. `kind` says which ("none": nothing). */
export function nowWords(
  player: PlayerView | null,
  test: ShowTestView | null,
  pianoLive: boolean,
): { kind: "player" | "test" | "piano" | "none"; title: string; sub: string | null; source?: ShowTestSource } {
  const piano = test?.sources?.piano;
  if (player && PLAYER_BUSY.includes(player.state) && player.current)
    return { kind: "player", title: player.current.title, sub: [player.current.composer, player.current.performer].filter(Boolean).join(" · ") || null };
  if (piano && PIANO_BUSY.includes(piano.state)) return { kind: "test", title: piano.label || "The test piano", sub: "Test piano", source: "piano" };
  const audio = test?.sources?.audio;
  if (audio && PIANO_BUSY.includes(audio.state)) return { kind: "test", title: audio.label || "Test music", sub: "Test music", source: "audio" };
  if (pianoLive) return { kind: "piano", title: "The piano", sub: "Played in the room" };
  return { kind: "none", title: "Nothing playing just now", sub: "It plays here as soon as the music starts." };
}

const WHATS_ON_MS = 5_000;
/** Hearing words that say nothing about the music. */
const HEARING_SKIP = new Set(["", "Listening", "Quiet", "No sound coming in"]);

export interface WhatsOn {
  /** The idle line's words: "Test music: <piece>", "Someone's playing the piano", "Hearing: soul · relaxed". */
  line: string;
  /** A test piece plays: its Stop. */
  stop?: () => Promise<void>;
}

/**
 * What's on while the music player is idle, for the docked player's empty line: a test piece (with Stop), else
 * someone at the piano (its MIDI streaming: GET /show/api/v1/inputs), else what the room hears (GET
 * /show/api/v1/card's hearing). Read every 5 s while `enabled` and the page is visible; null: nothing to say.
 */
export function useWhatsOn(baseUrl: string, enabled: boolean): WhatsOn | null {
  const api = useMemo(() => new RoomApi(baseUrl, loadToken(baseUrl).token), [baseUrl]);
  const [test, setTest] = useState<ShowTestView | null>(null);
  const [pianoLive, setPianoLive] = useState(false);
  const [hearing, setHearing] = useState<string | null>(null);
  const [n, bump] = useState(0);
  useEffect(() => {
    if (!enabled) return;
    let stop = false;
    let timer: number | undefined;
    const ac = new AbortController();
    const poll = async () => {
      if (document.visibilityState === "visible") {
        const [t, inp, card] = await Promise.all([
          api.showTest(ac.signal).catch(() => null),
          api.showInputs(ac.signal).catch(() => null) as Promise<{ pianos?: { state?: string }[] } | null>,
          api.showCard(ac.signal).catch(() => null) as Promise<ShowCard | null>,
        ]);
        if (stop) return;
        setTest(t);
        setPianoLive(!!inp?.pianos?.some((p) => p.state === "streaming"));
        setHearing(card?.hearing?.text ?? null);
      }
      timer = window.setTimeout(() => void poll(), WHATS_ON_MS);
    };
    void poll();
    return () => {
      stop = true;
      ac.abort();
      window.clearTimeout(timer);
    };
  }, [api, enabled, n]);
  if (!enabled) return null;
  const w = nowWords(null, test, pianoLive);
  if (w.kind === "test" && w.source) {
    const source = w.source;
    return {
      line: `${w.sub}: ${w.title}`,
      stop: async () => {
        await api.showTestStop(source).catch(() => undefined);
        bump((x) => x + 1);
      },
    };
  }
  if (w.kind === "piano") return { line: "Someone's playing the piano" };
  if (hearing && !HEARING_SKIP.has(hearing)) return { line: `Hearing: ${hearing}` };
  return null;
}

/** "This device" in words, with its one action (Tap to start sound, Try again, Open). Nothing while it's off. */
export function ListenLine({ big, onOpen, className }: { big?: boolean; onOpen?: () => void; className?: string }) {
  const here = useListenHere();
  useDeviceLag(); // re-render when the measured lag changes
  const [, tick] = useState(0);
  useEffect(() => {
    if (here.status !== "playing") return;
    const id = window.setInterval(() => tick((n) => n + 1), LAG_EVERY_MS);
    return () => window.clearInterval(id);
  }, [here.status]);
  if (!here.on || here.status === "off") return null;

  if (here.status === "tap")
    return (
      <button
        type="button"
        onClick={listenTap}
        className={cn(
          TAP_BUTTON_CLASS,
          "inline-flex w-full items-center justify-center gap-2 bg-primary font-semibold text-primary-foreground hover:bg-primary/90",
          big && "h-14 text-[17px]",
          className,
        )}
      >
        <Volume2 className="h-5 w-5" aria-hidden /> Tap to start sound
      </button>
    );

  const busy = BUSY_WORDS[here.status];
  const stopped = STOPPED_WORDS[here.status];
  const behind = Math.round(behindS() * 10) / 10;
  // connected, but the room's output moved elsewhere (another page or device): the stream carries silence
  const quiet = here.status === "quiet" || (!here.also && !!here.roomOutput && here.roomOutput !== myOutput());
  return (
    <div className={cn("flex min-h-11 items-center gap-2", className)} role="status">
      {busy ? (
        <Spinner className="shrink-0" />
      ) : (
        <span
          aria-hidden
          className={cn("h-2.5 w-2.5 shrink-0 rounded-full", stopped ? "bg-amber-500" : "bg-teal-600 motion-safe:animate-pulse")}
        />
      )}
      <p className={cn("min-w-0 flex-1 text-base leading-snug", stopped ? "text-amber-800" : "text-stone-700")}>
        {busy ??
          stopped ??
          (quiet ? (
            <>
              Quiet here
              <span className="text-muted-foreground"> · the room plays on {here.roomOutputLabel || "something else"}</span>
            </>
          ) : (
            <>
              Playing on this device
              <span className="text-muted-foreground"> · about {behind} s behind the room</span>
            </>
          ))}
      </p>
      {stopped ? (
        <button
          type="button"
          onClick={listenTap}
          className="h-11 shrink-0 rounded-xl border border-stone-300 bg-white px-3 text-[15px] font-medium hover:bg-stone-50"
        >
          Try again
        </button>
      ) : (
        onOpen && (
          <button
            type="button"
            onClick={onOpen}
            className="inline-flex h-11 shrink-0 items-center gap-1.5 rounded-xl border border-stone-300 bg-white px-3 text-[15px] font-medium hover:bg-stone-50"
          >
            <Maximize2 className="h-4 w-4" aria-hidden /> Open
          </button>
        )
      )}
    </div>
  );
}

/** The page's "This device": mount once, inside the page's themed root. */
export function ListenHere({ api, onScore = false }: { api: RoomApi; onScore?: boolean }) {
  // on a score (owner, 23 Oct: "the playing behind this device piece floating panel gets in the way when you're
  // watching a score"): no bar over the music and the live music screen doesn't open by itself; the score's own
  // Play on shows the line
  const navigate = useNavigate();
  const scoreRef = useRef(onScore);
  scoreRef.current = onScore;
  const here = useListenHere();
  // the docked player carries this device's line while music plays (one bar at the foot, not two)
  const docked = useDocked();
  const music = useMemo(() => new MusicApi(api.baseUrl), [api.baseUrl]);
  const [test, setTest] = useState<ShowTestView | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [player, setPlayer] = useState<PlayerView | null>(null);
  const [liveAt, setLiveAt] = useState(0);
  const feed = useMemo(() => new NoteFeed(), []);
  const sound = usePianoSound(test);
  // the pianola can be open without the sound here (openPianola): the notes and what's playing are read for it too
  const watching = here.on || here.viewOpen;

  // show_service's sounds (and, while on, what's playing): only while the page is visible
  useEffect(() => {
    let stop = false;
    let timer: number | undefined;
    let was: string | null = null;
    const ac = new AbortController();
    const poll = async () => {
      if (document.visibilityState === "visible") {
        const [t, p, d] = await Promise.all([
          api.showTest(ac.signal).catch(() => undefined),
          watching ? music.player(ac.signal).catch(() => null) : Promise.resolve(null),
          here.on ? fetchDevices(ac.signal) : Promise.resolve(null),
        ]);
        if (stop) return;
        if (t) setTest(t);
        // paused or stopped from another page: what this device holds unplayed isn't heard
        if (p && was && PLAYER_BUSY.includes(was) && !PLAYER_BUSY.includes(p.state)) listenFlush();
        was = p?.state ?? null;
        setPlayer(p);
        if (d) {
          const o = d.output ?? null;
          const label = o === myOutput() ? "This device" : ((t?.outputs ?? []).find((x) => x.id === o)?.label ?? d.devices.find((x) => x.id === o)?.label ?? null);
          setRoomOutput(o, label);
        }
        setLoaded(true);
      }
      timer = window.setTimeout(() => void poll(), watching ? POLL_ON_MS : POLL_OFF_MS);
    };
    const onVisible = () => {
      if (document.visibilityState !== "visible") return;
      window.clearTimeout(timer);
      void poll();
    };
    document.addEventListener("visibilitychange", onVisible);
    void poll();
    return () => {
      stop = true;
      ac.abort();
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [api, music, here.on, watching]);

  // the stream's sound: once show_service has answered (or not), so the first connect has it
  useEffect(() => {
    if (loaded) configureListen(api.baseUrl, sound.instrument);
  }, [loaded, api.baseUrl, sound.instrument]);

  // the notes, while on or watching
  useEffect(() => {
    if (!watching) return;
    const url = api.showEventsUrl().replace(/\/v1\/events$/, "/v1/show");
    let sock: WebSocket | null = null;
    let retry: number | undefined;
    let closed = false;
    let liveMark = 0;
    const open = () => {
      sock = new WebSocket(url);
      sock.onopen = () =>
        sock?.send(
          JSON.stringify({ type: "hello", v: 2, role: "room-page", client_id: "room-listen-here", want: ["music.note", "music.pedal", "music.notes"] }),
        );
      sock.onmessage = (ev) => {
        try {
          const m = JSON.parse(ev.data as string) as { type?: string; data?: Record<string, unknown> };
          if (m.type !== "music.note" && m.type !== "music.pedal" && m.type !== "music.notes") return;
          const s = listenSnap();
          feed.push(m, s.on ? behindS() * 1000 : 0);
          if (m.type === "music.note" && m.data?.on === true) {
            if (s.on && !s.dismissed && !s.viewOpen && !scoreRef.current) setListenView(true);
            const now = Date.now();
            if (m.data.source === "live" && now - liveMark > 2_000) {
              liveMark = now;
              setLiveAt(now);
            }
          }
        } catch {
          /* a message we can't read: skipped */
        }
      };
      sock.onclose = () => {
        if (!closed) retry = window.setTimeout(open, RETRY_MS);
      };
    };
    open();
    return () => {
      closed = true;
      window.clearTimeout(retry);
      sock?.close();
    };
  }, [api, feed, watching]);

  if (!watching) return null;

  // what's playing
  const piano = test?.sources?.piano;
  const { title, sub } = nowWords(player, test, Date.now() - liveAt < LIVE_FOR_MS);
  // the piece the pianola shows, when it has a score (the player's, else the test piano's library piece or recording)
  const playingId = (player && PLAYER_BUSY.includes(player.state) ? player.current?.id : null) ?? piano?.piece ?? player?.current?.id ?? null;
  const scorePiece = playingId && /^(lib|user|rec|file):/.test(playingId) ? playingId : null;
  const used = here.instrumentUsed ?? sound.instrument;
  const soundLabel = sound.instruments.find((i) => i.id === used)?.label ?? null;

  return (
    <>
      {/* the bar sits over the page's foot: the page gets the room back under it */}
      {here.on && !onScore && !docked && <div aria-hidden className="h-24" />}
      {here.on && !here.viewOpen && !onScore && !docked && (
        <div className="fixed inset-x-0 bottom-0 z-40 px-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]" data-testid="listen-here-bar">
          <div className={cn(CARD_CLASS, "mx-auto flex w-full max-w-3xl items-center gap-2 px-3 py-2")}>
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
        </div>
      )}
      {/* opened by a tap (Open, or Watch: Pianola), so on a score page too; only the auto-open is held back there */}
      {here.viewOpen && (
        <LiveMusicView
          feed={feed}
          title={title}
          sub={sub}
          soundLabel={soundLabel}
          line={<ListenLine big />}
          bar={
            // the same play bar as the score's; Watch: Score opens this piece's score full screen (1c, owner 23 Oct)
            <PlayBar
              baseUrl={api.baseUrl}
              compact
              watching="pianola"
              onWatch={
                scorePiece
                  ? (w) => {
                      if (w !== "score") return;
                      setListenView(false);
                      navigate(`/music?score=${encodeURIComponent(scorePiece)}&stand=1`, { state: { score: true, from: "Room" } });
                    }
                  : undefined
              }
            />
          }
        />
      )}
    </>
  );
}
