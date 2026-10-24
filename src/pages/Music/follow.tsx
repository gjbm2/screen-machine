// Follow along on a score (owner, 23 Oct: "basic play controls on the score page ... so that user can play, jump
// back/forward etc. and the currently playing bar / notes etc. are lit up"; "we already HAVE play features ... expose
// them properly on this score page"). Nothing here plays anything: the controls are the Music page's player (its
// play / pause / seek, falling back to play-from and stop on an older one), and the light follows its place in the
// piece (elapsed_s, the piece's own seconds), carried on between its polls at its speed, and held back by the lag of
// this device's own piano stream while it plays it (Room/deviceLag.ts).
//
//   useFollow     the score's timings for a layout (GET /score/<id>/follow: each bar's start and end, each note's onset,
//                 each page's bars) and the playhead: is this piece playing, where it is now, the bar and the notes
//                 just struck, the page that bar is on
//   FollowLight   over one page image: a soft band on the bar playing and a glow on the notes just struck (the page's
//                 bar boxes and note heads, GET /score/<id>/page/<n>.json, fetched once a page has the light)
//   Transport     ⏮ ◀ ⏯ ▶: back to the start, a bar back, play / pause, a bar on; play from the page in view when
//                 nothing plays (the pianola's and the stand's play bar)
//   useScoreTransport  the same, as parts for the app's shared Transport (components/player/Transport.tsx): its bar
//                 steps either side of Play, and its Play (the score's docked bar)
//   BarBand       over one page image: an amber band on each bar chosen for Record › These bars, and (`onPick`) a tap
//                 target on every bar, so a tap on the score chooses the bars
//
// A tap answers at once, whatever the sound's delay (owner, 23 Oct: "the interface still needs to be sensibly
// responsive (even if there's a delay after I press play)"): Pause freezes the light where it is (what was heard) and
// the button turns to Play; Play, a bar back or on, and back to the start put the light on that bar straight away,
// "Starting…", and the button turns to Pause (a second tap stops it again) until the sound gets there. Carry on plays
// from where the light stopped (a seek: what this device heard, not the player's own place, which runs ahead by the
// stream's delay).
//
// When this device plays the piano itself over its own buffer (Room/deviceAudio.ts), the light is what it hears:
// the place in the piece of the very frame playing (exact, no estimate), and Pause silences it at once.

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Pause, Play, SkipBack, StepBack, StepForward } from "lucide-react";
import type { TransportPlay } from "@/components/player/Transport";
import { cn } from "@/lib/utils";
import type { MusicApi, PlayerAction, ScoreFollow, ScoreGeometry, ScoreKind, ScoreSize } from "./api";
import { useDeviceLag } from "@/pages/Room/deviceLag";
import { heardPiece, heardT, hostNow, hushDeviceAudio, unhushDeviceAudio, useDeviceAudio } from "@/pages/Room/deviceAudio";
import { useMusic } from "./context";

const TICK_MS = 80;
/** A note glows this long after its onset (the piece's own seconds). */
const GLOW_S = 0.35;
/** The light is drawn this far ahead of the reported place: what's heard lags the MIDI a little less than the poll. */
const LEAD_S = 0.05;

export interface Playhead {
  /** This piece is the one the player is on and it's playing. */
  playing: boolean;
  /** This piece is the player's (playing, preparing or paused). */
  mine: boolean;
  /** Where it is (the piece's own seconds), or null. */
  pos: number | null;
  /** The bar playing (its number), or null. */
  bar: number | null;
  /** Note ids just struck. */
  notes: Set<number>;
  /** The page (1-based) the bar playing is on, or null. */
  page: number | null;
  /** Asked to play from ``pos``; the sound isn't there yet (the player starting it, or this device's delay). */
  starting: boolean;
}

const NO_HEAD: Playhead = { playing: false, mine: false, pos: null, bar: null, notes: new Set(), page: null, starting: false };

/** A tap not yet borne out by the player: play (from pos) or pause (frozen at pos). */
type Intent = { kind: "play" | "pause"; pos: number; at: number; answered?: boolean };
/** A tap's answer is waited for this long; then the player's own view wins again. */
const INTENT_MS = 20_000;

