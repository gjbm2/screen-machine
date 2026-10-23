// Test music: pretend someone's playing, so the room can be tried from another room.
// With show_service reachable (GET /show/api/v1/test, through room's /show pass-through), it plays through the show
// engine, which hears it as it hears the room, so the screens, lamps and beat clock all follow:
//   Music - recorded tracks (show_service's test audio: CC-licensed, across genres)
//   Piano - the test piano (stream_service/piano_replay.py's pieces, sent as the Steinway's MIDI would be)
// Without it (until the pass-through ships), the older pair:
//   Band  - room_service's fake Art-Net (dmx/fake_artnet.py): a made-up song sent to the screens (show bus)
//           and the lamps (streamed to the bridge) the way a lighting desk does. Real Art-Net stops it.
//   Piano - the renderer's test piano (stream_service/piano_replay.py): a piece played into every screen's
//           sound view exactly as the Steinway's MIDI arrives, so the piano scenes (Piano mode) follow it.
//           Pieces are built-in phrases plus any .mid file dropped in config/live/test-piano/.
// One plays at a time: starting one stops the other.

import { useCallback, useEffect, useRef, useState } from "react";
import { Guitar, Minus, Music, Piano, Play, Plus, SkipForward, Square } from "lucide-react";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import { explainError, type RoomApi } from "./api";
import { Pill, Spinner } from "./bits";
import { CARD_CLASS, TAP_BUTTON_CLASS } from "./theme";
import type { ShowTestSource, ShowTestStartOpts, ShowTestView, TestPianoView, TestShowView } from "./types";
import type { NewLogEntry } from "./useRoomConnection";
import { PlayStatus, SoundChoice } from "./PianoCard";
import { SpeedNudges } from "./SpeedNudges";
import { usePianoSound } from "./usePianoSound";

const POLL_RUNNING_MS = 1000;
const POLL_IDLE_MS = 3000;

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

type Source = "band" | "piano";

/** Poll a view: fast while it plays, slow while idle, only while the page is visible. */
function usePolled<T extends { running?: boolean }>(load: (s: AbortSignal) => Promise<T | null>) {
  const [view, setView] = useState<T | null>(null);
  const [missing, setMissing] = useState(false);
  const running = useRef(false);
  running.current = !!view?.running;
  useEffect(() => {
    let stop = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const ac = new AbortController();
    const poll = async () => {
      if (document.visibilityState === "visible") {
        try {
          const v = await load(ac.signal);
          if (stop) return;
          if (v === null) setMissing(true);
          else {
            setMissing(false);
            setView(v);
          }
        } catch {
          // the Lights section above reports a lost connection; keep the last view
        }
      }
      if (!stop) timer = setTimeout(poll, running.current ? POLL_RUNNING_MS : POLL_IDLE_MS);
    };
    void poll();
    return () => {
      stop = true;
      ac.abort();
      clearTimeout(timer);
    };
  }, [load]);
  return { view, setView, missing };
}

const CHIP = "min-h-11 rounded-xl border px-3 text-base font-medium transition-colors disabled:cursor-default";
const CHIP_ON = "border-teal-300 bg-teal-50 text-teal-900";
const CHIP_OFF = "border-stone-200 bg-white hover:bg-stone-50";

// "preparing": the piano's chosen sound is being got ready before it plays (it counts as playing: Stop, fast polls)
const SHOW_RUNNING = ["preparing", "starting", "playing"];
type ShowPolled = ShowTestView & { running: boolean };

