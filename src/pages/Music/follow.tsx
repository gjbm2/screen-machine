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
//                 nothing plays

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Pause, Play, SkipBack, StepBack, StepForward } from "lucide-react";
import { cn } from "@/lib/utils";
import type { MusicApi, PlayerAction, ScoreFollow, ScoreGeometry, ScoreKind, ScoreSize } from "./api";
import { useDeviceLag } from "@/pages/Room/deviceLag";
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
}

const NO_HEAD: Playhead = { playing: false, mine: false, pos: null, bar: null, notes: new Set(), page: null };

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
  const [now, setNow] = useState(() => performance.now());
  useEffect(() => {
    if (!playing) return;
    const t = setInterval(() => setNow(performance.now()), TICK_MS);
    return () => clearInterval(t);
  }, [playing]);

  // this device playing the piano itself hears it later than the room: the light waits for what it hears
  const lag = useDeviceLag();
  const head: Playhead = useMemo(() => {
    const b = base;
    if (!mine || !b) return { ...NO_HEAD, mine };
    const pos = Math.max(0, b.pos + (playing ? (Math.max(0, now - b.at) / 1000) * b.speed : 0) + (playing ? LEAD_S : 0) - (playing && lag ? lag * b.speed : 0));
    if (!data) return { ...NO_HEAD, mine, playing, pos };
    const bi = lastAtOrBefore(data.bars, pos, (x) => x[1]);
    const bar = bi >= 0 ? data.bars[bi][0] : (data.bars[0]?.[0] ?? null);
    const notes = new Set<number>();
    if (playing) {
      const ni = lastAtOrBefore(data.notes, pos, (x) => x[1]);
      for (let i = ni; i >= 0 && data.notes[i][1] > pos - GLOW_S; i--) notes.add(data.notes[i][0]);
    }
    const page = bar === null ? null : data.pages.findIndex(([a, z]) => a <= bar && bar <= z) + 1 || null;
    return { playing, mine, pos, bar, notes, page };
  }, [mine, playing, now, data, base, lag]);

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
  const playFrom = useCallback(
    (t: number) => act(mine ? { action: "seek", start_s: t } : { action: "play", piece: pieceId, start_s: t }, { action: "play", piece: pieceId, start_s: t }),
    [act, mine, pieceId],
  );
  const pause = useCallback(() => act({ action: "pause" }, { action: "stop" }), [act]);
  const resume = useCallback(
    () => act({ action: "resume" }, { action: "play", piece: pieceId, start_s: lastPos.current ?? 0 }),
    [act, pieceId],
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
          className={cn("absolute rounded-md transition-[left,top,width,height] duration-150", head.playing ? "bg-teal-400/15 ring-2 ring-teal-500/40" : "bg-amber-300/20 ring-2 ring-amber-400/50")}
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

/** ⏮ ◀ ⏯ ▶ for the score: the player's, on this piece. ``pageInView``: where Play starts when nothing plays. */
export function Transport({ follow, pageInView, compact }: { follow: Follow; pageInView: number; compact?: boolean }) {
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
  const toggle = () => {
    if (head.playing) void pause();
    else if (head.mine) void resume();
    else void playFrom(startOfPage());
  };
  return (
    <div className={cn("flex items-center", compact ? "gap-0" : "gap-1")} role="group" aria-label="Play">
      <button type="button" aria-label="Back to the start" title="Back to the start" onClick={() => void playFrom(0)} className={ROUND}>
        <SkipBack className="h-5 w-5" aria-hidden />
      </button>
      <button type="button" aria-label="A bar back" title="A bar back" disabled={!data} onClick={() => step(-1)} className={ROUND}>
        <StepBack className="h-5 w-5" aria-hidden />
      </button>
      <button
        type="button"
        aria-label={head.playing ? "Pause" : head.mine ? "Carry on" : "Play from this page"}
        title={head.playing ? "Pause" : head.mine ? "Carry on" : "Play from this page"}
        onClick={toggle}
        className={cn(ROUND, "h-12 w-12 bg-teal-700 text-white hover:bg-teal-800")}
      >
        {head.playing ? <Pause className="h-5 w-5" aria-hidden /> : <Play className="h-5 w-5 translate-x-px" aria-hidden />}
      </button>
      <button type="button" aria-label="A bar on" title="A bar on" disabled={!data} onClick={() => step(1)} className={ROUND}>
        <StepForward className="h-5 w-5" aria-hidden />
      </button>
      {!compact && head.mine && head.bar !== null && (
        <span className="ml-1 text-sm tabular-nums text-muted-foreground" aria-live="polite">
          bar {head.bar}
        </span>
      )}
    </div>
  );
}