/** The last index i with key(xs[i]) <= v, or -1. */
function lastAtOrBefore<T>(xs: T[], v: number, key: (x: T) => number): number {
  let lo = 0;
  let hi = xs.length - 1;
  let out = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (key(xs[mid]) <= v) {
      out = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  return out;
}

export function useFollow(pieceId: string, layout: (ScoreSize & { kind?: ScoreKind; rev?: string }) | null) {
  const { api, player } = useMusic();
  const [data, setData] = useState<ScoreFollow | null>(null);
  const key = layout ? `${layout.w}x${layout.h}@${layout.staff}/${layout.kind}/${layout.rev}` : "";

  useEffect(() => {
    if (!layout) return;
    const ac = new AbortController();
    api
      .scoreFollow(pieceId, layout, ac.signal)
      .then((d) => setData(d))
      .catch(() => undefined); // an older show_service: no light, the controls still work
    return () => ac.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, pieceId, key]);

  // the player's place, carried on between its polls at its speed
  const v = player.view;
  const mine = !!v && v.current?.id === pieceId && ["playing", "preparing", "paused"].includes(v.state);
  const playing = !!v && v.current?.id === pieceId && v.state === "playing";
  const elapsed = mine && typeof v?.elapsed_s === "number" ? v.elapsed_s : null;
  const speed = v?.speed ?? 1;
  // when this place was reported (each poll's): the light carries on from it
  // (at_ms, a newer player's: when elapsed_s was true by the wall clock; else when this view arrived)
  const atMs = v?.at_ms;
  const base = useMemo(
    () => (elapsed === null ? null : { pos: elapsed, at: performance.now() - (typeof atMs === "number" ? Math.min(5000, Math.max(0, Date.now() - atMs)) : 0), speed }),
    [elapsed, speed, atMs, v],
  );
  const device = useDeviceAudio();
  const hearing = device.status === "playing";
  const [now, setNow] = useState(() => performance.now());
  useEffect(() => {
    if (!playing && !hearing) return;
    const t = setInterval(() => setNow(performance.now()), TICK_MS);
    return () => clearInterval(t);
  }, [playing, hearing]);

  // a tap, shown at once (module comment); the player's view takes over once it bears it out
  const [intent, setIntent] = useState<Intent | null>(null);
  // where the last play started: the light never shows earlier than that (the sound's delay would pull it back)
  const anchor = useRef<{ pos: number; until: number; host: number | null } | null>(null);
  useEffect(() => {
    if (!intent?.answered) return; // the views from before the player's answer say nothing about the tap
    if (intent.kind === "play" && playing) setIntent(null);
    else if (intent.kind === "pause" && v && !["playing", "preparing", "paused"].includes(v.state)) setIntent(null);
    else if (intent.kind === "pause" && (v?.state === "playing" || v?.state === "preparing")) setIntent(null);
  }, [intent, playing, v]);
  useEffect(() => {
    if (!intent || (intent.kind === "pause" && v?.state === "paused")) return; // a pause holds while it's paused
    const t = setTimeout(() => setIntent(null), INTENT_MS);
    return () => clearTimeout(t);
  }, [intent, v?.state]);
  useEffect(() => {
    if (!intent) return;
    const t = setInterval(() => setNow(performance.now()), 250);
    return () => clearInterval(t);
  }, [intent]);

  // this device playing the piano itself hears it later than the room: the light waits for what it hears
  const lag = useDeviceLag();
  const head: Playhead = useMemo(() => {
    const at = (p: number, live: boolean, starting: boolean): Playhead => {
      if (!data) return { ...NO_HEAD, mine: true, playing: live, pos: p, starting };
      const bi = lastAtOrBefore(data.bars, p, (x) => x[1]);
      const bar = bi >= 0 ? data.bars[bi][0] : (data.bars[0]?.[0] ?? null);
      const notes = new Set<number>();
      if (live) {
        const ni = lastAtOrBefore(data.notes, p, (x) => x[1]);
        for (let i = ni; i >= 0 && data.notes[i][1] > p - GLOW_S; i--) notes.add(data.notes[i][0]);
      }
      const page = bar === null ? null : data.pages.findIndex(([a, z]) => a <= bar && bar <= z) + 1 || null;
      return { playing: live, mine: true, pos: p, bar, notes, page, starting };
    };
    if (intent?.kind === "pause") return at(intent.pos, false, false);
    if (intent?.kind === "play") return at(intent.pos, false, true);
    const a0 = anchor.current && performance.now() < anchor.current.until ? anchor.current : null;
    // this device's own sound: the place of the frame being heard
    const heard = hearing ? heardPiece() : null;
    if (heard && heard.piece === pieceId && mine) {
      const t = heardT();
      if (a0 && a0.host !== null && t !== null && t < a0.host) return at(a0.pos, false, true);   // the old sound
      return at(heard.pos, heard.playing && playing, false);
    }
    const b = base;
    if (!mine || !b) return { ...NO_HEAD, mine };
    const raw = Math.max(0, b.pos + (playing ? (Math.max(0, now - b.at) / 1000) * b.speed : 0) + (playing ? LEAD_S : 0) - (playing && lag ? lag * b.speed : 0));
    const a = anchor.current && performance.now() < anchor.current.until ? anchor.current.pos : null;
    const waiting = (playing && a !== null && raw < a) || v?.state === "preparing";
    const pos = a !== null && v?.state !== "paused" ? Math.max(a, raw) : raw;
    return at(pos, playing && !waiting, waiting);
  }, [mine, playing, now, data, base, lag, intent, v, hearing, pieceId]);

  const barStart = useCallback(
    (n: number): number | null => {
      const b = data?.bars.find((x) => x[0] === n);
      return b ? b[1] : null;
    },
    [data],
  );
  const firstBarOnPage = useCallback((p: number): number | null => data?.pages[p - 1]?.[0] ?? null, [data]);

  // the player's actions, with an older player's fallbacks (no pause / seek / start_s: stop, and play from the top)
  const lastPos = useRef<number | null>(null);
  useEffect(() => {
    if (head.pos !== null) lastPos.current = head.pos;
  }, [head.pos]);
  const act = useCallback(
    async (body: PlayerAction, fallback?: PlayerAction) => {
      const r = await player.act(body);
      if (!r.ok && fallback && /bad_action|no action|start_s/i.test(r.words)) return player.act(fallback);
      return r;
    },
    [player],
  );
  const shown = useCallback(async (it: Intent, run: () => Promise<{ ok: boolean }>) => {
    setIntent(it);
    if (it.kind === "pause") hushDeviceAudio(); // silent here at once, whatever the round trip
    else unhushDeviceAudio();
    if (it.kind === "play") anchor.current = { pos: it.pos, until: performance.now() + 15_000, host: hostNow() };
    const r = await run();
    setIntent((i) => (i && i.at === it.at ? (r.ok ? { ...i, answered: true } : null) : i));
    return r;
  }, []);
  const playFrom = useCallback(
    (t: number) =>
      shown({ kind: "play", pos: Math.max(0, t), at: Date.now() }, () =>
        act(mine ? { action: "seek", start_s: Math.max(0, t) } : { action: "play", piece: pieceId, start_s: Math.max(0, t) }, { action: "play", piece: pieceId, start_s: Math.max(0, t) }),
      ),
    [act, mine, pieceId, shown],
  );
  const pause = useCallback(
    () => shown({ kind: "pause", pos: lastPos.current ?? 0, at: Date.now() }, () => act({ action: "pause" }, { action: "stop" })),
    [act, shown],
  );
  /** Carry on from where the light stopped (what was heard), else the player's own resume. */
  const resume = useCallback(
    () => (lastPos.current !== null ? playFrom(lastPos.current) : act({ action: "resume" }, { action: "play", piece: pieceId, start_s: 0 })),
    [act, pieceId, playFrom],
  );

  return { data, head, barStart, firstBarOnPage, playFrom, pause, resume, busy: player.busy };
}

export type Follow = ReturnType<typeof useFollow>;

// page geometry, fetched once per page and layout
const geoCache = new Map<string, Promise<ScoreGeometry | null>>();

function useGeometry(api: MusicApi, pieceId: string, n: number, layout: ScoreSize & { kind?: ScoreKind; rev?: string }, want: boolean) {
  const [geo, setGeo] = useState<ScoreGeometry | null>(null);
  const key = `${pieceId}|${n}|${layout.w}x${layout.h}@${layout.staff}/${layout.kind}/${layout.rev}`;
  useEffect(() => {
    if (!want) return;
    let live = true;
    let p = geoCache.get(key);
    if (!p) {
      p = api.scoreGeometry(pieceId, n, layout).catch(() => null);
      geoCache.set(key, p);
      if (geoCache.size > 400) geoCache.delete(geoCache.keys().next().value as string);
    }
    void p.then((g) => {
      if (live) setGeo(g);
    });
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, want]);
  return want ? geo : null;
}

/** The light over page ``n`` (absolutely placed over its image: put it in the image's relative box). */
export function FollowLight({
  pieceId,
  n,
  layout,
  head,
}: {
  pieceId: string;
  n: number;
  layout: ScoreSize & { kind?: ScoreKind; rev?: string };
  head: Playhead;
}) {
  const { api } = useMusic();
  const here = head.mine && head.page === n;
  const geo = useGeometry(api, pieceId, n, layout, here);
  if (!here || !geo) return null;
  const box = geo.bars.find((b) => b[0] === head.bar);
  const pad = 0.012;
  return (
    <div aria-hidden className="pointer-events-none absolute inset-0">
      {box && (
        <div
          className={cn(
            "absolute rounded-md transition-[left,top,width,height] duration-150",
            head.playing ? "bg-teal-400/15 ring-2 ring-teal-500/40" : "bg-amber-300/20 ring-2 ring-amber-400/50",
            head.starting && "motion-safe:animate-pulse",
          )}
          style={{
            left: `${box[1] * 100}%`,
            top: `${Math.max(0, box[2] - pad) * 100}%`,
            width: `${(box[3] - box[1]) * 100}%`,
            height: `${(box[4] - box[2] + 2 * pad) * 100}%`,
          }}
        />
      )}
      {head.notes.size > 0 &&
        geo.heads
          .filter((h) => head.notes.has(h[0]))
          .map((h) => (
            <div
              key={h[0]}
              className="absolute h-[2.2%] w-[1.6%] -translate-x-1/2 -translate-y-1/2 rounded-full bg-teal-500/55 shadow-[0_0_10px_3px_rgba(20,184,166,0.55)]"
              style={{ left: `${h[1] * 100}%`, top: `${h[2] * 100}%`, minWidth: 10, minHeight: 10 }}
            />
          ))}
    </div>
  );
}

const ROUND = "grid h-11 w-11 shrink-0 place-items-center rounded-full text-stone-700 hover:bg-stone-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-40";

/** The score's play logic: a bar back or on (from the page in view when nothing plays here), and Play / Pause / Carry
 *  on / Play from this page. */
function useScoreMoves(follow: Follow, pageInView: number) {
  const { head, data, barStart, firstBarOnPage, playFrom, pause, resume } = follow;
  const bars = data?.bars ?? [];
  const idx = head.bar !== null ? bars.findIndex((b) => b[0] === head.bar) : -1;
  const startOfPage = () => {
    const b = firstBarOnPage(pageInView);
    return b !== null ? (barStart(b) ?? 0) : 0;
  };
  const step = (d: number) => {
    if (idx < 0) return void playFrom(startOfPage());
    const j = Math.min(Math.max(0, idx + d), bars.length - 1);
    void playFrom(bars[j][1]);
  };
  const going = head.playing || head.starting;
  const toggle = () => {
    if (going) void pause();
    else if (head.mine) void resume();
    else void playFrom(startOfPage());
  };
  const label = going ? "Pause" : head.mine ? "Carry on" : "Play from this page";
  return { step, toggle, going, label, starting: head.starting, ready: !!data };
}

/** The score's parts for the shared Transport: its bar steps (either side of Play) and its Play. */
export function useScoreTransport(follow: Follow, pageInView: number): { steps: { back: ReactNode; on: ReactNode }; play: TransportPlay } {
  const m = useScoreMoves(follow, pageInView);
  return {
    steps: {
      back: (
        <button type="button" aria-label="A bar back" title="A bar back" disabled={!m.ready} onClick={() => m.step(-1)} className={ROUND}>
          <StepBack className="h-5 w-5" aria-hidden />
        </button>
      ),
      on: (
        <button type="button" aria-label="A bar on" title="A bar on" disabled={!m.ready} onClick={() => m.step(1)} className={ROUND}>
          <StepForward className="h-5 w-5" aria-hidden />
        </button>
      ),
    },
    play: { going: m.going, starting: m.starting, label: m.label, onClick: m.toggle },
  };
}

/** ⏮ ◀ ⏯ ▶ for the score: the player's, on this piece. ``pageInView``: where Play starts when nothing plays. */
export function Transport({ follow, pageInView, compact }: { follow: Follow; pageInView: number; compact?: boolean }) {
  const { head, data, playFrom } = follow;
  const m = useScoreMoves(follow, pageInView);
  return (
    <div className={cn("flex items-center", compact ? "gap-0" : "gap-1")} role="group" aria-label="Play">
      <button type="button" aria-label="Back to the start" title="Back to the start" onClick={() => void playFrom(0)} className={ROUND}>
        <SkipBack className="h-5 w-5" aria-hidden />
      </button>
      <button type="button" aria-label="A bar back" title="A bar back" disabled={!data} onClick={() => m.step(-1)} className={ROUND}>
        <StepBack className="h-5 w-5" aria-hidden />
      </button>
      <button
        type="button"
        aria-label={m.label}
        title={m.label}
        onClick={m.toggle}
        className={cn(ROUND, "relative h-12 w-12 bg-teal-700 text-white hover:bg-teal-800")}
      >
        {head.starting && <span aria-hidden className="absolute inset-0 rounded-full border-2 border-white/70 border-t-transparent motion-safe:animate-spin" />}
        {m.going ? <Pause className="h-5 w-5" aria-hidden /> : <Play className="h-5 w-5 translate-x-px" aria-hidden />}
      </button>
      <button type="button" aria-label="A bar on" title="A bar on" disabled={!data} onClick={() => m.step(1)} className={ROUND}>
        <StepForward className="h-5 w-5" aria-hidden />
      </button>
      {!compact && head.mine && head.bar !== null && (
        <span className="ml-1 text-sm tabular-nums text-muted-foreground" aria-live="polite">
          {head.starting ? `Starting at bar ${head.bar}…` : `bar ${head.bar}`}
        </span>
      )}
    </div>
  );
}

/** Record › These bars over page ``n``: an amber band on each chosen bar (``from``..``to``), and with ``onPick`` a tap
 *  target on every bar of the page (the first tap sets From, the next To: the caller decides). Put it in the image's
 *  relative box, after the image. Its taps stay its own (the page turner and the stand's tap never see them). */
export function BarBand({
  pieceId,
  n,
  layout,
  from,
  to,
  onPick,
}: {
  pieceId: string;
  n: number;
  layout: ScoreSize & { kind?: ScoreKind; rev?: string };
  from: number | null;
  to: number | null;
  onPick?: (bar: number) => void;
}) {
  const { api } = useMusic();
  const geo = useGeometry(api, pieceId, n, layout, true);
  if (!geo) return null;
  const pad = 0.012;
  const inRange = (b: number) => from !== null && to !== null && b >= from && b <= to;
  const stop = (e: { stopPropagation: () => void }) => e.stopPropagation();
  return (
    <div aria-hidden={!onPick} className="pointer-events-none absolute inset-0" data-testid={`bar-band-${n}`}>
      {geo.bars.map((b, i) => {
        const chosen = inRange(b[0]);
        if (!chosen && !onPick) return null;
        const style = {
          left: `${b[1] * 100}%`,
          top: `${Math.max(0, b[2] - pad) * 100}%`,
          width: `${(b[3] - b[1]) * 100}%`,
          height: `${(b[4] - b[2] + 2 * pad) * 100}%`,
        };
        const cls = cn("absolute rounded-md", chosen ? "bg-amber-300/30 ring-2 ring-amber-500/60" : "");
        return onPick ? (
          <button
            key={`${b[0]}-${i}`}
            type="button"
            aria-label={`Bar ${b[0]}`}
            aria-pressed={chosen}
            onPointerDown={stop}
            onPointerUp={stop}
            onClick={(e) => {
              e.stopPropagation();
              onPick(b[0]);
            }}
            className={cn(cls, "pointer-events-auto cursor-pointer hover:bg-amber-200/25 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-500")}
            style={style}
          />
        ) : (
          <div key={`${b[0]}-${i}`} className={cls} style={style} />
        );
      })}
    </div>
  );
}
