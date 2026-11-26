// /karaoke (docs/KARAOKE.md): the karaoke's control panel. A section of the app like /music (same skin, same shape:
// a sticky left column from 1024 px, pill tabs for the lists, a toast at the foot), to be folded into its ergonomics.
//
//   - Now playing: the song the queue is on; previous / play-pause / next / stop, a scrub bar, the singer's voice
//     (the lead vocal under the backing: off / quiet / full) and a nudge for the lyrics' timing.
//   - The picture: the Room page's own card for the karaoke screen (LivePicture.tsx): Go live / Back to art, the scene
//     and its look under More.
//   - Find (owner, 25 Nov; fixed to the foot of the screen): one live search for the library and the web as you type (no submit; a spinner while the
//     web answers). The library matches at once and forgives spelling and half-typed words (fuzzy.ts); the web is
//     iTunes + Deezer steered by YouTube (routes/karaoke/sources.py catalog_search). Every row's Add puts it at the end of
//     the queue (singers pick a song and wait their turn); Up next jumps the queue; Library only gets it ready for later.
//   - Queue: the running order, like a hi-fi's. Songs already sung are tucked away under a line at the top ("3 already
//     sung"); the one playing is marked. Drag to reorder (by the grip), ▶ to jump to one, × to take it out (the
//     library keeps the song). The same song can be in it more than once. Songs still being prepared keep their
//     place and are skipped.
//   - Library: every song added, to browse for ideas: sorted most popular first (Deezer's play counts), or by title,
//     artist, year or when added; filtered by readiness and by tag (genre, decade: the catalogues' facts). Each shows
//     its preparation as a ring (getting it, lyrics, removing the vocal) until it's ready. Add / Up next, the bin
//     deletes it (asks first).
//   - An artist's name anywhere opens their songs, most popular first (ArtistSongs), to add any of them.
//   - Settings (rolled up): child mode (swear words as dashes and muted), sung words (turn yellow / bouncing dot / both), play on through the queue, the speaker delay and Calibrate, this song's lyrics timing,
//     and the room mic and its levels for checking.
//
// The order (owner, 25 Nov): Now playing (compact: the singer's voice at its top right), the queue, the picture and
// the settings; the search is fixed to the foot of the screen ("the dominant pathway when you're in a hurry"), its
// results opening upwards, and its library icon opens the library as a tray. From 1024 px Now playing stays beside
// the queue.
//
// The server runs the player (routes/karaoke/store.py): this page only asks. The sound and lyrics are the output
// window's (DisplayPage/karaoke/KaraokeLayer.tsx). Drag and drop: dnd-kit with the app's sensors (src/pages/Index.tsx:
// mouse after 8 px, touch after a 200 ms press), by a grip so a phone still scrolls; polling pauses during a drag.

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  DndContext,
  DragOverlay,
  KeyboardSensor,
  MouseSensor,
  TouchSensor,
  closestCenter,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  arrayMove,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import {
  AudioLines,
  Music,
  Gauge,
  GripVertical,
  History,
  Library as LibraryIcon,
  ListEnd,
  ListStart,
  Mic2,
  Pause,
  Play,
  RotateCcw,
  Search,
  SkipBack,
  SkipForward,
  Square,
  Trash2,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Drawer, DrawerContent, DrawerDescription, DrawerHeader, DrawerTitle } from "@/components/ui/drawer";
import { Input } from "@/components/ui/input";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import { Notice, Spinner, Unroll } from "@/pages/Room/bits";
import { CARD_CLASS, RoomThemeStyle, THEME_CLASS } from "@/pages/Room/theme";
import { getDeploymentRelative, type DeploymentInfo } from "@/shared/api/deployment";
import {
  karaoke,
  karaokeScreen,
  type KaraokeSong,
  type KaraokeState,
  type QueueAt,
  type QueueEntry,
  type SearchHit,
} from "@/shared/api/karaoke";
import { fuzzyFilter } from "./fuzzy";
import { LivePicture } from "./LivePicture";
import { RoomMicCheck } from "./RoomMic";

const POLL_MS = 1500;
const QUEUE_DROP = "queue-drop";
const LIB = "lib:";

const STATUS_WORDS: Record<KaraokeSong["status"], string> = {
  queued: "Waiting to be prepared",
  fetching: "Getting the song",
  lyrics: "Finding the lyrics",
  separating: "Removing the lead vocal",
  ready: "Ready",
  failed: "Didn't work",
};
/** How far along preparation is, for the ring. */
const STAGE: Record<KaraokeSong["status"], number> = {
  queued: 0.04,
  fetching: 0.2,
  lyrics: 0.4,
  separating: 0.65,
  ready: 1,
  failed: 1,
};

const HIGHLIGHT_OPTIONS: ["words" | "dot" | "both", string][] = [
  ["words", "Turn yellow"],
  ["dot", "Bouncing dot"],
  ["both", "Both"],
];

const GUIDES: { label: string; value: number }[] = [
  { label: "Off", value: 0 },
  { label: "Quiet", value: 0.25 },
  { label: "Full", value: 1 },
];

