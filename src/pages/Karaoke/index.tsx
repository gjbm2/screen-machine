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

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
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
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import {
  AudioLines,
  ChevronDown,
  Music,
  Gauge,
  GripVertical,
  History,
  Library as LibraryIcon,
  ListEnd,
  ListStart,
  Pause,
  Play,
  Plus,
  RotateCcw,
  Search,
  SkipBack,
  SkipForward,
  Square,
  Trash2,
  Users,
  Volume2,
  VolumeX,
  WandSparkles,
  MicVocal,
  X,
  MessageSquareText,
  MoreHorizontal,
  Check,
} from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { SongShowNote } from "@/components/show/SongShowNote";
import { AppMenu } from "@/components/nav/AppMenu";
import { PageHeader } from "@/components/nav/PageHeader";
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
import { dropIndex, insertIndex, moved, parts, playNextIndex, queuedCounts, whereOf, type Where } from "./queueView";
import { LivePicture } from "./LivePicture";
import { LightsPanel } from "@/components/room/LightsNow";
import { roomApi } from "@/components/room/roomLink";
import { SongPictures } from "./SongPictures";
import { LoopbackToggle, RoomMicCheck } from "./RoomMic";
import { Roster, SingerAsk, SingerTag, askSingers, setPhotos, setSingerFor } from "./Singer";
import { OutputTiming } from "@/components/sound/OutputTiming";
import { OutputEq } from "@/components/sound/OutputEq";
import { SoundOutputSelect } from "@/components/sound/SoundOutputSelect";
import { setKaraokeHere } from "@/components/player/KaraokeHere";
import { myOutput } from "@/pages/Room/listenStream";
import type { SoundOutput } from "@/shared/api/sound";

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
  // One singer's songs only in the queue (owner, 28 Nov: from their chip in Singers), else null: everyone's.
  const [only, setOnly] = useState<string | null>(null);
  useEffect(() => {
    setSingerFor(only);
    return () => setSingerFor(null);
  }, [only]);
  const [err, setErr] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [searchOpen, setSearchOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  // The page is zones, as a music or a messaging app (owner, 28 Nov: "song nav which should pin stably and properly";
  // "a search ux pattern which ... doesn't jump about, doesn't move to a different location on the screen"): a frame
  // the size of what is visible, its middle the only thing that scrolls, the search bar its foot. Nothing is laid
  // over a scrolling page or placed by where the scroll is, so nothing pops, slides or jumps.
  //   - The player (a phone): one card, pinned to the top of the scrolling part once it gets there and squeezed by
  //     the scroll down to its slim row (PinnedPlayer; "it just squeezes when it's pinned"; "one ... component which
  //     squeezed, not two").
  //   - The search bar: the frame's foot, at every width; a click or a tap never moves it. A phone's keyboard makes
  //     the frame shorter (useKeyboardFit), so the bar sits on the keyboard; its results are a tray over the middle.
  const wide = useWide();
  const shellRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const songRowRef = useRef<HTMLDivElement>(null);
  const playerRef = useRef<HTMLDivElement>(null);
  const [trayHost, setTrayHost] = useState<HTMLDivElement | null>(null);
  const [trayTop, setTrayTop] = useState(0);
  /** The pinned player's squeeze (PinnedPlayer): how far it goes (px of scroll) and its height once slim. */
  const squeeze = useRef({ range: 0, pinned: 0 });
  useKeyboardFit(shellRef);
  useLayoutProbe();
  useEffect(() => {
    // Android: the browser itself makes the page shorter for the keyboard (interactive-widget=resizes-content), so
    // the frame's own CSS height puts its foot on the keyboard. It is also what makes Brave tell the truth: with its
    // bottom toolbar on, the keyboard hides the toolbar but the visible height it reports still leaves the toolbar
    // out, 46-48 px short (brave/brave-browser#60074; the owner's phone, 28 Nov: a band of the page's backing under
    // the search bar). An iPhone ignores the setting and keeps useKeyboardFit's fit. On this page only: other pages
    // keep their bottom bars under the keyboard.
    const meta = document.querySelector<HTMLMetaElement>('meta[name="viewport"]');
    if (!meta) return;
    const was = meta.content;
    if (!/interactive-widget/.test(was)) meta.content = `${was}, interactive-widget=resizes-content`;
    return () => {
      meta.content = was;
    };
  }, []);
  useEffect(() => {
    // the frame is the page: the document behind it never scrolls, and is the page's colour (not the app's white),
    // so anything a browser shows of it beyond the frame reads as the page
    const root = document.documentElement, was = root.style.overflow;
    const body = document.body, wasBg = body.style.backgroundColor;
    root.style.overflow = "hidden";
    const paper = shellRef.current ? getComputedStyle(shellRef.current).backgroundColor : "";
    if (paper) body.style.backgroundColor = paper;
    return () => {
      root.style.overflow = was;
      body.style.backgroundColor = wasBg;
    };
  }, []);
  useLayoutEffect(() => {
    // the scrolling part's scrollbar (a desktop's): the bar and the tray line up with the page beside it
    const sc = scrollRef.current;
    if (sc) shellRef.current?.style.setProperty("--sbw", `${sc.offsetWidth - sc.clientWidth}px`);
  }, [wide]);
  // The tray opened on a phone: it starts under the player squeezed to its slim row, so the page is rolled up until
  // the player is that (the tray is one size every time). Closed without a song added, the page rolls back.
  const savedTop = useRef<number | null>(null);
  const onSearchOpen = useCallback(
    (on: boolean) => {
      const sc = scrollRef.current, place = playerRef.current;
      if (wide || !sc) return;
      if (!on) {
        if (savedTop.current != null) sc.scrollTo({ top: savedTop.current, behavior: "smooth" });
        savedTop.current = null;
        return;
      }
      if (!place) return setTrayTop(0);
      const { range, pinned } = squeeze.current;
      setTrayTop(pinned);
      const gone = sc.getBoundingClientRect().top - place.getBoundingClientRect().top;
      if (gone < range) {
        savedTop.current = sc.scrollTop;
        sc.scrollTo({ top: sc.scrollTop + (range - gone), behavior: "smooth" });
      }
    },
    [wide],
  );
  useLayoutEffect(() => onSearchOpen(searchOpen), [searchOpen, onSearchOpen]);
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
      setPhotos(s.singer_photos);
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
  // Only what is still to come counts as "in the queue": the songs already sung are history (owner, 27 Nov).
  const queuedCount = st ? queuedCounts(st) : new Map<string, number>();
  const upcoming = st ? queue.length - parts(st).sung : 0;

  const queueBox = useRef<HTMLDivElement>(null);
  const backToQueue = () => {
    savedTop.current = null;
    setArtist(null);
    setSearchOpen(false);
    (document.activeElement as HTMLElement | null)?.blur?.();
    requestAnimationFrame(() => queueBox.current?.scrollIntoView({ block: "start", behavior: "smooth" }));
  };
  const addToQueue = (s: KaraokeSong, at: At) =>
    at === "now"
      ? void (backToQueue(), act(() => playNow(s), said(`“${s.title}”`, "now", s.status !== "ready")))
      : void askSingers(s.title).then(
      (who) =>
        who &&
        (backToQueue(), true) &&
        act(
          () => karaoke.queueAdd(s.id, undefined, at === "next", who),
          at === "next" ? `“${s.title}” is up next` : `“${s.title}” added to the queue`,
        ),
    );

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
    // Where it lands is queueView's to say (the server's own rules: nothing above the top of the songs to come, where
    // it would vanish among the sung; the song that is on doesn't move).
    const p = parts(st);
    if (id.startsWith(LIB)) {
      const songId = id.slice(LIB.length);
      const at = insertIndex(p, queue.length, overIndex);
      void askSingers(songs.get(songId)?.title ?? "").then((who) => {
        if (!who) return;
        const q = [...queue];
        q.splice(at, 0, { qid: `pending-${Date.now()}`, song_id: songId, singers: who });
        setSt({ ...st, queue: q });
        void act(() => karaoke.queueAdd(songId, at, undefined, who));
      });
      return;
    }
    const from = queue.findIndex((e) => e.qid === id);
    const to = dropIndex(p, queue.length, from, overIndex);
    if (to === null) return;
    setSt({ ...st, ...moved(queue, p, from, to) });
    void act(() => karaoke.queueMove(id, to));
  };

  const dragSong = dragging
    ? songs.get(
        dragging.startsWith(LIB) ? dragging.slice(LIB.length) : (queue.find((e) => e.qid === dragging)?.song_id ?? ""),
      )
    : undefined;

  const queueCard = st && (
    <QueueCard
      st={st}
      songs={songs}
      act={act}
      addToQueue={addToQueue}
      onFind={() => setSearchOpen(true)}
      onArtist={setArtist}
      only={only}
      onOnly={setOnly}
    />
  );
  const libraryCard = st && (
    <LibraryCard
      songs={st.songs}
      queuedCount={queuedCount}
      draggable={false}
      act={act}
      addToQueue={addToQueue}
      onArtist={setArtist}
    />
  );
  const findCard = (
    <Find
      songs={st?.songs ?? NO_SONGS}
      queuedCount={queuedCount}
      act={act}
      inputRef={findRef}
      onArtist={setArtist}
      onAdded={backToQueue}
      wide={wide}
      trayHost={trayHost}
      trayTop={trayTop}
      open={searchOpen}
      setOpen={setSearchOpen}
      library={libraryCard}
    />
  );

  return (
    <div className={cn(THEME_CLASS, "text-base text-foreground antialiased")}>
      <RoomThemeStyle />
      {/* the frame: under the app's top bar, over its bottom bar and the docked player; a keyboard's height shorter
          while one is up (--kv-top / --kv-h: useKeyboardFit) */}
      <div
        ref={shellRef}
        className="fixed inset-x-0 flex h-screen flex-col overflow-hidden bg-background"
        style={{
          top: "var(--kv-top, calc(var(--app-top, 0px) + var(--app-nav-top, 0px)))",
          height:
            "var(--kv-h, calc(100dvh - var(--app-top, 0px) - var(--app-nav-top, 0px) - var(--app-nav-bottom, 0px) - var(--dock-h, 0px)))",
        }}
        data-testid="karaoke-shell"
      >
      <div ref={setTrayHost} className="relative min-h-0 flex-1 overflow-hidden">
      <div
        ref={scrollRef}
        className="h-full overflow-y-auto overscroll-contain [scrollbar-gutter:stable]"
        data-testid="karaoke-scroll"
      >
      <div className="mx-auto w-full max-w-3xl px-4 pb-6 sm:px-6 lg:max-w-7xl">
        <PageHeader title="Karaoke" current="karaoke" onMenu={() => setMenuOpen(true)} />
        {err && (
          <Notice tone="bad" className="mb-3">
            Can't reach the karaoke: {err}
          </Notice>
        )}

        {/* Now playing first (a wide screen: sticky beside the lists; a phone: the one pinned card that squeezes),
            then the queue and the singers; the picture, the lights and the settings below. The search bar is the
            frame's foot, the library under its icon. */}
        {!wide && st && (
          <PinnedPlayer
            scroller={scrollRef}
            boxRef={songRowRef}
            placeRef={playerRef}
            onMeasure={(m) => {
              squeeze.current = m;
            }}
            full={<NowPlaying st={st} skew={skewRef.current} songs={songs} act={act} bare />}
            slim={
              <NowPlaying
                st={st}
                skew={skewRef.current}
                songs={songs}
                act={act}
                part="row"
                bare
                onTop={() => scrollRef.current?.scrollTo({ top: 0, behavior: "smooth" })}
              />
            }
          />
        )}
        <main className="space-y-4 lg:grid lg:grid-cols-[minmax(0,26rem)_minmax(0,1fr)] lg:items-start lg:gap-6 lg:space-y-0">
          {wide && (
            <div className="sticky top-4" data-testid="karaoke-player">
              {st && <NowPlaying st={st} skew={skewRef.current} songs={songs} act={act} />}
            </div>
          )}
          <DndContext
            sensors={sensors}
            collisionDetection={closestCenter}
            onDragStart={onDragStart}
            onDragEnd={onDragEnd}
            onDragCancel={endDrag}
          >
            <div className="space-y-4">
              <div ref={queueBox} className="scroll-mt-20">{queueCard}</div>
              {st && (
                <Card
                  title={`Singers${st.singers?.length ? ` · ${st.singers.length}` : ""}`}
                  action={
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-9 rounded-lg px-2.5 text-muted-foreground"
                      onClick={() =>
                        window.confirm("New session? Tonight's names and the songs already sung are cleared.") &&
                        act(() => karaoke.newSession(), "New session")
                      }
                    >
                      <RotateCcw className="mr-1 h-4 w-4" /> New session
                    </Button>
                  }
                >
                  <Roster
                    names={st.singers ?? []}
                    onChange={(add, remove) => {
                      if (only && remove.some((n) => n.toLowerCase() === only.toLowerCase())) setOnly(null);
                      act(() => karaoke.roster({ add, remove }));
                    }}
                    onShow={(n) => {
                      setOnly(n);
                      queueBox.current?.scrollIntoView({ behavior: "smooth", block: "start" });
                    }}
                  />
                </Card>
              )}
            </div>
            <DragOverlay>{dragSong ? <RowFace song={dragSong} lifted /> : null}</DragOverlay>
            {st && <SingerAsk names={st.singers ?? []} />}
          </DndContext>
        </main>

        <div className="mt-4 space-y-4 lg:mt-6 lg:grid lg:grid-cols-2 lg:items-start lg:gap-6 lg:space-y-0">
          {/* the screen, then the room's lights: a card of their own, they belong to the room (owner, 28 Nov); through
              the phones' tunnel too (room.yaml tunnel_host_suffixes) */}
          <div className="space-y-4">
            <LivePicture screen={st ? karaokeScreen(dep, st.settings.screen) : null} />
            <section className={cn(CARD_CLASS, "px-4 py-1 sm:px-5")}>
              <LightsPanel api={roomApi()} />
            </section>
          </div>
          {st && <Settings st={st} songs={songs} act={act} dep={dep} />}
          {st?.player.song_id && <SongPictures songId={st.player.song_id} />}
        </div>
      </div>
      </div>
      {/* the tray (the library, the search's results) is put here by Find, over the scrolling part */}
      </div>
      {findCard}
      </div>
      <AppMenu open={menuOpen} onOpenChange={setMenuOpen} current="karaoke" />
      {st && artist && <ArtistSongs name={artist} songs={st.songs} act={act} onClose={() => setArtist(null)} onAdded={backToQueue} />}
      {toast && (
        <div className="fixed inset-x-0 bottom-[calc(max(0.75rem,env(safe-area-inset-bottom))+4.75rem+var(--app-nav-bottom,0px))] z-50 mx-auto w-fit max-w-[calc(100vw-2rem)] rounded-full bg-stone-900/90 px-4 py-2.5 text-[15px] font-medium text-white shadow-lg">
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
  part = "full",
  onTop,
  bare = false,
}: {
  st: KaraokeState;
  skew: number;
  songs: Map<string, KaraokeSong>;
  act: Act;
  /** The whole player, or its slim row, pinned on a phone (the song, play and next, a line for where it is). */
  part?: "full" | "row";
  /** The song row's song was tapped: back to the top of the page. */
  onTop?: () => void;
  /** Without the card's frame: inside the pinned card (PinnedPlayer), which is the frame. */
  bare?: boolean;
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

  // The singer's voice (the lead vocal under the backing) and the mute, at the heading's right.
  const voice = (
    <div className="flex items-center gap-2">
      <button
        type="button"
        onClick={() => act(() => karaoke.settings({ muted: !st.settings.muted }))}
        aria-label={st.settings.muted ? "Unmute" : "Mute"}
        aria-pressed={!!st.settings.muted}
        title={st.settings.muted ? "Unmute" : "Mute"}
        className={cn(
          "rounded-full p-1",
          st.settings.muted ? "bg-red-100 text-red-700" : "text-muted-foreground hover:text-foreground",
        )}
      >
        {st.settings.muted ? <VolumeX className="h-4 w-4" /> : <Volume2 className="h-4 w-4" />}
      </button>
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
  );
  const scrubBar = showing && (
    <div className="flex items-center gap-2 text-xs tabular-nums text-muted-foreground">
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
  );

  // Pinned on a phone (owner, 26 Nov: "the version of the player section that gets pinned may need to be
  // vertically smaller"): one slim row, the song, play/pause and next, a thin line for where it is.
  if (part === "row")
    return (
      <section className={cn(!bare && CARD_CLASS, "overflow-hidden p-2")}>
        <div className="flex items-center gap-2.5">
          {/* The song: back up to the top (owner, 26 Nov: "clicking current song should scroll to top"). */}
          <button
            type="button"
            className="flex min-w-0 flex-1 items-center gap-2.5 text-left"
            onClick={onTop}
            aria-label="Back to the top"
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
    <section className={cn(!bare && CARD_CLASS, "p-3 sm:p-4")}>
      {/* The heading, with the singer's voice at its right (it stays put while songs change). */}
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">Now playing</h2>
        {voice}
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
      {showing && song.status === "ready" && <RefineLine song={song} act={act} />}
      {scrubBar && <div className="mt-2">{scrubBar}</div>}
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

/** A phone's player (owner, 26 Nov: "player component should probably pin to top on mobile"; 28 Nov: "one ...
 *  component which squeezed, not two"): one card, pinned to the top of the scrolling part from the moment it gets
 *  there. From then the scroll squeezes it: its foot rises with the page under it until it is the slim row's height,
 *  the whole player giving way to the slim row inside the one frame. Its place in the page is a space of the full
 *  height that never changes, so nothing it does moves the page (26 Nov: an in-page version that changed size
 *  flickered). */
function PinnedPlayer({
  scroller,
  boxRef,
  placeRef,
  onMeasure,
  full,
  slim,
}: {
  scroller: React.RefObject<HTMLDivElement>;
  /** The pinned box (the card and the plain backing round it). */
  boxRef: React.RefObject<HTMLDivElement>;
  /** The player's place in the page. */
  placeRef: React.RefObject<HTMLDivElement>;
  /** How far the squeeze goes (px of scroll) and the box's height once slim. */
  onMeasure: (m: { range: number; pinned: number }) => void;
  full: ReactNode;
  slim: ReactNode;
}) {
  const fullRef = useRef<HTMLDivElement>(null);
  const slimRef = useRef<HTMLDivElement>(null);
  const [h, setH] = useState({ full: 204, slim: 58, top: 8 });
  const onMeasureRef = useRef(onMeasure);
  onMeasureRef.current = onMeasure;
  useLayoutEffect(() => {
    const f = fullRef.current, s = slimRef.current, box = boxRef.current;
    if (!f || !s || !box) return;
    const measure = () => {
      const next = { full: f.offsetHeight, slim: s.offsetHeight, top: parseFloat(getComputedStyle(box).paddingTop) || 0 };
      setH((was) => (was.full === next.full && was.slim === next.slim && was.top === next.top ? was : next));
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(f);
    ro.observe(s);
    return () => ro.disconnect();
  }, [boxRef]);
  const range = Math.max(0, h.full - h.slim);
  useLayoutEffect(() => {
    const sc = scroller.current, place = placeRef.current, box = boxRef.current;
    const f = fullRef.current, s = slimRef.current;
    if (!sc || !place || !box || !f || !s) return;
    onMeasureRef.current({ range, pinned: h.top + h.slim + 8 });
    // how far the player's place has gone above the top: that much is squeezed out of the card (--sq px; --sqp 0..1)
    const fit = () => {
      const gone = Math.min(range, Math.max(0, sc.getBoundingClientRect().top - place.getBoundingClientRect().top));
      const p = range ? gone / range : 0;
      box.style.setProperty("--sq", `${gone}px`);
      box.style.setProperty("--sqp", p.toFixed(3));
      f.style.visibility = p >= 0.65 ? "hidden" : "";
      f.style.pointerEvents = p > 0.5 ? "none" : "";
      s.style.visibility = p <= 0.35 ? "hidden" : "";
      s.style.pointerEvents = p > 0.5 ? "" : "none";
    };
    fit();
    sc.addEventListener("scroll", fit, { passive: true });
    window.addEventListener("resize", fit);
    return () => {
      sc.removeEventListener("scroll", fit);
      window.removeEventListener("resize", fit);
    };
  }, [scroller, placeRef, boxRef, range, h.top, h.slim]);
  return (
    <>
      <div className="sticky top-0 z-30 h-0">
        <div
          ref={boxRef}
          className="absolute inset-x-0 top-0 -mx-4 bg-background px-4 pb-2 pt-[max(0.5rem,env(safe-area-inset-top))] sm:-mx-6 sm:px-6"
          data-testid="karaoke-song-row"
        >
          <div
            className={cn(CARD_CLASS, "relative overflow-hidden")}
            style={{ height: `calc(${h.full}px - var(--sq, 0px))` }}
            data-testid="karaoke-player-card"
          >
            <div ref={fullRef} className="absolute inset-x-0 top-0" style={{ opacity: "clamp(0, calc(1.55 - var(--sqp, 0) * 2.4), 1)" }}>
              {full}
            </div>
            <div ref={slimRef} className="absolute inset-x-0 top-0" style={{ opacity: "clamp(0, calc(var(--sqp, 0) * 2.4 - 0.85), 1)" }}>
              {slim}
            </div>
          </div>
        </div>
      </div>
      <div ref={placeRef} aria-hidden className="mb-4" style={{ height: h.top + h.full }} data-testid="karaoke-player" />
    </>
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

/** Where a library song goes: the end of the queue, up next, or on now. */
type At = QueueAt | "now";
const playNow = (s: KaraokeSong) => karaoke.player("play", { song_id: s.id });
/** What to say once a song has gone where it was sent (``preparing``: it isn't ready yet). */
const said = (what: string, at: At | null, preparing: boolean) =>
  at === "now"
    ? preparing
      ? `${what} plays as soon as it's ready`
      : undefined
    : `${what} ${at === "next" ? "is up next" : at === "end" ? "added to the queue" : "added to the library"}${preparing ? ": getting it ready" : ""}`;

/** The running order. Songs already sung are tucked away (owner, 25 Nov: "previously played songs aren't very
 *  interesting -- should probably disappear unless user wants to find them"): a line at the top shows them. */
function QueueCard({
  st,
  songs,
  act,
  addToQueue,
  onFind,
  onArtist,
  only,
  onOnly,
}: {
  st: KaraokeState;
  songs: Map<string, KaraokeSong>;
  act: Act;
  addToQueue: (s: KaraokeSong, at: At) => void;
  onFind: () => void;
  onArtist: (a: string) => void;
  /** Only this singer's songs (null: everyone's). */
  only: string | null;
  onOnly: (n: string | null) => void;
}) {
  const { setNodeRef, isOver } = useDroppable({ id: QUEUE_DROP });
  const [showPlayed, setShowPlayed] = useState(false);
  const queue = st.queue;
  const p = parts(st);
  const theirs = (e: QueueEntry) => !only || (e.singers ?? []).some((n) => n.toLowerCase() === only.toLowerCase());
  const played = queue.slice(0, p.sung).filter(theirs).length;
  const shown = queue.map((e, i) => ({ e, i })).filter(({ e, i }) => theirs(e) && (showPlayed || i >= p.sung));
  const upcoming = queue.slice(p.sung).filter(theirs).length;
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
        {only && (
          <div className="mb-1 flex">
            <button
              type="button"
              onClick={() => onOnly(null)}
              aria-label="Everyone's songs"
              data-testid="karaoke-queue-only"
              className="flex max-w-full items-center gap-1 rounded-full bg-primary/10 py-0.5 pl-2.5 pr-1 text-sm font-medium"
            >
              <MicVocal className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
              <span className="truncate">{only}'s songs</span>
              <X className="h-4 w-4 shrink-0 text-muted-foreground" />
            </button>
          </div>
        )}
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
          <div className="pb-2 pt-5 text-center text-sm text-muted-foreground">
            {only
              ? `Nothing more for ${only}.`
              : queue.length
                ? "Nothing more in the queue."
                : "The queue is empty."}
          </div>
        )}
        <SortableContext items={shown.map(({ e }) => e.qid)} strategy={verticalListSortingStrategy}>
          <ul className="divide-y divide-stone-200/70">
            {shown.map(({ e, i }) => {
              const song = songs.get(e.song_id);
              if (!song) return null;
              const where = whereOf(i, p);
              return (
                <QueueRow
                  key={e.qid}
                  entry={e}
                  song={song}
                  where={where}
                  nextAt={playNextIndex(i, p)}
                  act={act}
                  addToQueue={addToQueue}
                  onArtist={onArtist}
                  singers={st.singers ?? []}
                />
              );
            })}
          </ul>
        </SortableContext>
        {/* how a song gets in, for whoever hasn't found the bar at the foot (owner, 28 Nov: "a low key, slim '+' at
            the bottom of the queue which triggers the tray") */}
        <button
          type="button"
          onClick={onFind}
          data-testid="karaoke-queue-add"
          className="flex h-6 w-full items-center justify-center gap-1 rounded-md text-xs text-muted-foreground/70 hover:bg-stone-100 hover:text-foreground"
        >
          <Plus className="h-3.5 w-3.5" /> Add song
        </button>
      </div>
    </Card>
  );
}

function QueueRow({
  entry,
  song,
  where,
  nextAt,
  act,
  addToQueue,
  onArtist,
  singers,
}: {
  entry: QueueEntry;
  song: KaraokeSong;
  where: Where;
  /** Where "Play next" puts this entry (straight after the song that is on); undefined when it is there already. */
  nextAt?: number;
  act: Act;
  addToQueue: (s: KaraokeSong, at: At) => void;
  onArtist: (a: string) => void;
  /** Tonight's names: the singer tag's suggestions. */
  singers: string[];
}) {
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, transform, transition, isDragging } = useSortable({
    id: entry.qid,
  });
  const [noting, setNoting] = useState(false);       // the show's interpretation of this song: the owner's verdict
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
        {/* three to see (owner, 28 Nov): who sings (a song already sung: Add), Play now, and "…" for the rest */}
        {where !== "played" ? (
          <SingerTag
            singers={entry.singers ?? []}
            names={singers}
            onChange={(n) => act(() => karaoke.queueSingers(entry.qid, n))}
          />
        ) : (
          song.status !== "failed" && <AddBtn onClick={() => addToQueue(song, "end")} />
        )}
        {song.status !== "failed" && where !== "current" && (
          <PlayBtn
            onClick={() =>
              act(
                () => karaoke.player("play", { qid: entry.qid }),
                said(`“${song.title}”`, "now", song.status !== "ready"),
              )
            }
          />
        )}
        <More testid="karaoke-queue-more">
            {where === "played" ? (
              // a song already sung goes in again (its place in what was sung stays)
              song.status !== "failed" && (
                <DropdownMenuItem onSelect={() => addToQueue(song, "next")}>
                  <ListStart className="mr-2 h-4 w-4" /> Play next
                </DropdownMenuItem>
              )
            ) : (
              where !== "current" && (
                <DropdownMenuItem
                  disabled={nextAt === undefined}
                  onSelect={() =>
                    nextAt !== undefined &&
                    act(() => karaoke.queueMove(entry.qid, nextAt), `“${song.title}” is up next`)
                  }
                >
                  <ListStart className="mr-2 h-4 w-4" /> Play next
                </DropdownMenuItem>
              )
            )}
            {(where === "current" || where === "played") && (
              <DropdownMenuItem onSelect={() => setNoting(!noting)}>
                <MessageSquareText className="mr-2 h-4 w-4" /> How the show played it
                {noting && <Check className="ml-2 h-4 w-4 text-primary" />}
              </DropdownMenuItem>
            )}
            {song.status !== "ready" && song.status !== "failed" && (
              <DropdownMenuItem
                onSelect={() =>
                  window.confirm(`Prepare “${song.title}” again from the start?`) &&
                  act(() => karaoke.retry(song.id), `Preparing “${song.title}” again`)
                }
              >
                <RotateCcw className="mr-2 h-4 w-4" /> Prepare again
              </DropdownMenuItem>
            )}
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={() => act(() => karaoke.queueRemove(entry.qid))}>
              <X className="mr-2 h-4 w-4" /> Take out of the queue
            </DropdownMenuItem>
        </More>
      </RowFace>
      {noting && <SongShowNote song={song.id} title={`${song.title} · ${song.artist}`} />}
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
}: {
  songs: KaraokeSong[];
  queuedCount: Map<string, number>;
  draggable: boolean;
  act: Act;
  addToQueue: (s: KaraokeSong, at: At) => void;
  onArtist: (a: string) => void;
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
  // In the chosen order. The search is the bar's (Find): typing there shows matches in this same tray.
  const shown = useMemo(
    () => sorted(songs, sort).filter((s) => inFilter(s, filter) && (!tag || (s.tags ?? []).includes(tag))),
    [songs, filter, sort, tag],
  );

  return (
      <div className="space-y-2.5">
        {/* Held at the top of the tray: the count and the order on one row, then readiness and tags as one row that
            slides sideways (owner, 27 Nov: the tags took a third of the tray). */}
        <div className="sticky top-0 z-10 -mx-1 space-y-2 bg-card px-1 pb-2">
          <div className="flex items-center justify-between gap-2">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
              Library · {songs.length}
            </h2>
            <select
              value={sort}
              onChange={(e) => setSort(e.target.value as Sort)}
              aria-label="Sort the library"
              className="h-9 shrink-0 rounded-xl border border-stone-200 bg-card px-2.5 text-xs text-muted-foreground hover:text-foreground"
            >
              {SORTS.map(([k, label]) => (
                <option key={k} value={k}>
                  {label}
                </option>
              ))}
            </select>
          </div>
        <div className="no-scrollbar -mx-1 flex items-center gap-1.5 overflow-x-auto px-1">
          {FILTERS.map(([f, label]) => {
            const n = songs.filter((s) => inFilter(s, f)).length;
            if (f !== "all" && n === 0 && filter !== f) return null;
            return (
              <button
                key={f}
                type="button"
                onClick={() => setFilter(f)}
                className={cn(
                  "shrink-0 whitespace-nowrap rounded-full border px-3 py-1 text-xs font-medium",
                  filter === f
                    ? "border-primary bg-primary text-primary-foreground"
                    : "border-stone-200 text-muted-foreground hover:text-foreground",
                )}
              >
                {label} {n}
              </button>
            );
          })}
          {tags.length > 0 && <span className="h-4 w-px shrink-0 bg-stone-200" aria-hidden />}
            {tags.map(([g, n]) => (
              <button
                key={g}
                type="button"
                onClick={() => setTag(tag === g ? null : g)}
                className={cn(
                  "shrink-0 whitespace-nowrap rounded-full px-2.5 py-1 text-xs",
                  tag === g ? "bg-stone-800 text-white" : "bg-stone-100 text-stone-600 hover:bg-stone-200",
                )}
              >
                {g} <span className="opacity-60">{n}</span>
              </button>
            ))}
        </div>
        </div>
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
  addToQueue: (s: KaraokeSong, at: At) => void;
  onArtist?: (a: string) => void;
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
            <AddBtn onClick={() => addToQueue(song, "end")} />
            <PlayBtn onClick={() => addToQueue(song, "now")} />
          </>
        )}
        <RowMenu song={song} act={act} onNext={failed ? undefined : () => addToQueue(song, "next")} />
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
        {/* A notch smaller on a phone (owner, 27 Nov: "make fonts slightly smaller for mobile display"). */}
        <div className="truncate text-[15px] font-medium sm:text-base">{song.title}</div>
        <div
          className={cn(
            "truncate text-[13px] sm:text-sm",
            song.status === "failed" ? "text-red-700" : "text-muted-foreground",
          )}
        >
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
          {song.status === "ready" && song.refine ? REFINE_WORDS[song.refine.state] : ""}
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
const WEB_FIRST = 8;
const WEB_MORE = 12;
const WEB_MAX = 40;

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
  onAdded,
  wide,
  trayHost,
  trayTop,
  open,
  setOpen,
  library,
}: {
  songs: KaraokeSong[];
  queuedCount: Map<string, number>;
  act: Act;
  inputRef: React.RefObject<HTMLInputElement>;
  onArtist: (a: string) => void;
  onAdded: () => void;
  /** A wide screen (the tray stands on the bar, in the lists' column) or a phone (it fills the page's middle). */
  wide: boolean;
  /** The page's middle, where the tray goes. */
  trayHost: HTMLElement | null;
  /** A phone: where the tray starts, under the song row (px from the top of ``trayHost``). */
  trayTop: number;
  /** The tray is open: from the box taking the focus or the library button until it is closed (its handle, the
   *  bar's button, Esc, a song queued, a click beside it). Not "while the box has the focus": the keyboard going
   *  away changes nothing. */
  open: boolean;
  setOpen: (open: boolean) => void;
  /** The library: what the tray holds while nothing is typed. */
  library: ReactNode;
}) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<{ q: string; rows: SearchHit[] } | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const seq = useRef(0);
  const text = q.trim();
  const link = isLink(text);
  // More results (owner, 27 Nov: "need 'see more' or endless scroll"): the library shows more on a click; the web
  // asks again for a longer list when its end scrolls into view, and only songs it hadn't shown are added, at the end.
  const [libMax, setLibMax] = useState(LIB_SHOWN);
  const [webMax, setWebMax] = useState(WEB_FIRST);
  const [moreBusy, setMoreBusy] = useState(false);
  const [webEnd, setWebEnd] = useState(false);
  useEffect(() => {
    setLibMax(LIB_SHOWN);
    setWebMax(WEB_FIRST);
    setWebEnd(false);
  }, [text]);
  const loadMore = () => {
    if (moreBusy || webEnd || !hits || hits.q !== text || busy) return;
    const n = seq.current, want = webMax + WEB_MORE;
    setMoreBusy(true);
    karaoke
      .search(text, want)
      .then((r) => {
        if (n !== seq.current) return;
        setHits((h) => {
          if (!h || h.q !== text) return h;
          const seen = new Set(h.rows.map((x) => x.key));
          const add = r.results.filter((x) => !seen.has(x.key));
          if (add.length === 0) setWebEnd(true);
          return { q: h.q, rows: [...h.rows, ...add] };
        });
        setWebMax(want);
        if (want >= WEB_MAX) setWebEnd(true);
      })
      .catch(() => setWebEnd(true))
      .finally(() => setMoreBusy(false));
  };
  const webFoot = useRef<HTMLDivElement>(null);
  const loadMoreRef = useRef(loadMore);
  loadMoreRef.current = loadMore;

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
        const r = await karaoke.search(text, WEB_FIRST);
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
  const byId = useMemo(() => new Map(songs.map((s) => [s.id, s])), [songs]);
  // Nothing moves under the pointer (owner, 27 Nov: "you cannot move things out from under a mouse pointer"): web
  // results only ever add below the library rows, the panel keeps one height while open, and while the pointer is
  // over the results what they show is held until it leaves or the search text changes.
  const liveView = {
    text,
    libRows: lib,
    web: (hits?.rows ?? []).filter((h) => !(h.have && libIds.has(h.have))),
    hits,
    busy,
    msg,
  };
  const [over, setOver] = useState(false);
  const held = useRef(liveView);
  if (!over || held.current.text !== text) held.current = liveView;
  else if (held.current.web.every((h, i) => liveView.web[i]?.key === h.key))
    held.current = { ...held.current, web: liveView.web, hits: liveView.hits };   // rows added at the end: fine
  const { libRows, web } = held.current;
  const shownHits = held.current.hits, shownBusy = held.current.busy, shownMsg = held.current.msg;
  const clear = () => {
    setQ("");
    inputRef.current?.focus();
  };

  // Queued (a name picked): the search closes and the page goes back to the queue at once (owner, 27 Nov: "drop back
  // to queue, not leave the selector open"); into the library only: the search clears when it's done.
  const go = (at: At | null, run: () => Promise<unknown>) => {
    if (!at) return void run().then(clear);
    inputRef.current?.blur();
    setQ("");
    onAdded();
    setOpen(false);
    void run();
  };
  const queueSong = (s: KaraokeSong, at: At) =>
    at === "now"
      ? go("end", () => act(() => playNow(s), said(`“${s.title}”`, "now", s.status !== "ready")))
      : void (inputRef.current?.blur(), askSingers(s.title)).then(
      (who) =>
        who &&
        go(at, () =>
          act(
            () => karaoke.queueAdd(s.id, undefined, at === "next", who),
            at === "next" ? `“${s.title}” is up next` : `“${s.title}” added to the queue`,
          ),
        ),
    );
  const addWeb = (h: SearchHit, at: At | null) =>
    void (at && at !== "now" ? (inputRef.current?.blur(), askSingers(h.title)) : Promise.resolve([])).then(
      (who) =>
        who &&
        go(at, () =>
          act(
            () => karaoke.request({ ...hitFacts(h), ...(at ? { queue: at, singers: who } : {}) }),
            said(`“${h.title}”`, at, true),
          ),
        ),
    );
  const addText = (at: At | null) =>
    void (at && at !== "now"
      ? (inputRef.current?.blur(), askSingers(link ? "This link" : text))
      : Promise.resolve([])
    ).then(
      (who) =>
        who &&
        go(at, () =>
          act(
            () =>
              karaoke.request({
                ...(link ? { url: text } : { q: text }),
                ...(at ? { queue: at, singers: who } : {}),
              }),
            said(link ? "The link" : `“${text}”`, at, true),
          ),
        ),
    );

  const hasText = link || text.length >= 1;
  const touchTimer = useRef<number | undefined>(undefined);
  useEffect(() => {
    const el = webFoot.current;
    if (!el) return;
    const io = new IntersectionObserver((es) => es.some((e) => e.isIntersecting) && loadMoreRef.current());
    io.observe(el);
    return () => io.disconnect();
  });
  // What was found: the library first, the web under it (never above: rows are only ever added at the end).
  const results = hasText && (
          <section
            onPointerEnter={(e) => e.pointerType === "mouse" && setOver(true)}
            onPointerLeave={(e) => e.pointerType === "mouse" && setOver(false)}
            // a finger has no hover: on the results from a touch until a few seconds after the last
            onPointerDown={(e) => {
              if (e.pointerType === "mouse") return;
              window.clearTimeout(touchTimer.current);
              setOver(true);
            }}
            onPointerUp={(e) => {
              if (e.pointerType === "mouse") return;
              window.clearTimeout(touchTimer.current);
              touchTimer.current = window.setTimeout(() => setOver(false), 4000);
            }}
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
                      {/* the library's own rows: a song is the same row browsed or searched for */}
                      {libRows.slice(0, libMax).map((s) => (
                        <LibraryRow
                          key={s.id}
                          song={s}
                          queued={queuedCount.get(s.id) ?? 0}
                          draggable={false}
                          act={act}
                          addToQueue={queueSong}
                          onArtist={onArtist}
                        />
                      ))}
                    </ul>
                    {libRows.length > libMax && (
                      <button
                        type="button"
                        className="px-1 py-1 text-sm text-primary underline-offset-2 hover:underline"
                        onClick={() => setLibMax((m) => m + 20)}
                      >
                        {libRows.length - libMax} more
                      </button>
                    )}
                  </div>
                )}
                {text.length >= 2 && (
                  <div>
                    <SectionWord>From the web {shownBusy && <Spinner className="ml-1 inline h-3 w-3" />}</SectionWord>
                    {shownMsg && <p className="px-1 py-2 text-sm text-red-700">{shownMsg}</p>}
                    {shownHits && shownHits.q === text && web.length === 0 && !shownBusy && !shownMsg && (
                      <p className="px-1 py-2 text-sm text-muted-foreground">Nothing new found.</p>
                    )}
                    <ul className={cn("divide-y divide-stone-200/70 transition-opacity", shownBusy && shownHits && "opacity-60")}>
                      {web.map((h) => {
                        // a song the library has is the library's row here too (owner, 28 Nov: parity)
                        const mine = h.have ? byId.get(h.have) : undefined;
                        return mine ? (
                          <LibraryRow
                            key={h.key}
                            song={mine}
                            queued={queuedCount.get(mine.id) ?? 0}
                            draggable={false}
                            act={act}
                            addToQueue={queueSong}
                            onArtist={onArtist}
                          />
                        ) : (
                        <li key={h.key} className="flex min-h-[3.25rem] items-center gap-2 py-1.5 pl-1 pr-1">
                          {/* laid out as RowFace is: the two kinds of row line up */}
                          <Cover src={h.art} />
                          <div className="min-w-0 flex-1">
                            <div className="truncate text-[15px] font-medium sm:text-base">{h.title}</div>
                            <div className="truncate text-[13px] text-muted-foreground sm:text-sm">
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
                        );
                      })}
                    </ul>
                    {shownHits && shownHits.q === text && !shownBusy && web.length > 0 && !webEnd && (
                      <div ref={webFoot} className="flex h-9 items-center px-1">
                        {moreBusy ? (
                          <Spinner className="h-4 w-4 text-muted-foreground" />
                        ) : (
                          <button
                            type="button"
                            className="text-sm text-primary underline-offset-2 hover:underline"
                            onClick={loadMore}
                          >
                            More
                          </button>
                        )}
                      </div>
                    )}
                    {shownHits && shownHits.q === text && !shownBusy && (
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
  );
  // The bar is the page's foot and stays there (owner, 28 Nov: "things cannot jump away from user when they're
  // clicked"): the box you click or tap is the box you type in, where it was. On a phone the keyboard brings the
  // page's foot up with it (useKeyboardFit), as a messaging app's message box.
  // One tray for choosing a song (owner, 28 Nov: "two different patterns -- one when I focus search, the other when I
  // click library"): it rises from the bar over the page's middle, whichever opened it. Nothing typed: the library,
  // to browse; typing: what the library and the web have for it. On a phone it is all of the middle under the song
  // row (one size, whatever is in it) and a drag on its list puts the keyboard away; on a wide screen it stands on
  // the bar in the lists' column. Its handle (a tap or a pull down), the bar's button, Esc or a click beside it
  // closes it.
  const close = () => {
    setOpen(false);
    setQ("");
    inputRef.current?.blur();
  };
  const closeRef = useRef(close);
  closeRef.current = close;
  useEffect(() => {
    if (!open) return;
    const key = (e: KeyboardEvent) => {
      // a dialog or a menu over the tray takes its own Esc
      if (e.key === "Escape" && !document.querySelector("[role=dialog],[role=alertdialog],[role=menu]"))
        closeRef.current();
    };
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  }, [open]);
  const touchY = useRef(0);
  // Pulled down, the tray comes with the finger and, let go far enough or flicked, rolls away (owner, 28 Nov: "if I
  // ... 'pull down' the tray when I'm at the top of a list, it should pull the tray down ... that's just standard
  // ux"): from its handle or heading, or from its list when the list is at its top (anywhere else in the list a pull
  // scrolls the list). Let go short of that, it settles back.
  const sheetRef = useRef<HTMLDivElement | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);
  // It rolls up from behind the bar and rolls back down (owner, 28 Nov: "the 'roll up' animation of the tray ...
  // essential"): its whole height, the old drawer's curve, always (not only where motion isn't reduced). It stays
  // in the page for the way down, showing what it held.
  const present = usePresence(open, TRAY_OUT_MS);
  const sheet = cn(
    "flex flex-col overflow-hidden rounded-t-2xl border border-b-0 border-stone-200 bg-card shadow-[0_-10px_30px_-12px_rgba(0,0,0,0.3)] ease-[cubic-bezier(0.32,0.72,0,1)]",
    open
      ? "pointer-events-auto duration-500 animate-in slide-in-from-bottom"
      : "pointer-events-none duration-300 animate-out slide-out-to-bottom fill-mode-forwards",
  );
  useEffect(() => {
    const sheet = sheetRef.current;
    if (!sheet || !present) return;
    if (open) sheet.style.transform = sheet.style.transition = "";
    let x0 = 0, y0 = 0, t0 = 0, free = false, from: number | null = null, pulled = 0;
    const start = (e: TouchEvent) => {
      const t = e.touches[0], list = listRef.current;
      x0 = t.clientX;
      y0 = t.clientY;
      t0 = e.timeStamp;
      from = null;
      pulled = 0;
      free = !list || !list.contains(e.target as Node) || list.scrollTop <= 0;
    };
    const move = (e: TouchEvent) => {
      const t = e.touches[0], dy = t.clientY - y0;
      if (from == null) {
        if (!free || dy < 8 || Math.abs(t.clientX - x0) > dy) return;   // not at the top, not downwards, or sideways
        from = t.clientY;
        sheet.style.transition = "none";
      }
      pulled = Math.max(0, t.clientY - from);
      sheet.style.transform = `translateY(${pulled}px)`;
      if (e.cancelable) e.preventDefault();   // the tray moves, the list doesn't stretch
    };
    const end = (e: TouchEvent) => {
      if (from == null) return;
      from = null;
      const flick = pulled > 16 && (pulled + 8) / Math.max(1, e.timeStamp - t0) > 0.4;   // px per ms, from the touch
      if (flick || pulled > Math.min(140, sheet.offsetHeight * 0.3)) return closeRef.current();
      sheet.style.transition = "transform 200ms cubic-bezier(0.32,0.72,0,1)";
      sheet.style.transform = "";
    };
    sheet.addEventListener("touchstart", start, { passive: true });
    sheet.addEventListener("touchmove", move, { passive: false });
    sheet.addEventListener("touchend", end);
    sheet.addEventListener("touchcancel", end);
    return () => {
      sheet.removeEventListener("touchstart", start);
      sheet.removeEventListener("touchmove", move);
      sheet.removeEventListener("touchend", end);
      sheet.removeEventListener("touchcancel", end);
    };
  }, [present, open, wide]);
  const holds = hasText ? results : library;
  const held2 = useRef(holds);
  if (open) held2.current = holds;
  const inside = (
    <>
      <button
        type="button"
        aria-label="Close"
        className="flex w-full shrink-0 touch-none justify-center pb-2 pt-2.5"
        onClick={close}
      >
        <span className="h-1.5 w-10 rounded-full bg-stone-300" />
      </button>
      <div
        ref={listRef}
        className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-3 pb-3 sm:px-4"
        onTouchStart={(e) => {
          touchY.current = e.touches[0]?.clientY ?? 0;
        }}
        onTouchMove={(e) => {
          const moved = Math.abs((e.touches[0]?.clientY ?? 0) - touchY.current);
          if (moved > 12 && document.activeElement === inputRef.current) inputRef.current?.blur();
        }}
      >
        {/* a hair taller than the tray, so it always scrolls and a drag never reaches the page behind */}
        <div className="min-h-[calc(100%+1px)]">{open ? holds : held2.current}</div>
      </div>
    </>
  );
  const tray = present && (
    <>
      {/* the page behind, dimmed: a click on it puts the tray away (what was typed stays in the box) */}
      <div
        aria-hidden
        className={cn(
          "absolute inset-0 z-10 bg-black/20",
          open ? "duration-500 animate-in fade-in" : "pointer-events-none duration-300 animate-out fade-out fill-mode-forwards",
        )}
        onPointerDown={() => setOpen(false)}
      />
      {wide ? (
        <div className="pointer-events-none absolute inset-0 z-20" style={{ paddingRight: "var(--sbw, 0px)" }}>
          <div className="mx-auto flex h-full w-full max-w-7xl items-end gap-6 px-6">
            <div className="w-[26rem] shrink-0" />
            <div
              ref={sheetRef}
              className={cn(sheet, "h-[min(100%_-_1rem,46rem)] min-w-0 flex-1")}
              data-testid="karaoke-find-tray"
            >
              {inside}
            </div>
          </div>
        </div>
      ) : (
        <div
          ref={sheetRef}
          className={cn(sheet, "absolute inset-x-0 bottom-0 z-20 mx-auto max-w-3xl")}
          style={{ top: trayTop }}
          data-testid="karaoke-find-tray"
        >
          {inside}
        </div>
      )}
    </>
  );
  return (
    <>
      {tray && trayHost && createPortal(tray, trayHost)}
      <div
        data-testid="karaoke-find"
        // a surface of its own, lifted off the page with room round the box (owner, 28 Nov: "getting slightly
        // visually lost in the page ... allow it to breathe")
        className="relative z-30 shrink-0 border-t border-stone-200 bg-card shadow-[0_-8px_24px_-10px_rgba(0,0,0,0.22)] [touch-action:none]"
        style={{ paddingRight: "var(--sbw, 0px)" }}
      >
        <div
          className="mx-auto flex w-full max-w-3xl gap-6 px-4 pt-3 sm:px-6 lg:max-w-7xl"
          style={{ paddingBottom: FOOT_PAD }}
        >
          <div className="hidden w-[26rem] shrink-0 lg:block" />
          <div className="flex min-w-0 flex-1 items-center gap-2">
            <div className="relative min-w-0 flex-1">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                ref={inputRef}
                value={q}
                onChange={(e) => {
                  setQ(e.target.value);
                  setOpen(true);
                }}
                // a phone's Search key: the keyboard away, the tray the whole height
                onKeyDown={(e) => e.key === "Enter" && !wide && inputRef.current?.blur()}
                onFocus={() => setOpen(true)}
                placeholder="Find a song or paste a link"
                // type="search": Chromium on Android puts its autofill bar (64 px: passwords, cards, addresses)
                // over the keyboard for any plain text box when the user has anything saved, and never reads
                // autocomplete="off" for it; a search field doesn't get it (keyboard_accessory:
                // ManualFillingControllerImpl::ShouldShowAccessoryForLastFocusedFieldType; measured on the owner's
                // phone, 28 Nov: 442 px visible against 378). The keyboard is told nothing else special: a Done
                // key, as the singers' name box. The browser's own clear button is hidden for ours.
                type="search"
                autoComplete="off"
                autoCapitalize="none"
                enterKeyHint="done"
                className={cn(
                  "h-11 rounded-xl pl-9 text-base [&::-webkit-search-cancel-button]:hidden [&::-webkit-search-decoration]:hidden",
                  q || busy ? "pr-10" : "pr-3",
                )}
                aria-label="Find a song"
              />
              <div className="absolute right-2 top-1/2 flex -translate-y-1/2 items-center">
                {busy ? (
                  <Spinner className="mr-1.5 text-muted-foreground" />
                ) : q ? (
                  <button
                    type="button"
                    aria-label="Clear"
                    // pressed before the box loses the focus, so the keyboard stays
                    onPointerDown={(e) => e.preventDefault()}
                    onClick={clear}
                    className="rounded-md p-1.5 text-muted-foreground hover:text-foreground"
                  >
                    <X className="h-4 w-4" />
                  </button>
                ) : null}
              </div>
            </div>
            {/* one slot, one size: the library (the tray, no keyboard), or with the tray open, its close */}
            <Button
              type="button"
              variant="outline"
              className="h-11 w-11 shrink-0 rounded-xl p-0"
              title={open ? "Close" : "Browse the library"}
              aria-label={open ? "Close" : "Browse the library"}
              aria-expanded={open}
              onPointerDown={(e) => e.preventDefault()}
              onClick={() => (open ? close() : setOpen(true))}
            >
              {open ? <ChevronDown className="h-5 w-5" /> : <LibraryIcon className="h-5 w-5" />}
            </Button>
          </div>
        </div>
      </div>
    </>
  );
}

const NO_SONGS: KaraokeSong[] = [];

/** TEMPORARY (28 Nov, to find the band under the search bar on the owner's phone; take it out once found): through
 *  the phones' tunnel, what the browser says its sizes are when the keyboard comes and goes, sent as a path that
 *  room_service's access log records (GET /zz-diag/... 404). Nothing is stored and nothing else reads it. */
function useLayoutProbe() {
  useEffect(() => {
    const vv = window.visualViewport;
    if (!vv || !/\.trycloudflare\.com$/i.test(window.location.hostname)) return;
    let t = 0, n = 0;
    const px = (css: string, pad?: "paddingBottom" | "paddingTop") => {
      const d = document.createElement("div");
      d.style.cssText = `position:fixed;visibility:hidden;pointer-events:none;top:0;left:0;width:0;${css}`;
      document.body.appendChild(d);
      const v = pad ? parseFloat(getComputedStyle(d)[pad]) : d.getBoundingClientRect().height;
      d.remove();
      return Math.round(v);
    };
    const send = (why: string) => {
      const q = (id: string) => document.querySelector<HTMLElement>(`[data-testid="${id}"]`);
      const r = (e: HTMLElement | null) => {
        const b = e?.getBoundingClientRect();
        return b ? `${Math.round(b.top)}to${Math.round(b.bottom)}` : "x";
      };
      const sh = q("karaoke-shell"), bar = q("karaoke-find");
      const a = document.activeElement as HTMLElement | null;
      const vk = (navigator as { virtualKeyboard?: { boundingRect: DOMRect } }).virtualKeyboard;
      const inner = bar?.firstElementChild;
      const parts = [
        `n${++n}`, why, `iw${window.innerWidth}`, `ih${window.innerHeight}`,
        `ch${document.documentElement.clientHeight}`, `vh${Math.round(vv.height)}`, `vo${Math.round(vv.offsetTop)}`,
        `vp${Math.round(vv.pageTop)}`, `zoom${vv.scale.toFixed(2)}`, `scr${window.screen.height}`, `oh${window.outerHeight}`,
        `dvh${px("height:100dvh")}`, `svh${px("height:100svh")}`, `lvh${px("height:100lvh")}`,
        `sab${px("padding-bottom:env(safe-area-inset-bottom,0px)", "paddingBottom")}`,
        `sat${px("padding-top:env(safe-area-inset-top,0px)", "paddingTop")}`,
        `shell${r(sh)}`, `bar${r(bar)}`, `tray${r(q("karaoke-find-tray"))}`,
        `pad${inner ? Math.round(parseFloat(getComputedStyle(inner).paddingBottom)) : "x"}`,
        `kb${sh?.dataset.kb ?? 0}`, `kvh${(sh?.style.getPropertyValue("--kv-h") || "x").replace("px", "")}`,
        `kvt${(sh?.style.getPropertyValue("--kv-top") || "x").replace("px", "")}`, `wy${Math.round(window.scrollY)}`,
        `vk${vk ? Math.round(vk.boundingRect.height) : "x"}`, `ae${a?.tagName ?? "x"}${a?.getAttribute("type") ?? ""}`,
        `dpr${window.devicePixelRatio}`,
      ];
      void fetch(`/rs/zz-diag/${parts.join("_")}`).catch(() => undefined);
    };
    const soon = (why: string) => {
      window.clearTimeout(t);
      t = window.setTimeout(() => send(why), 600);
    };
    const onVv = () => soon("vv"), onIn = () => soon("focus"), onOut = () => soon("blur");
    send("load");
    vv.addEventListener("resize", onVv);
    document.addEventListener("focusin", onIn);
    document.addEventListener("focusout", onOut);
    return () => {
      window.clearTimeout(t);
      vv.removeEventListener("resize", onVv);
      document.removeEventListener("focusin", onIn);
      document.removeEventListener("focusout", onOut);
    };
  }, []);
}
/** The space under the search box. Only a Home Screen app has the page under the phone's home bar, so only there is
 *  room kept for it (and not over the app's tab bar or a keyboard). In a browser's tab the browser keeps its own bars
 *  under the page, and Android's browsers report the system buttons' height (48 px) as "unsafe" all the same, the
 *  keyboard up or not: a band of nothing under the box (owner, 28 Nov: "that ... white space under the search bar"). */
const FOOT_PAD =
  typeof window !== "undefined" &&
  (window.matchMedia("(display-mode: standalone)").matches || (navigator as { standalone?: boolean }).standalone === true)
    ? "var(--kv-foot, max(0.75rem, calc(env(safe-area-inset-bottom, 0px) - var(--app-nav-bottom, 0px))))"
    : "0.75rem";
/** The tray's roll down (ms): it stays in the page this long after it is closed. */
const TRAY_OUT_MS = 300;

/** In the page while ``open`` and for ``ms`` after, for a way out that is seen. */
function usePresence(open: boolean, ms: number): boolean {
  const [here, setHere] = useState(open);
  useEffect(() => {
    if (open) return setHere(true);
    const t = window.setTimeout(() => setHere(false), ms);
    return () => window.clearTimeout(t);
  }, [open, ms]);
  return open || here;
}

/** A wide screen (from 1024 px: the player beside the lists) or a phone's one column. */
function useWide(): boolean {
  const [wide, setWide] = useState(() => window.matchMedia("(min-width: 1024px)").matches);
  useEffect(() => {
    const m = window.matchMedia("(min-width: 1024px)");
    const set = () => setWide(m.matches);
    set();
    m.addEventListener("change", set);
    return () => m.removeEventListener("change", set);
  }, []);
  return wide;
}

/** A phone's keyboard. Some browsers make the page shorter for it; others (an iPhone, Chrome by default) leave the
 *  page its height, cover its foot, and may slide it up. Either way, while one is up (much less is visible than
 *  there was at this width) the frame's foot gives up its home-bar space (--kv-foot: the keyboard is under it now),
 *  and where the browser left the page its height the frame is given the top and height of what is still visible
 *  (--kv-top, --kv-h), so its foot sits on the keyboard and its top stays in view. With no keyboard nothing is set:
 *  the frame is plain CSS. */
function useKeyboardFit(ref: React.RefObject<HTMLElement>) {
  useEffect(() => {
    const vv = window.visualViewport;
    const el = ref.current;
    if (!vv || !el) return;
    let restTop = 0;
    let rest = { w: window.innerWidth, h: vv.height }; // the most that has been visible at this width: no keyboard
    const fit = () => {
      if (window.innerWidth !== rest.w) rest = { w: window.innerWidth, h: vv.height };
      else if (vv.height > rest.h) rest.h = vv.height;
      const up = vv.scale < 1.02 && rest.h - vv.height > 120;
      if (!up) {
        if (!el.dataset.kb) return;
        delete el.dataset.kb;
        for (const k of ["--kv-top", "--kv-h", "--kv-foot"]) el.style.removeProperty(k);
        window.scrollTo(0, 0); // an iPhone can leave the document shifted after its keyboard
        return;
      }
      if (!el.dataset.kb) restTop = el.offsetTop;
      el.dataset.kb = "1";
      el.style.setProperty("--kv-foot", "0.75rem");
      if (window.innerHeight - vv.height > 120) {
        // the page kept its height: the frame takes what is visible
        const top = Math.max(restTop, vv.offsetTop);
        el.style.setProperty("--kv-top", `${top}px`);
        el.style.setProperty("--kv-h", `${vv.offsetTop + vv.height - top}px`);
      } else {
        el.style.removeProperty("--kv-top");
        el.style.removeProperty("--kv-h");
      }
    };
    fit();
    // A keyboard can go without the browser saying so (the app left with it up, the back button: the owner's phone,
    // 28 Nov, left the frame keyboard-high over nothing). So the fit is also checked whenever the focus moves, the
    // page comes back or the window changes, and every so often for as long as a keyboard is thought to be up.
    const again = () => {
      fit();
      window.setTimeout(fit, 350);
    };
    const watch = window.setInterval(() => el.dataset.kb && fit(), 400);
    vv.addEventListener("resize", fit);
    vv.addEventListener("scroll", fit);
    window.addEventListener("resize", fit);
    window.addEventListener("pageshow", again);
    document.addEventListener("visibilitychange", again);
    document.addEventListener("focusin", again);
    document.addEventListener("focusout", again);
    return () => {
      window.clearInterval(watch);
      vv.removeEventListener("resize", fit);
      vv.removeEventListener("scroll", fit);
      window.removeEventListener("resize", fit);
      window.removeEventListener("pageshow", again);
      document.removeEventListener("visibilitychange", again);
      document.removeEventListener("focusin", again);
      document.removeEventListener("focusout", again);
    };
  }, [ref]);
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
  onAdded,
}: {
  name: string;
  songs: KaraokeSong[];
  act: Act;
  onClose: () => void;
  onAdded: () => void;
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
  const add = (h: SearchHit, at: At | null) => {
    const mine = h.have ? byId.get(h.have) : undefined;
    void (at && at !== "now" ? askSingers(h.title) : Promise.resolve([])).then(
      (who) =>
        who &&
        (at && onAdded(), true) &&
        act(
          () =>
            mine && at === "now"
              ? playNow(mine)
              : mine && at
                ? karaoke.queueAdd(mine.id, undefined, at === "next", who)
                : karaoke.request({ ...hitFacts(h), ...(at ? { queue: at, singers: who } : {}) }),
          said(`“${h.title}”`, at, mine?.status !== "ready"),
        ).then(() => setAdded((x) => new Set(x).add(h.key))),
    );
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
        {o?.results.map((h) => {
          // a song the library has is the library's row here too
          const mine = h.have ? byId.get(h.have) : undefined;
          return mine ? (
            <LibraryRow
              key={h.key}
              song={mine}
              queued={0}
              draggable={false}
              act={act}
              addToQueue={(_s, at) => add(h, at)}
            />
          ) : (
          <li key={h.key} className="flex min-h-[3.25rem] min-w-0 items-center gap-2 py-1.5 pl-1 pr-1">
            <Cover src={h.art} />
            <div className="min-w-0 flex-1">
              <div className="truncate text-[15px] font-medium sm:text-base">{h.title}</div>
              <div className="truncate text-[13px] text-muted-foreground sm:text-sm">
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
          );
        })}
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

/** Add to the queue: a "+" wherever a song can be added (owner, 28 Nov: "add to list should be a '+' ... simpler
 *  everywhere"). A row shows it, Play now where the song is ready, and "…" for the rest. */
function AddBtn({ onClick }: { onClick: () => void }) {
  return (
    <Button
      size="icon"
      className="h-9 w-9 shrink-0 rounded-lg"
      onClick={onClick}
      aria-label="Add to the queue"
      title="Add to the queue"
    >
      <Plus className="h-5 w-5" />
    </Button>
  );
}

/** The rest of a library row's actions, behind "…": Play next, the duet switch, the careful lyrics pass, prepare
 *  again, delete. */
function RowMenu({ song, act, onNext }: { song: KaraokeSong; act: Act; onNext?: () => void }) {
  const ready = song.status === "ready";
  const duet = !!song.duet;
  const found = song.duet_found?.found ?? false;
  const r = song.refine?.state;
  const refining = r === "waiting" || r === "working";
  const lyrics = !!song.lyrics && song.lyrics.kind !== "none";
  return (
    <More>
        {onNext && (
          <DropdownMenuItem onSelect={onNext}>
            <ListStart className="mr-2 h-4 w-4" /> Play next
          </DropdownMenuItem>
        )}
        {ready && (
          <DropdownMenuItem
            onSelect={() =>
              act(() => karaoke.setDuet(song.id, !duet === found ? null : !duet), duet ? "Not a duet" : "A duet")
            }
          >
            <Users className="mr-2 h-4 w-4" /> Duet
            {duet && <Check className="ml-auto h-4 w-4 text-primary" />}
          </DropdownMenuItem>
        )}
        {ready && lyrics && (
          <DropdownMenuItem disabled={refining} onSelect={() => refineAsk(song, act)}>
            <WandSparkles className="mr-2 h-4 w-4" /> {refining ? "Timing lyrics…" : "Time lyrics carefully"}
          </DropdownMenuItem>
        )}
        {!ready && song.status !== "failed" && (
          <DropdownMenuItem
            onSelect={() =>
              window.confirm(`Prepare “${song.title}” again from the start?`) &&
              act(() => karaoke.retry(song.id), `Preparing “${song.title}” again`)
            }
          >
            <RotateCcw className="mr-2 h-4 w-4" /> Prepare again
          </DropdownMenuItem>
        )}
        <DropdownMenuSeparator />
        <DropdownMenuItem
          className="text-red-700"
          onSelect={() =>
            window.confirm(`Delete “${song.title}” from the library?`) && act(() => karaoke.remove(song.id))
          }
        >
          <Trash2 className="mr-2 h-4 w-4" /> Delete
        </DropdownMenuItem>
    </More>
  );
}

/** A row's "…": everything but the two or three things a row shows. */
function More({ children, testid }: { children: ReactNode; testid?: string }) {
  const [open, setOpen] = useState(false);
  const finger = useRef(false);
  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="h-10 w-9 shrink-0 rounded-lg"
          aria-label="More"
          data-testid={testid}
          // A finger opens it with a tap, not with the touch a scroll starts on (owner, 28 Nov: "... triggers
          // instantly when I'm trying to scroll"): the menu's own open-on-press is kept for the mouse only.
          onPointerDown={(e) => {
            finger.current = e.pointerType !== "mouse";
            if (finger.current) e.preventDefault();
          }}
          onClick={() => finger.current && setOpen(true)}
        >
          <MoreHorizontal className="h-4 w-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">{children}</DropdownMenuContent>
    </DropdownMenu>
  );
}

/** Play it now (a library song: a new queue entry straight after the one that is on, and on it goes). */
function PlayBtn({ onClick }: { onClick: () => void }) {
  return (
    <IconBtn label="Play this now" onClick={onClick}>
      <Play className="h-4 w-4" />
    </IconBtn>
  );
}

/** A song not in the library yet has the same three as one that is (owner, 28 Nov: "the same ... options in all
 *  cases"): "+" (to the end of the queue), Play now (it is prepared, then on it goes), and behind "…" Play next and
 *  Library only (get it ready for later, not queued). */
function AtButtons({ onAt, onLibrary }: { onAt: (at: At) => void; onLibrary?: () => void }) {
  return (
    <div className="flex shrink-0 items-center">
      <AddBtn onClick={() => onAt("end")} />
      <PlayBtn onClick={() => onAt("now")} />
      <More>
        <DropdownMenuItem onSelect={() => onAt("next")}>
          <ListStart className="mr-2 h-4 w-4" /> Play next
        </DropdownMenuItem>
        {onLibrary && (
          <DropdownMenuItem onSelect={onLibrary}>
            <LibraryIcon className="mr-2 h-4 w-4" /> Library only
          </DropdownMenuItem>
        )}
      </More>
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
  // Where the sound can go: the app's sound outputs (docs/SOUND.md), each with its delay; the ones karaoke can't play
  // to are greyed. Fetched again after a calibration or a trim.
  const [outputs, setOutputs] = useState<SoundOutput[] | null>(null);
  const loadOutputs = useCallback(() => {
    karaoke.outputs().then(
      (o) => setOutputs(o.outputs),
      () => undefined,
    );
  }, []);
  useEffect(loadOutputs, [loadOutputs]);
  // This device (owner, 27 Nov: "'this device' is always a meaningful notion in any device that can host a browser"):
  // always offered, after the screens; this browser then plays the sound itself (KaraokeHere, the screens' own player).
  const mine = myOutput();
  const choices = useMemo(() => {
    const list = (outputs ?? []).filter((o) => o.kind !== "device" || o.id === mine);
    const here: SoundOutput = {
      ...(list.find((o) => o.id === mine) ?? {
        id: mine, kind: "device", room: null, calibrate: "device", available: true,
      }),
      label: "This device", kind_label: "This device", hint: "This browser plays the sound (the lyrics stay on the screen)",
    } as SoundOutput;
    const rest = list.filter((o) => o.id !== mine);
    const screensEnd = rest.filter((o) => o.kind === "screen").length;
    return [...rest.slice(0, screensEnd), here, ...rest.slice(screensEnd)];
  }, [outputs, mine]);
  const playsHere = st.settings.output === mine;
  useEffect(() => setKaraokeHere(playsHere), [playsHere]);   // moved elsewhere (here or by another page): follow it
  const target = karaokeScreen(dep, st.settings.screen);
  const current = choices.find((o) => o.id === st.settings.output) ?? null;
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
            <SoundOutputSelect
              value={st.settings.output}
              outputs={outputs ? choices : []}
              ariaLabel="Karaoke sound"
              onChange={(id) => {
                setKaraokeHere(id === mine);   // at once, in the click: the browser lets a page sound after one
                act(() => karaoke.settings({ output: id }));
              }}
            />
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
          {/* The sound's delay: the output's own (Settings › Sound & timing shows them all), measured with the room
              mic; the lyrics wait this long. */}
          {current && current.kind !== "none" && (
            <div className="space-y-1">
              <span>
                Speaker delay <span className="text-muted-foreground">(the lyrics wait this long)</span>
              </span>
              <OutputTiming
                output={current}
                compact
                onChanged={loadOutputs}
                confirmText="Calibrate the speaker delay? The song pauses, and the speaker plays ten chirps (about 12 s). Keep the room quiet."
              />
            </div>
          )}
          {current && current.kind !== "none" && st.settings.eq != null && (
            <div className="space-y-1">
              <span>EQ</span>
              <OutputEq output={current.id} />
            </div>
          )}
          {st.sonos_error && (st.settings.output ?? "").startsWith("sonos:") && (
            <p className="text-sm text-red-700">Sonos: {st.sonos_error}</p>
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
          <LoopbackToggle />
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

// ---------------------------------------------------------------- the careful pass over a song's lyrics

/** What the careful pass is doing, in a song's line (RowFace). */
const REFINE_WORDS: Record<NonNullable<KaraokeSong["refine"]>["state"], string> = {
  waiting: " · lyrics: waiting for the careful pass",
  working: " · lyrics: careful pass running…",
  done: " · lyrics timed carefully",
  no_better: "",
  failed: "",
  undone: "",
  cancelled: "",
};

const refineAsk = (song: KaraokeSong, act: Act) =>
  act(() => karaoke.refine(song.id), `“${song.title}”: the lyrics will be timed carefully (a few minutes)`);

/** Lyrics off (owner, 27 Nov: "a way to refer a song for 2ndary processing -- e.g. more carefully time lyrics"): the
 *  careful pass in the background (every voice taken out, heard, the whole lyrics timed at once); the song plays on
 *  meanwhile and the new timing comes in by itself, even mid-song. */
/** The playing song's line under its title: refer it, how it is going, Undo. */
function RefineLine({ song, act }: { song: KaraokeSong; act: Act }) {
  if (!song.lyrics || song.lyrics.kind === "none") return null;
  const r = song.refine;
  const link = "underline-offset-2 hover:text-foreground hover:underline";
  return (
    <div className="mt-1 flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
      {r?.state === "waiting" ? (
        <>
          <span>Lyrics: waiting for the careful pass (after any song being prepared)</span>
          <button type="button" className={link} onClick={() => act(() => karaoke.refine(song.id, { cancel: true }))}>
            Cancel
          </button>
        </>
      ) : r?.state === "working" ? (
        <span className="flex items-center gap-1">
          <WandSparkles className="h-3.5 w-3.5 animate-pulse text-primary" />
          Timing the lyrics carefully… a few minutes; they change over by themselves
        </span>
      ) : (
        <>
          {r?.state === "done" && <span>Lyrics timed carefully.</span>}
          {(r?.state === "no_better" || r?.state === "failed") && <span className="text-red-700">{r.error}</span>}
          <button type="button" className={cn(link, "flex items-center gap-1")} onClick={() => refineAsk(song, act)}>
            <WandSparkles className="h-3.5 w-3.5" />
            {r?.state === "done" ? "Again" : "Lyrics off? Time them carefully"}
          </button>
          {r?.state === "done" && (
            <button
              type="button"
              className={link}
              onClick={() => act(() => karaoke.refine(song.id, { undo: true }), "The first lyrics timing is back")}
            >
              Undo
            </button>
          )}
        </>
      )}
    </div>
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