/** Test music through show_service: Music or Piano, one at a time. */
function ShowTestCard({
  api,
  connected,
  liveNow,
  view,
  setView,
  addLog,
  reportError,
  stopOthers,
}: {
  api: RoomApi;
  connected: boolean;
  liveNow: boolean;
  view: ShowPolled;
  setView: (v: ShowPolled) => void;
  addLog: (e: NewLogEntry) => void;
  reportError: (e: unknown) => void;
  stopOthers: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [picked, setPicked] = useState<ShowTestSource | null>(null);
  const [pick, setPick] = useState<Partial<Record<ShowTestSource, string>>>({});
  const on = (src: ShowTestSource) => SHOW_RUNNING.includes(view.sources[src]?.state ?? "off");
  const playingSrc: ShowTestSource | null = on("audio") ? "audio" : on("piano") ? "piano" : null;
  const source: ShowTestSource = playingSrc ?? picked ?? "audio";
  const pieces = view.pieces[source] ?? [];
  const pieceNow = on(source) ? view.sources[source].piece : (pick[source] ?? pieces[0]?.id ?? null);
  const label = (src: ShowTestSource, id: string | null | undefined) =>
    (view.pieces[src] ?? []).find((x) => x.id === id)?.label ?? view.sources[src]?.label ?? id ?? "";
  const failed = view.sources[source]?.state === "failed" ? view.sources[source]?.error : null;
  // the piano's sound and output (shared with the Piano card); none of it without show_service's instruments
  const sound = usePianoSound(view);

  const refresh = async () => {
    const v = await api.showTest();
    if (v) setView({ ...v, running: Object.values(v.sources).some((x) => SHOW_RUNNING.includes(x.state)) });
  };
  const act = async (what: string, fn: () => Promise<unknown>) => {
    setBusy(true);
    setErr(null);
    try {
      await fn();
      await refresh();
      addLog({ type: "fixture", level: "info", summary: `Test music: ${what}` });
    } catch (e) {
      reportError(e);
      setErr(explainError(e));
    } finally {
      setBusy(false);
    }
  };
  const start = (src: ShowTestSource, id: string, opts?: ShowTestStartOpts) =>
    act(label(src, id) || src, async () => {
      await stopOthers();
      const other: ShowTestSource = src === "audio" ? "piano" : "audio";
      if (on(other)) await api.showTestStop(other);
      await api.showTestStart(src, id, src === "piano" ? (opts ?? sound.opts) : undefined);
    });
  // a new sound or output while the piano plays: the piece starts again with it (as picking a piece does)
  const changeSound = (o: ShowTestStartOpts) => {
    if (o.instrument) sound.setInstrument(o.instrument);
    if (o.output) sound.setOutput(o.output);
    const now = view.sources.piano?.piece;
    if (on("piano") && now) void start("piano", now, { ...sound.opts, ...o });
  };
  // a new speed while the piano plays: the piece carries on from where it was, at the new speed
  const changeSpeed = (x: number) => {
    sound.setSpeed?.(x);
    const cur = view.sources.piano;
    if (on("piano") && cur?.piece) void start("piano", cur.piece, { ...sound.opts, speed: x, start_s: cur.pos_s ?? 0 });
  };
  const stop = () => act("stop", async () => Promise.all((["audio", "piano"] as const).filter(on).map((x) => api.showTestStop(x))));
  const disabled = !connected || busy;

  return (
    <section aria-labelledby="room-test-music">
      <div className="mb-2 flex items-center justify-between gap-2">
        <h2 id="room-test-music" className="text-lg font-semibold text-stone-800">
          Test music
        </h2>
        <Pill tone={playingSrc ? "live" : "muted"} pulse={!!playingSrc}>
          {playingSrc ? `${playingSrc === "audio" ? "Music" : "Piano"} · ${label(playingSrc, view.sources[playingSrc].piece)}` : "Off"}
        </Pill>
      </div>
      <div className={cn(CARD_CLASS, "space-y-4 px-3 py-3 sm:px-4")}>
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            disabled={disabled || (!playingSrc && !pieceNow)}
            onClick={() => void (playingSrc ? stop() : pieceNow ? start(source, pieceNow) : undefined)}
            className={cn(
              TAP_BUTTON_CLASS,
              "inline-flex items-center gap-2 font-semibold disabled:opacity-60",
              playingSrc ? "border border-stone-300 bg-white hover:bg-stone-50" : "bg-primary text-primary-foreground hover:bg-primary/90",
            )}
          >
            {busy ? <Spinner /> : playingSrc ? <Square className="h-4 w-4" aria-hidden /> : <Play className="h-4 w-4" aria-hidden />}
            {playingSrc ? "Stop" : source === "audio" ? "Play the music" : "Play the piano"}
          </button>
          <p className="min-w-0 flex-1 text-sm text-muted-foreground">
            Pretend someone's playing: the show hears it as it hears the room.
          </p>
        </div>

        <div role="radiogroup" aria-label="What plays" className="grid grid-cols-2 gap-2">
          {(
            [
              ["audio", "Music", Music],
              ["piano", "Piano", Piano],
            ] as const
          ).map(([id, text, Icon]) => {
            const sel = id === source;
            return (
              <button
                key={id}
                type="button"
                role="radio"
                aria-checked={sel}
                disabled={disabled || sel || !(view.pieces[id] ?? []).length}
                onClick={() => {
                  setPicked(id);
                  if (playingSrc && id !== playingSrc) {
                    const first = pick[id] ?? view.pieces[id]?.[0]?.id;
                    if (first) void start(id, first);
                  }
                }}
                className={cn(CHIP, "inline-flex items-center justify-center gap-2", sel ? CHIP_ON : CHIP_OFF, !sel && disabled && "opacity-60")}
              >
                <Icon className="h-4 w-4" aria-hidden /> {text}
              </button>
            );
          })}
        </div>

        {source === "piano" && (
          <SoundChoice
            sound={sound}
            disabled={disabled}
            onInstrument={(id) => changeSound({ instrument: id })}
          />
        )}
        {source === "piano" && sound.available && <SpeedNudges speed={sound.speed ?? 1} onSpeed={changeSpeed} disabled={disabled} />}

        <div role="radiogroup" aria-label={source === "audio" ? "Track" : "Piece"} className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {pieces.map((pc) => {
            const sel = pc.id === pieceNow;
            return (
              <button
                key={pc.id}
                type="button"
                role="radio"
                aria-checked={sel}
                disabled={disabled || (sel && on(source))}
                onClick={() => (on(source) ? void start(source, pc.id) : setPick({ ...pick, [source]: pc.id }))}
                className={cn(CHIP, "text-left", sel ? CHIP_ON : CHIP_OFF, !sel && disabled && "opacity-60")}
              >
                {pc.label}
                {pc.genre && <span className="ml-1.5 text-sm font-normal text-muted-foreground">· {pc.genre}</span>}
              </button>
            );
          })}
        </div>

        {source === "piano" && sound.available && <PlayStatus state={view.sources.piano} sound={sound} />}
        {playingSrc && view.sources[playingSrc]?.state !== "preparing" && (
          <ul className="space-y-1 text-base text-muted-foreground">
            <li>{liveNow ? "Screens: following it." : "Screens: showing art in this mode."}</li>
            <li>Lamps and the beat follow it as they would the room.</li>
          </ul>
        )}
        {failed && !(source === "piano" && sound.available) && <p className="text-sm text-amber-800">It didn't start: {failed}</p>}
        {err && <p className="text-sm text-rose-700">{err}</p>}
      </div>
    </section>
  );
}

export function TestShowSection({
  api,
  connected,
  liveNow,
  addLog,
  reportError,
}: {
  api: RoomApi;
  connected: boolean;
  liveNow: boolean;
  addLog: (e: NewLogEntry) => void;
  reportError: (e: unknown) => void;
}) {
  const fetchBand = useCallback((s: AbortSignal) => api.testShow(s), [api]);
  const fetchPiano = useCallback((s: AbortSignal) => api.testPiano(s), [api]);
  const fetchShow = useCallback(
    async (s: AbortSignal): Promise<ShowPolled | null> => {
      const v = await api.showTest(s);
      return v ? { ...v, running: Object.values(v.sources).some((x) => SHOW_RUNNING.includes(x.state)) } : null;
    },
    [api],
  );
  const band = usePolled<TestShowView>(fetchBand);
  const piano = usePolled<TestPianoView>(fetchPiano);
  const show = usePolled<ShowPolled>(fetchShow);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [picked, setPicked] = useState<Source | null>(null);
  const [piece, setPiece] = useState<string | null>(null); // the piano piece for the next start

  const song = band.view?.song;
  const bandOk = !band.missing && !!band.view?.available;
  const pianoOk = !piano.missing && !!piano.view?.available;
  const bandOn = !!band.view?.running;
  const pianoOn = !!piano.view?.running;
  const source: Source = bandOn ? "band" : pianoOn ? "piano" : (picked ?? (bandOk ? "band" : "piano"));

  const bpmNow = Math.round(song?.target_bpm ?? song?.bpm ?? 124);
  const [bpm, setBpm] = useState(bpmNow);
  const [style, setStyle] = useState<string | null>(null); // the band's style for the next start
  const dragging = useRef(false);
  useEffect(() => {
    if (!dragging.current) setBpm(bpmNow);
  }, [bpmNow]);

  const run = useCallback(
    async (what: string, fn: () => Promise<void>) => {
      setBusy(what);
      setErr(null);
      try {
        await fn();
        addLog({ type: "fixture", level: "info", summary: `Test music: ${what}` });
      } catch (e) {
        reportError(e);
        setErr(explainError(e));
      } finally {
        setBusy(null);
      }
    },
    [addLog, reportError],
  );
  const sendBand = useCallback(async (body: Record<string, unknown>) => band.setView(await api.testShowSend(body)), [api, band]);
  const sendPiano = useCallback(
    async (body: { on: boolean; piece?: string }) => piano.setView(await api.testPianoSend(body)),
    [api, piano],
  );

  if (show.view && !show.missing) {
    // the older sources must not play over it: the renderer's own test piano would double the notes
    const stopOthers = async () => {
      if (piano.view?.running) await api.testPianoSend({ on: false });
      if (band.view?.running) await api.testShowSend({ action: "stop" });
    };
    return (
      <ShowTestCard
        api={api}
        connected={connected}
        liveNow={liveNow}
        view={show.view}
        setView={show.setView}
        addLog={addLog}
        reportError={reportError}
        stopOthers={stopOthers}
      />
    );
  }
  const gone = (m: boolean, v: { available: boolean } | null) => m || v?.available === false;
  if (gone(band.missing, band.view) && gone(piano.missing, piano.view)) return null;

  const pieces = piano.view?.pieces ?? [];
  const pieceNow = pianoOn ? piano.view?.piece : (piece ?? pieces[0]?.id ?? "medley");
  const pieceLabel = (id: string | null | undefined) => pieces.find((p) => p.id === id)?.label ?? id ?? "";
  const bandStyle = bandOn ? (song?.pending_song?.song ?? song?.song) : (style ?? song?.song);
  const playing = bandOn || pianoOn;
  const disabled = !connected || !!busy;

  const startBand = () =>
    run("band", async () => {
      if (pianoOn) await sendPiano({ on: false });
      await sendBand({ action: "start", song: bandStyle, bpm });
    });
  const startPiano = (id: string) =>
    run(pieceLabel(id) || "piano", async () => {
      if (bandOn) await sendBand({ action: "stop" });
      await sendPiano({ on: true, piece: id });
    });
  const stopAll = () =>
    run("stop", async () => {
      if (bandOn) await sendBand({ action: "stop" });
      if (pianoOn) await sendPiano({ on: false });
    });
  const choose = (s: Source) => {
    setPicked(s);
    if (playing && s !== source) void (s === "band" ? startBand() : startPiano(pieceNow ?? "medley"));
  };

  const [lo, hi] = song?.bpm_range ?? [40, 200];
  const setTempo = (v: number) => {
    const b = Math.max(lo, Math.min(hi, Math.round(v)));
    setBpm(b);
    if (bandOn) void run(`${b} bpm`, () => sendBand({ bpm: b }));
  };
  const lamps = band.view?.lamps;
  const yielded = !bandOn && band.view?.stopped?.startsWith("real Art-Net");

  return (
    <section aria-labelledby="room-test-music">
      <div className="mb-2 flex items-center justify-between gap-2">
        <h2 id="room-test-music" className="text-lg font-semibold text-stone-800">
          Test music
        </h2>
        {(band.view || piano.view) && (
          <Pill tone={playing ? "live" : "muted"} pulse={playing}>
            {bandOn ? `Band · ${song?.section ?? ""}` : pianoOn ? `Piano · ${pieceLabel(piano.view?.piece)}` : "Off"}
          </Pill>
        )}
      </div>
      <div className={cn(CARD_CLASS, "space-y-4 px-3 py-3 sm:px-4")}>
        {!band.view && !piano.view ? (
          <p className="flex items-center gap-2 py-1 text-base text-muted-foreground">
            <Spinner /> Loading…
          </p>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-3">
              <button
                type="button"
                disabled={disabled || (source === "band" ? !bandOk : !pianoOk)}
                onClick={() => void (playing ? stopAll() : source === "band" ? startBand() : startPiano(pieceNow ?? "medley"))}
                className={cn(
                  TAP_BUTTON_CLASS,
                  "inline-flex items-center gap-2 font-semibold disabled:opacity-60",
                  playing ? "border border-stone-300 bg-white hover:bg-stone-50" : "bg-primary text-primary-foreground hover:bg-primary/90",
                )}
              >
                {busy ? <Spinner /> : playing ? <Square className="h-4 w-4" aria-hidden /> : <Play className="h-4 w-4" aria-hidden />}
                {playing ? "Stop" : source === "band" ? "Play the band" : "Play the piano"}
              </button>
              <p className="min-w-0 flex-1 text-sm text-muted-foreground">
                Pretend someone's playing, so the room can be tried from anywhere.
              </p>
            </div>

            {/* Source */}
            <div role="radiogroup" aria-label="What plays" className="grid grid-cols-2 gap-2">
              {(
                [
                  ["band", "Band", Guitar, bandOk],
                  ["piano", "Piano", Piano, pianoOk],
                ] as const
              ).map(([id, label, Icon, ok]) => {
                const on = id === source;
                return (
                  <button
                    key={id}
                    type="button"
                    role="radio"
                    aria-checked={on}
                    disabled={disabled || on || !ok}
                    onClick={() => choose(id)}
                    className={cn(CHIP, "inline-flex items-center justify-center gap-2", on ? CHIP_ON : CHIP_OFF, !on && (disabled || !ok) && "opacity-60")}
                  >
                    <Icon className="h-4 w-4" aria-hidden /> {label}
                  </button>
                );
              })}
            </div>

            {yielded && <p className="text-base text-amber-800">The band stopped: the real lighting desk started sending.</p>}

            {source === "band" && bandOk && (
              <>
                {/* Tempo */}
                <div className="flex min-h-11 items-center gap-2">
                  <span className="w-16 shrink-0 text-base font-medium">Tempo</span>
                  <button
                    type="button"
                    aria-label="Slower"
                    disabled={disabled || bpm <= lo}
                    onClick={() => setTempo(bpm - 1)}
                    className="grid h-11 w-11 shrink-0 place-items-center rounded-xl border border-stone-200 bg-white hover:bg-stone-50 disabled:opacity-50"
                  >
                    <Minus className="h-4 w-4" aria-hidden />
                  </button>
                  <Slider
                    aria-label="Tempo"
                    className="flex-1 py-3 [&_[role=slider]]:h-6 [&_[role=slider]]:w-6 [&_[role=slider]]:shadow-sm"
                    min={lo}
                    max={hi}
                    step={1}
                    value={[bpm]}
                    disabled={!connected}
                    onValueChange={([v]) => {
                      dragging.current = true;
                      setBpm(v);
                    }}
                    onValueCommit={([v]) => {
                      dragging.current = false;
                      setTempo(v);
                    }}
                  />
                  <button
                    type="button"
                    aria-label="Faster"
                    disabled={disabled || bpm >= hi}
                    onClick={() => setTempo(bpm + 1)}
                    className="grid h-11 w-11 shrink-0 place-items-center rounded-xl border border-stone-200 bg-white hover:bg-stone-50 disabled:opacity-50"
                  >
                    <Plus className="h-4 w-4" aria-hidden />
                  </button>
                  <span className="w-20 shrink-0 text-right font-mono text-[15px] tabular-nums text-muted-foreground">{bpm} bpm</span>
                </div>

                {/* Style */}
                <div>
                  <span className="flex items-center gap-1.5 text-base font-medium">
                    <Music className="h-4 w-4" aria-hidden /> Style
                  </span>
                  <div role="radiogroup" aria-label="Style" className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
                    {(song?.songs ?? []).map((s) => {
                      const on = s === bandStyle;
                      return (
                        <button
                          key={s}
                          type="button"
                          role="radio"
                          aria-checked={on}
                          disabled={disabled || on}
                          onClick={() => (bandOn ? void run(s, () => sendBand({ song: s })) : setStyle(s))}
                          className={cn(CHIP, on ? CHIP_ON : CHIP_OFF, !on && disabled && "opacity-60")}
                        >
                          {cap(s)}
                        </button>
                      );
                    })}
                  </div>
                  {bandOn && song?.pending_song && (
                    <p className="mt-1 text-sm text-muted-foreground">{cap(song.pending_song.song)} starts at the next bar.</p>
                  )}
                </div>

                {bandOn && (
                  <div className="flex flex-wrap items-center gap-3">
                    <label className="flex min-h-11 flex-1 items-center justify-between gap-3 rounded-xl border border-stone-200 px-3">
                      <span>
                        <span className="block text-base font-medium">Beat</span>
                        <span className="block text-sm text-muted-foreground">Off is a breakdown</span>
                      </span>
                      <Switch
                        aria-label="Beat"
                        checked={song?.pulses !== false}
                        disabled={disabled}
                        onCheckedChange={(on) => void run(on ? "beat on" : "breakdown", () => sendBand({ pulses: on }))}
                      />
                    </label>
                    <button
                      type="button"
                      disabled={disabled}
                      onClick={() => void run("next section", () => sendBand({ next_section: true }))}
                      className={cn(TAP_BUTTON_CLASS, "inline-flex items-center gap-2 border border-stone-200 bg-white hover:bg-stone-50 disabled:opacity-60")}
                    >
                      <SkipForward className="h-4 w-4" aria-hidden /> Next section
                    </button>
                  </div>
                )}

                {bandOn && (
                  <ul className="space-y-1 text-base text-muted-foreground">
                    <li>
                      {cap(song?.section ?? "")} · bar {song?.bar ?? 0}
                    </li>
                    <li>
                      {lamps && lamps.following > 0
                        ? `Lamps: ${lamps.following} of ${lamps.total} following`
                        : lamps && lamps.armed > 0
                          ? "Lamps: connecting…"
                          : "Lamps: not following in this mode."}
                    </li>
                    <li>
                      {!band.view?.renderers
                        ? "Screens: the renderer isn't listening."
                        : liveNow
                          ? "Screens: following the show."
                          : "Screens: showing art in this mode."}
                    </li>
                  </ul>
                )}
              </>
            )}

            {source === "piano" && pianoOk && (
              <>
                <div>
                  <span className="flex items-center gap-1.5 text-base font-medium">
                    <Piano className="h-4 w-4" aria-hidden /> Piece
                  </span>
                  <div role="radiogroup" aria-label="Piece" className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
                    {pieces.map((p) => {
                      const on = p.id === pieceNow;
                      return (
                        <button
                          key={p.id}
                          type="button"
                          role="radio"
                          aria-checked={on}
                          disabled={disabled || (on && pianoOn)}
                          onClick={() => (pianoOn ? void startPiano(p.id) : setPiece(p.id))}
                          className={cn(CHIP, "text-left", on ? CHIP_ON : CHIP_OFF, !on && disabled && "opacity-60")}
                        >
                          {p.label}
                        </button>
                      );
                    })}
                  </div>
                </div>
                {pianoOn && (
                  <ul className="space-y-1 text-base text-muted-foreground">
                    <li>{liveNow ? "Screens: piano scenes follow it (in Piano mode they're the only ones)." : "Screens: showing art in this mode."}</li>
                    <li>Lamps: not yet. They'll follow the piano once the show engine hears the room.</li>
                  </ul>
                )}
              </>
            )}

            {err && <p className="text-sm text-rose-700">{err}</p>}
          </>
        )}
      </div>
    </section>
  );
}