const mmss = (s: number | null | undefined) =>
  s == null || !isFinite(s) ? "–:––" : `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

const isLink = (s: string) => /^https?:\/\//i.test(s.trim());

type Act = (fn: () => Promise<unknown>, ok?: string) => Promise<void>;

export default function KaraokePage() {
  const [st, setSt] = useState<KaraokeState | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [libOpen, setLibOpen] = useState(false);
  // On a phone, once the player has scrolled out of sight, a slim copy of it rides the top of the screen (laid over
  // the page: nothing in the page changes size, so the scroll never jumps; 26 Nov the in-page version flickered).
  const playerRef = useRef<HTMLDivElement>(null);
  const [pinned, setPinned] = useState(false);
  useEffect(() => {
    const el = playerRef.current;
    if (!el) return;
    const wide = window.matchMedia("(min-width: 1024px)");
    let gone = false;
    const set = () => setPinned(gone && !wide.matches);
    const io = new IntersectionObserver(([e]) => {
      gone = !e.isIntersecting;
      set();
    });
    io.observe(el);
    wide.addEventListener("change", set);
    return () => {
      io.disconnect();
      wide.removeEventListener("change", set);
    };
  }, [st != null]); // eslint-disable-line react-hooks/exhaustive-deps
  const [dep, setDep] = useState<DeploymentInfo | null>(null);
  useEffect(() => {
    getDeploymentRelative().then(setDep, () => undefined);
  }, []);
  const findRef = useRef<HTMLInputElement>(null);
  const [artist, setArtist] = useState<string | null>(null);
  const [dragging, setDragging] = useState<string | null>(null);
  const draggingRef = useRef(false);

  // The server's clock minus this one (s), from the reply's midpoint: the scrub bar runs on it between polls.
  const skewRef = useRef(0);
  const refresh = useCallback(async () => {
    if (draggingRef.current) return;
    try {
      const sent = Date.now();
      const s = await karaoke.state();
      skewRef.current = s.now - (sent + Date.now()) / 2000;
      if (!draggingRef.current) setSt(s);
      setErr(null);
    } catch (e) {
      setErr(String((e as Error).message || e));
    }
  }, []);

  useEffect(() => {
    void refresh();
    const t = window.setInterval(refresh, POLL_MS);
    return () => window.clearInterval(t);
  }, [refresh]);

  const act: Act = useCallback(
    async (fn, ok) => {
      try {
        await fn();
        if (ok) setToast(ok);
      } catch (e) {
        setToast(String((e as Error).message || e));
      }
      void refresh();
    },
    [refresh],
  );

  useEffect(() => {
    if (!toast) return;
    const t = window.setTimeout(() => setToast(null), 3500);
    return () => window.clearTimeout(t);
  }, [toast]);

  const sensors = useSensors(
    useSensor(MouseSensor, { activationConstraint: { distance: 8 } }),
    useSensor(TouchSensor, {
      activationConstraint: { delay: 200, tolerance: 8 },
    }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    }),
  );

  const songs = useMemo(() => new Map((st?.songs ?? []).map((s) => [s.id, s])), [st?.songs]);
  const queue = st?.queue ?? [];
  const curIdx = queue.findIndex((e) => e.qid === st?.player.qid);
  const upcoming = curIdx < 0 ? queue.length : queue.length - curIdx;
  const queuedCount = new Map<string, number>();
  for (const e of queue) queuedCount.set(e.song_id, (queuedCount.get(e.song_id) ?? 0) + 1);

  const addToQueue = (s: KaraokeSong, at: QueueAt) =>
    void act(
      () => karaoke.queueAdd(s.id, undefined, at === "next"),
      at === "next" ? `“${s.title}” is up next` : `“${s.title}” added to the queue`,
    );
  const focusFind = () => {
    findRef.current?.focus();
    findRef.current?.scrollIntoView({ block: "center", behavior: "smooth" });
  };

  const onDragStart = (e: DragStartEvent) => {
    draggingRef.current = true;
    setDragging(String(e.active.id));
  };
  const endDrag = () => {
    draggingRef.current = false;
    setDragging(null);
  };
  const onDragEnd = ({ active, over }: DragEndEvent) => {
    endDrag();
    if (!st || !over) return;
    const id = String(active.id);
    const overId = String(over.id);
    const overIndex = overId === QUEUE_DROP ? queue.length : queue.findIndex((e) => e.qid === overId);
    if (overIndex < 0) return;
    if (id.startsWith(LIB)) {
      const songId = id.slice(LIB.length);
      const q = [...queue];
      q.splice(overIndex, 0, { qid: `pending-${Date.now()}`, song_id: songId });
      setSt({ ...st, queue: q });
      void act(() => karaoke.queueAdd(songId, overIndex));
      return;
    }
    const from = queue.findIndex((e) => e.qid === id);
    const to = overId === QUEUE_DROP ? queue.length - 1 : overIndex;
    if (from < 0 || from === to) return;
    setSt({ ...st, queue: arrayMove(queue, from, to) });
    void act(() => karaoke.queueMove(id, to));
  };

  const dragSong = dragging
    ? songs.get(
        dragging.startsWith(LIB) ? dragging.slice(LIB.length) : (queue.find((e) => e.qid === dragging)?.song_id ?? ""),
      )
    : undefined;

  const queueCard = st && <QueueCard st={st} songs={songs} act={act} onFind={focusFind} onArtist={setArtist} />;
  const libraryCard = st && (
    <LibraryCard
      songs={st.songs}
      queuedCount={queuedCount}
      draggable={false}
      act={act}
      addToQueue={addToQueue}
      onArtist={(a) => {
        setLibOpen(false);
        setArtist(a);
      }}
      onClose={() => setLibOpen(false)}
    />
  );
  const findCard = st && (
    <Find
      songs={st.songs}
      queuedCount={queuedCount}
      act={act}
      inputRef={findRef}
      onArtist={setArtist}
      libOpen={libOpen}
      onLibrary={() => setLibOpen(!libOpen)}
    />
  );

  return (
    <div className={cn(THEME_CLASS, "min-h-screen bg-background text-base text-foreground antialiased")}>
      <RoomThemeStyle />
      <div className="mx-auto w-full max-w-3xl px-4 pb-16 sm:px-6 lg:max-w-7xl">
        <header className="flex items-center gap-2 pb-3 pt-5">
          <h1 className="flex items-center gap-2 text-2xl font-semibold">
            <Mic2 className="h-6 w-6 text-primary" /> Karaoke
          </h1>
        </header>
        {err && (
          <Notice tone="bad" className="mb-3">
            Can't reach the karaoke: {err}
          </Notice>
        )}

        {/* Now playing first (sticky beside the lists on a wide screen), then the queue, then the search (Add puts a song
            at the queue's end) with the library under its icon; the picture and the settings below. */}
        <main className="space-y-4 lg:grid lg:grid-cols-[minmax(0,26rem)_minmax(0,1fr)] lg:items-start lg:gap-6 lg:space-y-0">
          {/* Beside the lists on a wide screen (sticky); on a phone it scrolls away and its slim copy takes the top
              (owner, 26 Nov: "player component should probably pin to top on mobile"). */}
          <div ref={playerRef} className="lg:sticky lg:top-4">
            {st && <NowPlaying st={st} skew={skewRef.current} songs={songs} act={act} />}
          </div>
          <DndContext
            sensors={sensors}
            collisionDetection={closestCenter}
            onDragStart={onDragStart}
            onDragEnd={onDragEnd}
            onDragCancel={endDrag}
          >
            <div className="space-y-4">
              {queueCard}
              {findCard}
            </div>
            <DragOverlay>{dragSong ? <RowFace song={dragSong} lifted /> : null}</DragOverlay>
          </DndContext>
        </main>

        <div className="mt-4 space-y-4 lg:mt-6 lg:grid lg:grid-cols-2 lg:items-start lg:gap-6 lg:space-y-0">
          <LivePicture screen={st ? karaokeScreen(dep, st.settings.screen) : null} />
          {st && <Settings st={st} songs={songs} act={act} dep={dep} />}
        </div>
      </div>
      {/* The library: a tray from the foot of the screen, opened from the search bar's icon. */}
      <Drawer open={libOpen && !!st} onOpenChange={setLibOpen} shouldScaleBackground={false}>
        <DrawerContent className={cn(THEME_CLASS, "mx-auto h-[85vh] max-w-3xl lg:max-w-5xl")}>
          <DrawerTitle className="sr-only">Library</DrawerTitle>
          <DrawerDescription className="sr-only">Every song added, to browse and add to the queue</DrawerDescription>
          <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-[max(1rem,env(safe-area-inset-bottom))] sm:px-4">
            {libraryCard}
          </div>
        </DrawerContent>
      </Drawer>
      {st && pinned && (
        <div className="fixed inset-x-0 top-0 z-40 px-3 pt-[max(0.5rem,env(safe-area-inset-top))] motion-safe:animate-in motion-safe:fade-in motion-safe:slide-in-from-top-2 lg:hidden">
          <div className="mx-auto max-w-3xl">
            <NowPlaying st={st} skew={skewRef.current} songs={songs} act={act} compact />
          </div>
        </div>
      )}
      {st && artist && <ArtistSongs name={artist} songs={st.songs} act={act} onClose={() => setArtist(null)} />}
      {toast && (
        <div className="fixed inset-x-0 bottom-[calc(max(0.75rem,env(safe-area-inset-bottom))+4.75rem)] z-50 mx-auto w-fit max-w-[calc(100vw-2rem)] rounded-full bg-stone-900/90 px-4 py-2.5 text-[15px] font-medium text-white shadow-lg">
          {toast}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- now playing

function NowPlaying({
  st,
  skew,
  songs,
  act,
  compact = false,
}: {
  st: KaraokeState;
  skew: number;
  songs: Map<string, KaraokeSong>;
  act: Act;
  /** Pinned while the page scrolls on a phone: the slim version. */
  compact?: boolean;
}) {
  const p = st.player;
  const song = p.song_id ? (songs.get(p.song_id) ?? null) : null;
  const dur = song?.audio_s ?? song?.duration ?? 0;
  // The scrub bar runs on between polls from the server's clock (skew: the server's minus this one); while a finger
  // is on it, it shows the finger; once let go, it holds there until the server's player has moved (a new rev).
  const [scrub, setScrub] = useState<number | null>(null);
  const [held, setHeld] = useState<{ v: number; rev: number } | null>(null);
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!held) return;
    if (p.rev !== held.rev) return setHeld(null);
    const t = window.setTimeout(() => setHeld(null), 3000); // the seek failed: back to the server's position
    return () => window.clearTimeout(t);
  }, [p.rev, held]);
  useEffect(() => {
    if (p.state !== "playing") return;
    let raf = 0;
    const loop = () => {
      setTick((x) => (x + 1) % 1_000_000);
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [p.state]);
  const live = p.state === "playing" ? p.pos_s + (Date.now() / 1000 + skew - p.at) : p.pos_s;
  const pos = scrub ?? held?.v ?? Math.min(dur || Infinity, Math.max(0, live));
  const playing = p.state === "playing";
  const showing = !!song && p.state !== "stopped";
  const anyReady = st.queue.some((e) => songs.get(e.song_id)?.status === "ready");

  // Pinned while the page scrolls on a phone (owner, 26 Nov: "the version of the player section that gets pinned may
  // need to be vertically smaller"): one slim row, the song, play/pause and next, a thin line for where it is.
  if (compact)
    return (
      <section className={cn(CARD_CLASS, "overflow-hidden p-2")}>
        <div className="flex items-center gap-2.5">
          {/* The song: back up to the full player (owner, 26 Nov: "clicking current song should scroll to top"). */}
          <button
            type="button"
            className="flex min-w-0 flex-1 items-center gap-2.5 text-left"
            onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}
            aria-label="Back to the player"
          >
            <Cover src={showing ? song?.art : null} />
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-semibold leading-tight">
                {showing ? song!.title : anyReady ? "Stopped" : "Nothing ready yet"}
              </div>
              <div className="truncate text-xs text-muted-foreground">{showing ? song!.artist : ""}</div>
            </div>
          </button>
          <RoundBtn
            label={playing ? "Pause" : "Play"}
            big
            small
            disabled={!playing && !anyReady && !showing}
            onClick={() => act(() => karaoke.player(playing ? "pause" : "play"))}
          >
            {playing ? <Pause className="h-4 w-4" /> : <Play className="ml-0.5 h-4 w-4" />}
          </RoundBtn>
          <RoundBtn
            label="Next"
            small
            onClick={() => act(() => karaoke.player("next"))}
            disabled={!showing && !anyReady}
          >
            <SkipForward className="h-4 w-4" />
          </RoundBtn>
        </div>
        {showing && dur > 0 && (
          <div className="-mx-2 -mb-2 mt-2 h-1 bg-stone-200">
            <div className="h-full bg-primary" style={{ width: `${Math.min(100, (pos / dur) * 100).toFixed(2)}%` }} />
          </div>
        )}
      </section>
    );

  return (
    <section className={cn(CARD_CLASS, "p-3 sm:p-4")}>
      {/* The heading, with the singer's voice at its right (it stays put while songs change). */}
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">Now playing</h2>
        <div className="flex items-center gap-1.5" role="group" aria-label="Singer's voice">
          <span className="text-xs text-muted-foreground" title="The lead vocal under the backing track">
            Voice
          </span>
          <div className="flex rounded-full border border-stone-200 p-0.5">
            {GUIDES.map((g) => (
              <button
                key={g.label}
                type="button"
                onClick={() => act(() => karaoke.settings({ guide: g.value }))}
                className={cn(
                  "rounded-full px-2.5 py-0.5 text-xs font-medium",
                  Math.abs(st.settings.guide - g.value) < 0.01
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {g.label}
              </button>
            ))}
          </div>
        </div>
      </div>
      <div className="mt-2 flex min-h-[2.75rem] items-center gap-3">
        {showing && <Cover src={song.art} big />}
        {showing ? (
          <div className="min-w-0">
            <div className="truncate text-lg font-semibold leading-tight">{song.title}</div>
            <div className="truncate text-sm text-muted-foreground">{song.artist}</div>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            {anyReady ? "Stopped. Play starts the queue." : "Nothing ready in the queue yet."}
          </p>
        )}
      </div>
      {showing && (
        <div className="mt-2 flex items-center gap-2 text-xs tabular-nums text-muted-foreground">
          <span className="w-9 text-right">{mmss(pos)}</span>
          <Slider
            value={[pos]}
            min={0}
            max={Math.max(1, dur)}
            step={0.1}
            onValueChange={([v]) => setScrub(v)}
            onValueCommit={([v]) => {
              setScrub(null);
              setHeld({ v, rev: p.rev });
              void act(() => karaoke.player("seek", { pos_s: v }));
            }}
            aria-label="Position in the song"
            className="flex-1"
          />
          <span className="w-9">-{mmss(Math.max(0, dur - pos))}</span>
        </div>
      )}
      <div className="mt-2 flex items-center justify-center gap-2">
        <RoundBtn label="Previous" onClick={() => act(() => karaoke.player("prev"))} disabled={!showing}>
          <SkipBack className="h-4 w-4" />
        </RoundBtn>
        <RoundBtn
          label={playing ? "Pause" : "Play"}
          big
          disabled={!playing && !anyReady && !showing}
          onClick={() => act(() => karaoke.player(playing ? "pause" : "play"))}
        >
          {playing ? <Pause className="h-5 w-5" /> : <Play className="ml-0.5 h-5 w-5" />}
        </RoundBtn>
        <RoundBtn label="Next" onClick={() => act(() => karaoke.player("next"))} disabled={!showing && !anyReady}>
          <SkipForward className="h-4 w-4" />
        </RoundBtn>
        <RoundBtn label="Stop" onClick={() => act(() => karaoke.player("stop"))} disabled={!showing}>
          <Square className="h-3.5 w-3.5" />
        </RoundBtn>
      </div>
    </section>
  );
}

function RoundBtn({
  label,
  onClick,
  disabled,
  big,
  small,
  children,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  big?: boolean;
  /** The pinned player's size. */
  small?: boolean;
  children: ReactNode;
}) {
  return (
    <Button
      type="button"
      variant={big ? "default" : "secondary"}
      title={label}
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
      className={cn("shrink-0 rounded-full p-0", small ? "h-9 w-9" : big ? "h-12 w-12" : "h-10 w-10")}
    >
      {children}
    </Button>
  );
}

// ---------------------------------------------------------------- queue

type Where = "played" | "current" | "stopped" | "next";

/** The running order. Songs already sung are tucked away (owner, 25 Nov: "previously played songs aren't very
 *  interesting -- should probably disappear unless user wants to find them"): a line at the top shows them. */
function QueueCard({
  st,
  songs,
  act,
  onFind,
  onArtist,
}: {
  st: KaraokeState;
  songs: Map<string, KaraokeSong>;
  act: Act;
  onFind: () => void;
  onArtist: (a: string) => void;
}) {
  const { setNodeRef, isOver } = useDroppable({ id: QUEUE_DROP });
  const [showPlayed, setShowPlayed] = useState(false);
  const queue = st.queue;
  const cur = queue.findIndex((e) => e.qid === st.player.qid);
  const played = Math.max(0, cur);
  const shown = queue.map((e, i) => ({ e, i })).filter(({ i }) => showPlayed || i >= played);
  const upcoming = queue.length - played;
  return (
    <Card
      title={`Queue${upcoming ? ` · ${upcoming}` : ""}`}
      action={
        queue.length > (st.player.state === "stopped" ? 0 : 1) ? (
          <Button
            variant="ghost"
            size="sm"
            className="h-9 rounded-lg px-2.5 text-muted-foreground hover:text-red-700"
            onClick={() =>
              window.confirm(
                st.player.state === "stopped"
                  ? "Clear the whole queue?"
                  : "Clear the queue? The song playing now stays.",
              ) && act(() => karaoke.queueClear(), "Queue cleared")
            }
          >
            <Trash2 className="mr-1 h-4 w-4" /> Clear
          </Button>
        ) : undefined
      }
    >
      <div ref={setNodeRef} className={cn("min-h-[5rem] rounded-xl transition-colors", isOver && "bg-primary/5")}>
        {played > 0 && (
          <button
            type="button"
            onClick={() => setShowPlayed(!showPlayed)}
            className="mb-1 flex w-full items-center gap-1.5 rounded-lg px-1 py-1.5 text-left text-sm text-muted-foreground hover:text-foreground"
          >
            <History className="h-4 w-4" />
            {showPlayed ? "Hide the songs already sung" : `${played} already sung`}
          </button>
        )}
        {upcoming === 0 && (
          <div className="py-6 text-center text-sm text-muted-foreground">
            {queue.length ? "Nothing more in the queue." : "The queue is empty."}{" "}
            <button type="button" className="text-primary underline-offset-2 hover:underline" onClick={onFind}>
              Find a song
            </button>
            .
          </div>
        )}
        <SortableContext items={shown.map(({ e }) => e.qid)} strategy={verticalListSortingStrategy}>
          <ul className="divide-y divide-stone-200/70">
            {shown.map(({ e, i }) => {
              const song = songs.get(e.song_id);
              if (!song) return null;
              const where: Where =
                cur < 0
                  ? "next"
                  : i < cur
                    ? "played"
                    : i === cur
                      ? st.player.state === "stopped"
                        ? "stopped"
                        : "current"
                      : "next";
              return <QueueRow key={e.qid} entry={e} song={song} where={where} act={act} onArtist={onArtist} />;
            })}
          </ul>
        </SortableContext>
      </div>
    </Card>
  );
}

function QueueRow({
  entry,
  song,
  where,
  act,
  onArtist,
}: {
  entry: QueueEntry;
  song: KaraokeSong;
  where: Where;
  act: Act;
  onArtist: (a: string) => void;
}) {
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, transform, transition, isDragging } = useSortable({
    id: entry.qid,
  });
  return (
    <li
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={cn(
        "rounded-xl",
        isDragging && "opacity-40",
        where === "current" && "bg-primary/10",
        where === "played" && "opacity-55",
      )}
    >
      <RowFace
        song={song}
        lead={
          where === "current" ? (
            <AudioLines className="h-5 w-5 shrink-0 text-primary motion-safe:animate-pulse" />
          ) : undefined
        }
        hint={where === "current" ? "Now playing" : where === "played" ? "Played" : undefined}
        grip={<Grip setRef={setActivatorNodeRef} listeners={listeners} attributes={attributes} />}
        onArtist={onArtist}
      >
        {song.status === "ready" && where !== "current" && (
          <IconBtn label="Play this now" onClick={() => act(() => karaoke.player("play", { qid: entry.qid }))}>
            <Play className="h-4 w-4" />
          </IconBtn>
        )}
        {song.status !== "ready" && song.status !== "failed" && <RestartBtn song={song} act={act} />}
        <IconBtn label="Take out of the queue" onClick={() => act(() => karaoke.queueRemove(entry.qid))}>
          <X className="h-4 w-4" />
        </IconBtn>
      </RowFace>
    </li>
  );
}

// ---------------------------------------------------------------- library

type Filter = "all" | "ready" | "preparing" | "failed";
const FILTERS: [Filter, string][] = [
  ["all", "All"],
  ["ready", "Ready"],
  ["preparing", "Preparing"],
  ["failed", "Problems"],
];

function inFilter(s: KaraokeSong, f: Filter): boolean {
  if (f === "ready") return s.status === "ready";
  if (f === "failed") return s.status === "failed";
  if (f === "preparing") return s.status !== "ready" && s.status !== "failed";
  return true;
}

type Sort = "popular" | "az" | "artist" | "year" | "added";
const SORTS: [Sort, string][] = [
  ["popular", "Most popular"],
  ["az", "Title A–Z"],
  ["artist", "Artist"],
  ["year", "Year, newest"],
  ["added", "Recently added"],
];
const SORT_KEY = "karaoke.librarySort";

function sorted(rows: KaraokeSong[], sort: Sort): KaraokeSong[] {
  const t = (a: KaraokeSong, b: KaraokeSong) => a.title.localeCompare(b.title);
  const r = [...rows];
  if (sort === "popular") return r.sort((a, b) => (b.rank ?? -1) - (a.rank ?? -1) || t(a, b));
  if (sort === "az") return r.sort((a, b) => t(a, b) || a.artist.localeCompare(b.artist));
  if (sort === "artist") return r.sort((a, b) => a.artist.localeCompare(b.artist) || (b.rank ?? -1) - (a.rank ?? -1));
  if (sort === "year") return r.sort((a, b) => (b.year ?? -1) - (a.year ?? -1) || t(a, b));
  return r.sort((a, b) => b.created_at - a.created_at);
}

function LibraryCard({
  songs,
  queuedCount,
  draggable,
  act,
  addToQueue,
  onArtist,
  onClose,
}: {
  songs: KaraokeSong[];
  queuedCount: Map<string, number>;
  draggable: boolean;
  act: Act;
  addToQueue: (s: KaraokeSong, at: QueueAt) => void;
  onArtist: (a: string) => void;
  onClose: () => void;
}) {
  const [filter, setFilter] = useState<Filter>("all");
  const [tag, setTag] = useState<string | null>(null);
  const [sort, setSortState] = useState<Sort>(() =>
    SORTS.some(([k]) => k === localStorage.getItem(SORT_KEY)) ? (localStorage.getItem(SORT_KEY) as Sort) : "popular",
  );
  const setSort = (v: Sort) => {
    setSortState(v);
    localStorage.setItem(SORT_KEY, v);
  };
  // The tags (genre, decade) of the songs, most used first.
  const tags = useMemo(() => {
    const n = new Map<string, number>();
    for (const s of songs) for (const g of s.tags ?? []) n.set(g, (n.get(g) ?? 0) + 1);
    return [...n.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  }, [songs]);
  const shown = useMemo(
    () =>
      sorted(
        songs.filter((s) => inFilter(s, filter) && (!tag || (s.tags ?? []).includes(tag))),
        sort,
      ),
    [songs, filter, sort, tag],
  );

  return (
    <Card
      title={`Library · ${songs.length}`}
      action={
        <IconBtn label="Hide the library" onClick={onClose}>
          <X className="h-4 w-4" />
        </IconBtn>
      }
    >
      <div className="space-y-2.5">
        <div className="flex flex-wrap items-center gap-1.5">
          {FILTERS.map(([f, label]) => {
            const n = songs.filter((s) => inFilter(s, f)).length;
            if (f !== "all" && n === 0 && filter !== f) return null;
            return (
              <button
                key={f}
                type="button"
                onClick={() => setFilter(f)}
                className={cn(
                  "rounded-full border px-3 py-1 text-xs font-medium",
                  filter === f
                    ? "border-primary bg-primary text-primary-foreground"
                    : "border-stone-200 text-muted-foreground hover:text-foreground",
                )}
              >
                {label} {n}
              </button>
            );
          })}
          <select
            value={sort}
            onChange={(e) => setSort(e.target.value as Sort)}
            aria-label="Sort the library"
            className="ml-auto rounded-full border border-stone-200 bg-card px-2.5 py-1 text-xs text-muted-foreground hover:text-foreground"
          >
            {SORTS.map(([k, label]) => (
              <option key={k} value={k}>
                {label}
              </option>
            ))}
          </select>
        </div>
        {tags.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {tags.map(([g, n]) => (
              <button
                key={g}
                type="button"
                onClick={() => setTag(tag === g ? null : g)}
                className={cn(
                  "rounded-full px-2.5 py-0.5 text-xs",
                  tag === g ? "bg-stone-800 text-white" : "bg-stone-100 text-stone-600 hover:bg-stone-200",
                )}
              >
                {g} <span className="opacity-60">{n}</span>
              </button>
            ))}
          </div>
        )}
        {songs.length === 0 && (
          <p className="py-4 text-center text-sm text-muted-foreground">Songs you add appear here.</p>
        )}
        {songs.length > 0 && shown.length === 0 && (
          <p className="py-4 text-center text-sm text-muted-foreground">None here.</p>
        )}
        <ul className="divide-y divide-stone-200/70">
          {shown.map((s) => (
            <LibraryRow
              key={s.id}
              song={s}
              queued={queuedCount.get(s.id) ?? 0}
              draggable={draggable}
              act={act}
              addToQueue={addToQueue}
              onArtist={onArtist}
            />
          ))}
        </ul>
      </div>
    </Card>
  );
}

function LibraryRow({
  song,
  queued,
  draggable,
  act,
  addToQueue,
  onArtist,
}: {
  song: KaraokeSong;
  queued: number;
  draggable: boolean;
  act: Act;
  addToQueue: (s: KaraokeSong, at: QueueAt) => void;
  onArtist: (a: string) => void;
}) {
  const failed = song.status === "failed";
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, isDragging } = useDraggable({
    id: `${LIB}${song.id}`,
    disabled: !draggable || failed,
  });
  return (
    <li ref={setNodeRef} className={cn(isDragging && "opacity-40")}>
      <RowFace
        song={song}
        hint={queued ? (queued > 1 ? `In the queue ×${queued}` : "In the queue") : undefined}
        grip={
          draggable && !failed ? (
            <Grip setRef={setActivatorNodeRef} listeners={listeners} attributes={attributes} />
          ) : undefined
        }
        onArtist={onArtist}
      >
        {failed ? (
          <IconBtn label="Try again" onClick={() => act(() => karaoke.retry(song.id), `Trying “${song.title}” again`)}>
            <RotateCcw className="h-4 w-4" />
          </IconBtn>
        ) : (
          <>
            {song.status !== "ready" && <RestartBtn song={song} act={act} />}
            <AtButtons onAt={(at) => addToQueue(song, at)} />
          </>
        )}
        <IconBtn
          label="Delete from the library"
          quiet
          onClick={() =>
            window.confirm(`Delete “${song.title}” from the library?`) && act(() => karaoke.remove(song.id))
          }
        >
          <Trash2 className="h-4 w-4" />
        </IconBtn>
      </RowFace>
    </li>
  );
}

// ---------------------------------------------------------------- rows

type GripProps = {
  setRef: (el: HTMLElement | null) => void;
  listeners: ReturnType<typeof useDraggable>["listeners"];
  attributes: ReturnType<typeof useDraggable>["attributes"];
};

function Grip({ setRef, listeners, attributes }: GripProps) {
  return (
    <button
      type="button"
      ref={setRef}
      {...listeners}
      {...attributes}
      aria-label="Drag"
      className="flex h-10 w-7 shrink-0 cursor-grab touch-none items-center justify-center rounded-lg text-muted-foreground/70 hover:bg-muted active:cursor-grabbing"
    >
      <GripVertical className="h-4 w-4" />
    </button>
  );
}

/** Preparation as a ring that fills stage by stage, turning while it works. */
function Ring({ song }: { song: KaraokeSong }) {
  const r = 9;
  const c = 2 * Math.PI * r;
  const f = STAGE[song.status];
  return (
    <svg viewBox="0 0 24 24" className="h-6 w-6 shrink-0" role="img" aria-label={STATUS_WORDS[song.status]}>
      <circle cx="12" cy="12" r={r} fill="none" strokeWidth="3" className="stroke-stone-200" />
      <circle
        cx="12"
        cy="12"
        r={r}
        fill="none"
        strokeWidth="3"
        strokeLinecap="round"
        className="stroke-primary transition-[stroke-dasharray] duration-700"
        strokeDasharray={`${f * c} ${c}`}
        transform="rotate(-90 12 12)"
      />
      <circle
        cx="12"
        cy="12"
        r={r}
        fill="none"
        strokeWidth="3"
        strokeLinecap="round"
        style={{ transformOrigin: "12px 12px" }}
        className="stroke-primary/30 motion-safe:animate-spin"
        strokeDasharray={`${0.12 * c} ${c}`}
      />
    </svg>
  );
}

/** One song's row: grip, title, artist and state, then its buttons. Also the dragged copy (lifted). */
function RowFace({
  song,
  hint,
  grip,
  lead,
  lifted,
  onArtist,
  children,
}: {
  song: KaraokeSong;
  hint?: string;
  grip?: ReactNode;
  lead?: ReactNode;
  lifted?: boolean;
  /** The artist's name opens their songs (ArtistSongs). */
  onArtist?: (name: string) => void;
  children?: ReactNode;
}) {
  const working = !["ready", "failed"].includes(song.status);
  // The cover, else a plain tile of the same size (owner, 26 Nov: "use a placeholder for visual consistency").
  const art = working || lead ? null : <Cover src={song.art} />;
  return (
    <div
      className={cn(
        "flex min-h-[3.25rem] items-center gap-2 py-1.5 pr-1",
        !grip && "pl-1",
        lifted && "rounded-xl bg-card px-3 shadow-lg ring-1 ring-stone-200",
      )}
    >
      {grip ?? (lifted ? <GripVertical className="h-4 w-4 text-muted-foreground" /> : null)}
      {working ? <Ring song={song} /> : (lead ?? art)}
      <div className="min-w-0 flex-1">
        <div className="truncate font-medium">{song.title}</div>
        <div className={cn("truncate text-sm", song.status === "failed" ? "text-red-700" : "text-muted-foreground")}>
          {song.artist ? (
            <>
              {onArtist ? (
                <button
                  type="button"
                  className="underline-offset-2 hover:text-foreground hover:underline"
                  onClick={() => onArtist(song.artist)}
                >
                  {song.artist}
                </button>
              ) : (
                song.artist
              )}
              {" · "}
            </>
          ) : (
            ""
          )}
          {song.status === "failed"
            ? song.error || STATUS_WORDS.failed
            : song.status === "ready"
              ? mmss(song.audio_s ?? song.duration)
              : `${STATUS_WORDS[song.status]}…`}
          {song.status === "ready" && song.year ? ` · ${song.year}` : ""}
          {song.status === "ready" && song.lyrics?.kind !== "synced" ? " · no timed lyrics" : ""}
          {hint ? ` · ${hint}` : ""}
        </div>
      </div>
      {children && <div className="flex shrink-0 items-center">{children}</div>}
    </div>
  );
}

// ---------------------------------------------------------------- finding songs

const SEARCH_DEBOUNCE_MS = 280;
const LIB_SHOWN = 6;

/** The one search (owner, 25 Nov: "pinwheel search for current library and new songs through same search interface
 *  ... live / dynamic ... add directly to top of songs queue"): the library matches at once as you type (fuzzy:
 *  fuzzy.ts), the web a moment later (iTunes + Deezer steered by YouTube: forgiving of spelling); a link pasted is
 *  one row of its own. Add puts it at the end of the queue; Up next jumps the queue; Library only (a song not yet
 *  in the library) just gets it ready. */
function Find({
  songs,
  queuedCount,
  act,
  inputRef,
  onArtist,
  libOpen,
  onLibrary,
}: {
  songs: KaraokeSong[];
  queuedCount: Map<string, number>;
  act: Act;
  inputRef: React.RefObject<HTMLInputElement>;
  onArtist: (a: string) => void;
  libOpen: boolean;
  onLibrary: () => void;
}) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<{ q: string; rows: SearchHit[] } | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const seq = useRef(0);
  const text = q.trim();
  const link = isLink(text);

  useEffect(() => {
    const n = ++seq.current;
    setMsg(null);
    if (text.length < 2 || link) {
      setBusy(false);
      setHits(null);
      return;
    }
    setBusy(true);
    const t = window.setTimeout(async () => {
      try {
        const r = await karaoke.search(text);
        if (n === seq.current) setHits({ q: text, rows: r.results });
      } catch (err) {
        if (n === seq.current) setMsg(String((err as Error).message || err));
      } finally {
        if (n === seq.current) setBusy(false);
      }
    }, SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(t);
  }, [text, link]);

  const lib = useMemo(
    () => (text && !link ? fuzzyFilter(songs, text, (s) => `${s.title} ${s.artist}`) : []),
    [songs, text, link],
  );
  const libIds = new Set(lib.map((s) => s.id));
  const web = (hits?.rows ?? []).filter((h) => !(h.have && libIds.has(h.have)));
  const haveMore = (hits?.rows ?? [])
    .filter((h) => h.have && !libIds.has(h.have))
    .map((h) => songs.find((s) => s.id === h.have))
    .filter(Boolean) as KaraokeSong[];
  const libRows = [...lib, ...haveMore];
  const clear = () => {
    setQ("");
    inputRef.current?.focus();
  };

  const queueSong = (s: KaraokeSong, at: QueueAt) =>
    void act(
      () => karaoke.queueAdd(s.id, undefined, at === "next"),
      at === "next" ? `“${s.title}” is up next` : `“${s.title}” added to the queue`,
    ).then(clear);
  const said = (at: QueueAt | null) =>
    at === "next" ? "is up next" : at === "end" ? "added to the queue" : "added to the library";
  const addWeb = (h: SearchHit, at: QueueAt | null) =>
    void act(
      () => karaoke.request({ ...hitFacts(h), ...(at ? { queue: at } : {}) }),
      `“${h.title}” ${said(at)}: getting it ready`,
    ).then(clear);
  const addText = (at: QueueAt | null) =>
    void act(
      () =>
        karaoke.request({
          ...(link ? { url: text } : { q: text }),
          ...(at ? { queue: at } : {}),
        }),
      `${link ? "The link" : `“${text}”`} ${said(at)}: getting it ready`,
    ).then(clear);

  const open = link || text.length >= 1;
  // Under the queue, held to the foot of the screen while its place is further down (owner, 25-26 Nov: "the dominant
  // pathway when you're in a hurry"; "if you scroll down past bottom of queue etc. then search can detach from
  // bottom of viewport": CSS sticky does exactly that). The results open upwards over the page; the library icon
  // opens the library as a tray.
  return (
    <div className="sticky bottom-0 z-40 pb-[max(0.75rem,env(safe-area-inset-bottom))] pt-1">
      <div className="relative">
        {open && (
          <section
            className={cn(
              CARD_CLASS,
              "absolute inset-x-0 bottom-full mb-2 max-h-[min(60vh,34rem)] overflow-y-auto p-3 shadow-xl sm:p-4",
            )}
          >
            {link && SPOTIFY_LIST.test(text) && <SpotifyListRow url={text} act={act} onDone={clear} />}
            {link && !SPOTIFY_LIST.test(text) && (
              <ul className="mt-2">
                <li className="flex min-h-[3.25rem] items-center gap-2 py-1.5 pl-1 pr-1">
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-medium">This link</div>
                    <div className="truncate text-sm text-muted-foreground">{text}</div>
                  </div>
                  <AtButtons onAt={addText} onLibrary={() => addText(null)} />
                </li>
              </ul>
            )}

            {text.length >= 1 && !link && (
              <div className="mt-2 space-y-2">
                {libRows.length > 0 && (
                  <div>
                    <SectionWord>In the library</SectionWord>
                    <ul className="divide-y divide-stone-200/70">
                      {libRows.slice(0, LIB_SHOWN).map((s) => {
                        const n = queuedCount.get(s.id) ?? 0;
                        return (
                          <li key={s.id}>
                            <RowFace
                              song={s}
                              hint={n ? (n > 1 ? `In the queue ×${n}` : "In the queue") : undefined}
                              onArtist={onArtist}
                            >
                              {s.status === "failed" ? (
                                <IconBtn
                                  label="Try again"
                                  onClick={() => act(() => karaoke.retry(s.id), `Trying “${s.title}” again`)}
                                >
                                  <RotateCcw className="h-4 w-4" />
                                </IconBtn>
                              ) : (
                                <AtButtons onAt={(at) => queueSong(s, at)} />
                              )}
                            </RowFace>
                          </li>
                        );
                      })}
                    </ul>
                    {libRows.length > LIB_SHOWN && (
                      <p className="px-1 text-xs text-muted-foreground">
                        and {libRows.length - LIB_SHOWN} more: type a little more
                      </p>
                    )}
                  </div>
                )}
                {text.length >= 2 && (
                  <div>
                    <SectionWord>From the web {busy && <Spinner className="ml-1 inline h-3 w-3" />}</SectionWord>
                    {msg && <p className="px-1 py-2 text-sm text-red-700">{msg}</p>}
                    {hits && hits.q === text && web.length === 0 && !busy && !msg && (
                      <p className="px-1 py-2 text-sm text-muted-foreground">Nothing new found.</p>
                    )}
                    <ul className={cn("divide-y divide-stone-200/70 transition-opacity", busy && hits && "opacity-60")}>
                      {web.map((h) => (
                        <li key={h.key} className="flex min-h-[3.25rem] items-center gap-2.5 py-1.5 pl-1 pr-1">
                          {h.art ? (
                            <img
                              src={h.art}
                              alt=""
                              className="h-9 w-9 shrink-0 rounded-md object-cover"
                              loading="lazy"
                            />
                          ) : (
                            <div className="h-9 w-9 shrink-0 rounded-md bg-stone-100" />
                          )}
                          <div className="min-w-0 flex-1">
                            <div className="truncate font-medium">{h.title}</div>
                            <div className="truncate text-sm text-muted-foreground">
                              <button
                                type="button"
                                className="underline-offset-2 hover:text-foreground hover:underline"
                                onClick={() => onArtist(h.artist)}
                              >
                                {h.artist}
                              </button>
                              {h.year ? ` · ${h.year}` : ""}
                              {h.duration ? ` · ${mmss(h.duration)}` : ""}
                              {h.have ? " · in the library" : ""}
                            </div>
                          </div>
                          <AtButtons
                            onAt={(at) => addWeb(h, at)}
                            onLibrary={h.have ? undefined : () => addWeb(h, null)}
                          />
                        </li>
                      ))}
                    </ul>
                    {hits && hits.q === text && !busy && (
                      <button
                        type="button"
                        className="mt-1 px-1 text-left text-sm text-primary underline-offset-2 hover:underline"
                        onClick={() => addText("end")}
                      >
                        Not listed? Add “{text}” as typed (YouTube's best guess; may have no lyrics)
                      </button>
                    )}
                  </div>
                )}
              </div>
            )}
          </section>
        )}
        <section className={cn(CARD_CLASS, "p-2.5 shadow-lg")}>
          <div className="flex items-center gap-2">
            <div className="relative min-w-0 flex-1">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                ref={inputRef}
                value={q}
                onChange={(e) => setQ(e.target.value)}
                onKeyDown={(e) => e.key === "Escape" && setQ("")}
                placeholder="Find a song or paste a link"
                className="h-11 rounded-xl pl-9 pr-10 text-base"
                enterKeyHint="search"
                aria-label="Find a song"
              />
              <div className="absolute right-2 top-1/2 flex -translate-y-1/2 items-center">
                {busy ? (
                  <Spinner className="mr-1.5 text-muted-foreground" />
                ) : q ? (
                  <button
                    type="button"
                    aria-label="Clear"
                    onClick={clear}
                    className="rounded-md p-1.5 text-muted-foreground hover:text-foreground"
                  >
                    <X className="h-4 w-4" />
                  </button>
                ) : null}
              </div>
            </div>
            <Button
              type="button"
              variant={libOpen ? "default" : "outline"}
              className="h-11 w-11 shrink-0 rounded-xl p-0"
              title={libOpen ? "Hide the library" : "Browse the library"}
              aria-label={libOpen ? "Hide the library" : "Browse the library"}
              aria-pressed={libOpen}
              onClick={onLibrary}
            >
              <LibraryIcon className="h-5 w-5" />
            </Button>
          </div>
        </section>
      </div>
    </div>
  );
}

/** A web song's request: who and what, plus the catalogue facts the library sorts and tags by. */
const hitFacts = (h: SearchHit) => ({
  artist: h.artist,
  title: h.title,
  duration: h.duration,
  genre: h.genre ?? null,
  year: h.year ?? null,
  rank: h.rank ?? null,
  art: h.art ?? null,
  album: h.album ?? null,
});

/** More from an artist (owner, 25 Nov: "taylor swift has _many_ hits ... sort by most popular"): their songs, most
 *  popular first (Deezer's top tracks), each one Add / Up next / Library only, the library's own marked. */
function ArtistSongs({
  name,
  songs,
  act,
  onClose,
}: {
  name: string;
  songs: KaraokeSong[];
  act: Act;
  onClose: () => void;
}) {
  const [mobile] = useState(() => window.matchMedia("(max-width: 767px)").matches);
  const [o, setO] = useState<{
    artist: string;
    picture: string | null;
    fans: number | null;
    results: SearchHit[];
  } | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [added, setAdded] = useState<Set<string>>(new Set());
  useEffect(() => {
    let dead = false;
    setO(null);
    setMsg(null);
    karaoke
      .artist(name)
      .then((r) => !dead && setO(r))
      .catch((e) => !dead && setMsg(String((e as Error).message || e)));
    return () => {
      dead = true;
    };
  }, [name]);
  const byId = new Map(songs.map((s) => [s.id, s]));
  const said = (at: QueueAt | null) =>
    at === "next" ? "is up next" : at === "end" ? "added to the queue" : "added to the library";
  const add = (h: SearchHit, at: QueueAt | null) => {
    const mine = h.have ? byId.get(h.have) : undefined;
    void act(
      () =>
        mine && at
          ? karaoke.queueAdd(mine.id, undefined, at === "next")
          : karaoke.request({ ...hitFacts(h), ...(at ? { queue: at } : {}) }),
      `“${h.title}” ${said(at)}${mine ? "" : ": getting it ready"}`,
    ).then(() => setAdded((x) => new Set(x).add(h.key)));
  };
  const head = (
    <div className="flex min-w-0 items-center gap-3 text-left">
      {o?.picture && <img src={o.picture} alt="" className="h-12 w-12 shrink-0 rounded-full object-cover" />}
      <div className="min-w-0">
        <div className="truncate text-lg font-semibold leading-tight">{o?.artist ?? name}</div>
        <div className="text-sm text-muted-foreground">
          Most popular first
          {o?.fans ? ` · ${Intl.NumberFormat(undefined, { notation: "compact" }).format(o.fans)} fans` : ""}
        </div>
      </div>
    </div>
  );
  const list = (
    <>
      {msg && <p className="text-sm text-red-700">{msg}</p>}
      {!o && !msg && (
        <div className="flex justify-center py-6">
          <Spinner className="h-6 w-6 text-muted-foreground" />
        </div>
      )}
      {o && o.results.length === 0 && <p className="py-4 text-sm text-muted-foreground">No songs found for {name}.</p>}
      <ul className="divide-y divide-stone-200/70">
        {o?.results.map((h, i) => (
          <li key={h.key} className="flex min-h-[3.25rem] min-w-0 items-center gap-2 py-1.5">
            <span className="hidden w-5 shrink-0 text-right text-xs tabular-nums text-muted-foreground sm:block">
              {i + 1}
            </span>
            <Cover src={h.art} />
            <div className="min-w-0 flex-1">
              <div className="truncate font-medium">{h.title}</div>
              <div className="truncate text-sm text-muted-foreground">
                {h.album ? `${h.album} · ` : ""}
                {mmss(h.duration)}
                {h.have ? " · in the library" : added.has(h.key) ? " · added" : ""}
              </div>
            </div>
            <AtButtons
              onAt={(at) => add(h, at)}
              onLibrary={h.have || added.has(h.key) ? undefined : () => add(h, null)}
            />
          </li>
        ))}
      </ul>
    </>
  );
  // A tray from the bottom on a phone, a dialog on a bigger screen.
  if (mobile)
    return (
      <Drawer open onOpenChange={(v) => !v && onClose()} shouldScaleBackground={false}>
        <DrawerContent className={cn(THEME_CLASS, "max-h-[88vh]")}>
          <DrawerHeader className="px-4 pb-2">
            <DrawerTitle asChild>{head}</DrawerTitle>
            <DrawerDescription className="sr-only">{name}'s songs, most popular first</DrawerDescription>
          </DrawerHeader>
          <div className="min-w-0 overflow-y-auto overflow-x-hidden px-3 pb-[max(1rem,env(safe-area-inset-bottom))]">
            {list}
          </div>
        </DrawerContent>
      </Drawer>
    );
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent
        className={cn(
          THEME_CLASS,
          "flex max-h-[85vh] w-[calc(100vw-2rem)] max-w-xl flex-col gap-3 overflow-hidden p-4 sm:p-5",
        )}
      >
        <DialogHeader className="min-w-0 pr-6">
          <DialogTitle asChild>{head}</DialogTitle>
          <DialogDescription className="sr-only">{name}'s songs, most popular first</DialogDescription>
        </DialogHeader>
        <div className="min-w-0 overflow-y-auto overflow-x-hidden">{list}</div>
      </DialogContent>
    </Dialog>
  );
}

const SPOTIFY_LIST = /open\.spotify\.com\/(?:intl-[a-z]+\/)?(?:embed\/)?(?:playlist|album)\//;

/** A Spotify playlist or album pasted into the search (owner, 26 Nov: "import a spotify playlist"; "use the existing
 *  search box -- results should offer a 'spotify playlist' option"): one result, its name and songs, and the
 *  options: all of them into the library to be prepared (the ones it has are skipped), or into the queue too. */
function SpotifyListRow({ url, act, onDone }: { url: string; act: Act; onDone: () => void }) {
  const [o, setO] = useState<Awaited<ReturnType<typeof karaoke.playlist>> | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    let dead = false;
    setO(null);
    setMsg(null);
    karaoke.playlist(url).then(
      (r) => !dead && setO(r),
      (e) => !dead && setMsg(String((e as Error).message || e)),
    );
    return () => {
      dead = true;
    };
  }, [url]);
  const what = url.includes("/album/") ? "album" : "playlist";
  const have = o ? o.tracks.filter((t) => t.have).length : 0;
  const go = (queue: boolean) =>
    void act(
      () => karaoke.importList(url, queue),
      queue ? "Added to the queue: preparing the new songs" : "Added to the library: preparing the new songs",
    ).then(onDone);
  return (
    <div className="mt-2">
      <SectionWord>Spotify {what}</SectionWord>
      {msg && <p className="px-1 py-2 text-sm text-red-700">{msg}</p>}
      {!o && !msg && (
        <p className="flex items-center gap-2 px-1 py-2 text-sm text-muted-foreground">
          <Spinner /> Reading it…
        </p>
      )}
      {o && (
        <>
          <div className="flex min-h-[3.25rem] items-center gap-2 py-1.5 pl-1 pr-1">
            <button type="button" className="min-w-0 flex-1 text-left" onClick={() => setOpen(!open)}>
              <div className="truncate font-medium">{o.name}</div>
              <div className="truncate text-sm text-muted-foreground">
                {o.tracks.length} songs{have ? ` · ${have} in the library` : ""}
                {o.tracks.length > have ? ` · ${o.tracks.length - have} to prepare` : ""} · {open ? "hide" : "show"}{" "}
                them
              </div>
            </button>
            <div className="flex shrink-0 items-center gap-0.5">
              <Button
                size="sm"
                variant="outline"
                className="h-9 rounded-lg px-2.5"
                disabled={!o.tracks.length}
                onClick={() => go(false)}
                title="Every song into the library, prepared for later"
              >
                <LibraryIcon className="h-4 w-4 sm:mr-1" />
                <span className="hidden sm:inline">Add all</span>
              </Button>
              <IconBtn
                label="Every song into the library and the queue"
                onClick={() => go(true)}
                disabled={!o.tracks.length}
              >
                <ListEnd className="h-4 w-4" />
              </IconBtn>
            </div>
          </div>
          {open && (
            <ul className="max-h-48 overflow-y-auto px-1 text-sm text-muted-foreground">
              {o.tracks.map((t, i) => (
                <li key={i} className="truncate py-0.5">
                  {t.title} · {t.artist}
                  {t.have ? " · in the library" : ""}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}

/** A song's cover, or a placeholder tile of the same size (a note on a plain square) when the catalogues had none. */
function Cover({ src, big }: { src?: string | null; big?: boolean }) {
  const size = big ? "h-11 w-11" : "h-9 w-9";
  return src ? (
    <img src={src} alt="" loading="lazy" className={cn(size, "shrink-0 rounded-md object-cover")} />
  ) : (
    <div
      aria-hidden
      className={cn(size, "flex shrink-0 items-center justify-center rounded-md bg-stone-200/80 text-stone-500")}
    >
      <Music className={big ? "h-5 w-5" : "h-4 w-4"} />
    </div>
  );
}

function SectionWord({ children }: { children: ReactNode }) {
  return (
    <div className="px-1 pb-0.5 pt-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
      {children}
    </div>
  );
}

/** Add (to the end of the queue: singers pick a song and wait their turn), with Up next (the exception: it jumps the
 *  queue) and, for a song not yet in the library, Library only (get it ready for later, not queued). */
function AtButtons({ onAt, onLibrary }: { onAt: (at: QueueAt) => void; onLibrary?: () => void }) {
  return (
    <div className="flex shrink-0 items-center gap-0.5">
      <Button
        size="sm"
        className="h-9 rounded-lg px-2.5 sm:px-3"
        onClick={() => onAt("end")}
        title="Add to the end of the queue"
        aria-label="Add to the queue"
      >
        <ListEnd className="h-4 w-4 sm:mr-1" />
        <span className="hidden sm:inline">Add</span>
      </Button>
      <IconBtn label="Up next: play it after the one playing" onClick={() => onAt("next")}>
        <ListStart className="h-4 w-4" />
      </IconBtn>
      {onLibrary && (
        <IconBtn label="Library only: get it ready, don't queue it" onClick={onLibrary}>
          <LibraryIcon className="h-4 w-4" />
        </IconBtn>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- settings, bits

/** Rolled up until opened (owner, 25 Nov): play on, the speaker delay and its calibration, this song's lyrics
 *  timing, and the room mic. */
function Settings({
  st,
  songs,
  act,
  dep,
}: {
  st: KaraokeState;
  songs: Map<string, KaraokeSong>;
  act: Act;
  dep: DeploymentInfo | null;
}) {
  const screens = (dep?.screens ?? []).filter((s) => s.live);
  // Where the sound can go: the screen, nowhere, or this installation's Sonos rooms (the Music page's).
  const [outputs, setOutputs] = useState<{ id: string; label: string }[] | null>(null);
  useEffect(() => {
    karaoke.outputs().then(
      (o) => setOutputs(o.outputs),
      () => undefined,
    );
  }, []);
  const target = karaokeScreen(dep, st.settings.screen);
  const d = st.settings.audio_delay_ms ?? 0;
  const cal = st.calibration;
  const calRunning = cal?.state === "starting" || cal?.state === "running";
  const song = st.player.song_id && st.player.state !== "stopped" ? (songs.get(st.player.song_id) ?? null) : null;
  const off = (song?.user_offset_ms ?? 0) / 1000;
  const nudge = (ms: number) => song && act(() => karaoke.nudge(song.id, (song.user_offset_ms ?? 0) + ms));
  return (
    <section className={cn(CARD_CLASS, "px-4 py-1 sm:px-5")}>
      <Unroll title="Settings" defaultOpen={calRunning} testId="karaoke-settings">
        <div className="space-y-3 pb-3 text-sm">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span>
              Sung words <span className="text-muted-foreground">(on the screen)</span>
            </span>
            <div className="flex rounded-full border border-stone-200 p-0.5" role="group" aria-label="Sung words">
              {HIGHLIGHT_OPTIONS.map(([v, label]) => (
                <button
                  key={v}
                  type="button"
                  onClick={() => act(() => karaoke.settings({ highlight: v }))}
                  className={cn(
                    "rounded-full px-3 py-1 text-xs font-medium",
                    (st.settings.highlight ?? "words") === v
                      ? "bg-primary text-primary-foreground"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
          {/* Where the karaoke plays (owner, 26 Nov): the screen that shows the lyrics, and where its sound goes. */}
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span>
              Screen <span className="text-muted-foreground">(shows the lyrics)</span>
            </span>
            <select
              value={target ?? ""}
              onChange={(e) => act(() => karaoke.settings({ screen: e.target.value || null }))}
              aria-label="Karaoke screen"
              className="rounded-lg border border-stone-200 bg-card px-2.5 py-1.5 text-sm"
            >
              {screens.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span>Sound</span>
            <select
              value={st.settings.output ?? "screen"}
              onChange={(e) => act(() => karaoke.settings({ output: e.target.value }))}
              aria-label="Karaoke sound"
              className="rounded-lg border border-stone-200 bg-card px-2.5 py-1.5 text-sm"
            >
              {(
                outputs ?? [
                  { id: "screen", label: "From the screen's own page" },
                  { id: "none", label: "No sound (screens only)" },
                ]
              ).map((o) => (
                <option key={o.id} value={o.id}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>
          <label className="flex items-center justify-between gap-3">
            <span>
              Child mode{" "}
              <span className="text-muted-foreground">(swear words shown as dashes and muted, in the effects too)</span>
            </span>
            <Switch
              checked={!!st.settings.child_mode}
              onCheckedChange={(v) =>
                act(() => karaoke.settings({ child_mode: v }), v ? "Child mode on" : "Child mode off")
              }
            />
          </label>
          <label className="flex items-center justify-between gap-3">
            <span>Play on through the queue</span>
            <Switch
              checked={st.settings.autoplay}
              onCheckedChange={(v) => act(() => karaoke.settings({ autoplay: v }))}
            />
          </label>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span>
              Speaker delay <span className="text-muted-foreground">(Bluetooth: the lyrics wait this long)</span>
            </span>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={calRunning}
                title="Plays ten chirps on the speaker and times them with the room mic"
                onClick={() =>
                  window.confirm(
                    "Calibrate the speaker delay? The song pauses, and the speaker plays ten chirps (about 10 s). Keep the room quiet.",
                  ) && act(() => karaoke.calibrate())
                }
              >
                {calRunning ? <Spinner className="mr-1" /> : <Gauge className="mr-1 h-4 w-4" />} Calibrate
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => act(() => karaoke.settings({ audio_delay_ms: d - 20 }))}
              >
                −
              </Button>
              <span className="w-16 text-center tabular-nums">{d} ms</span>
              <Button
                variant="outline"
                size="sm"
                onClick={() => act(() => karaoke.settings({ audio_delay_ms: d + 20 }))}
              >
                +
              </Button>
            </div>
          </div>
          {st.sonos_error && (st.settings.output ?? "").startsWith("sonos:") && (
            <p className="text-sm text-red-700">Sonos: {st.sonos_error}</p>
          )}
          {cal?.message && (
            <p className={cn("text-sm", cal.state === "failed" ? "text-red-700" : "text-muted-foreground")}>
              {calRunning ? "Calibrating: " : ""}
              {cal.message}
            </p>
          )}
          {song?.lyrics?.kind === "synced" && (
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="min-w-0">
                Lyrics timing <span className="text-muted-foreground">(this song: {song.title})</span>
              </span>
              <div className="flex items-center gap-2">
                <Button variant="outline" size="sm" onClick={() => nudge(-250)}>
                  Earlier
                </Button>
                <span className="w-16 text-center tabular-nums">{off.toFixed(2).replace(/^(?!-)/, "+")} s</span>
                <Button variant="outline" size="sm" onClick={() => nudge(250)}>
                  Later
                </Button>
              </div>
            </div>
          )}
          {/* For checking the room mic (it comes on while a song plays): rolled up, nothing runs until opened. */}
          <Unroll title="Room mic and levels" className="-mx-1" testId="karaoke-mic">
            <RoomMicCheck />
          </Unroll>
        </div>
      </Unroll>
    </section>
  );
}

function Card({ title, action, children }: { title: string; action?: ReactNode; children: ReactNode }) {
  return (
    <section className={cn(CARD_CLASS, "p-4 sm:p-5")}>
      <div className={cn("flex items-center justify-between gap-2", action ? "-mr-2 -mt-2 mb-1" : "mb-3")}>
        <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">{title}</h2>
        {action}
      </div>
      {children}
    </section>
  );
}

/** A song still being prepared, prepared again from the start (owner, 26 Nov: "no way to restart an extraction. it's
 *  just stuck"): its running step ends, its files go, it waits its turn afresh. */
function RestartBtn({ song, act }: { song: KaraokeSong; act: Act }) {
  return (
    <IconBtn
      label="Restart: prepare it again from the start"
      quiet
      onClick={() =>
        window.confirm(`Prepare “${song.title}” again from the start?`) &&
        act(() => karaoke.retry(song.id), `Preparing “${song.title}” again`)
      }
    >
      <RotateCcw className="h-4 w-4" />
    </IconBtn>
  );
}

function IconBtn({
  label,
  onClick,
  disabled,
  quiet,
  children,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  quiet?: boolean;
  children: ReactNode;
}) {
  return (
    <Button
      variant="ghost"
      size="icon"
      className={cn("h-10 w-10 rounded-lg", quiet && "text-muted-foreground/60 hover:text-red-700")}
      title={label}
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
    >
      {children}
    </Button>
  );
}
