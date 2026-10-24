// The docked player (owner, 24 Oct: "when music is playing through the app, that apparatus should anchor bottom
// throughout the app, probably"): the mini player (Room/NowPlayingRow.tsx's MiniPlayer: title, composer, the shared
// Transport, Score, Play on, Playlist, Open Music) fixed at the bottom of the screen on every page a person uses
// (appPlayer.tsx's DOCK_PATHS: /, /room, /music, /lights, /scheduler, /settings):
//   - on the Room page, always (idle: "Nothing playing", what else is on, Play when there's a queue, Playlist);
//   - elsewhere, while the music player plays, prepares or is paused, or while the piano records.
// While the piano records (whoever started it: Room/PianoRecorder.tsx's useRecorder, one poll for the app), the dock
// starts with "Recording · 12 notes  ● 1:23 Stop"; Stop opens the new take (Play it, Score, Keep, Discard) in a sheet.
// Not shown on a score (/music?score=: the score has its own play bar), or while the live music screen is open (it
// has its own too). When the sound plays on this device too, the dock carries this device's line and Stop sound here,
// and ListenHere leaves its own bar off (dockState.ts): one bar, never two.
// Its height is --dock-h on <html>, so the page gets the room back under it (index.css: body's padding-bottom) and
// anything else fixed to the foot can sit above it. Mounted once, in App.tsx.

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { cn } from "@/lib/utils";
import { SCORE_PARAM } from "@/pages/Music/bits";
import { useListenHere } from "@/pages/Room/listenStream";
import { MiniPlayer } from "@/pages/Room/NowPlayingRow";
import { ActionSheet } from "@/pages/Room/overlays";
import { NewTakePanel, RecordPill, useRecorder } from "@/pages/Room/PianoRecorder";
import { clock } from "@/pages/Room/RecordButton";
import { RoomThemeStyle, THEME_CLASS } from "@/pages/Room/theme";
import { useAppPlayerCtx } from "./appPlayer";
import { setDocked } from "./dockState";

const SHOWN = ["preparing", "playing", "paused"];

/** Tells the dock whether the piano records (mounted only on the dock's pages, so the TVs never poll). */
function RecordingWatch({ baseUrl, onChange }: { baseUrl: string; onChange: (recording: boolean) => void }) {
  const { recording } = useRecorder(baseUrl);
  useEffect(() => onChange(recording), [recording, onChange]);
  return null;
}

/** The dock's first line while the piano records: "Recording · 12 notes", then the red "● 1:23 Stop" pill. */
function RecordingRow({ baseUrl, onStopped }: { baseUrl: string; onStopped: () => void }) {
  const r = useRecorder(baseUrl);
  if (!r.recording) return null;
  return (
    <div className="flex items-center gap-2 border-b border-stone-200/80 pb-2" data-testid="dock-recording">
      <span aria-hidden className="h-2.5 w-2.5 shrink-0 rounded-full bg-rose-600 motion-safe:animate-pulse" />
      <p className="min-w-0 flex-1 truncate text-base font-semibold text-rose-900">
        Recording
        <span className="font-normal text-muted-foreground"> · {r.notes} note{r.notes === 1 ? "" : "s"}</span>
      </p>
      <RecordPill baseUrl={baseUrl} idle={false} sheet={false} onStopped={(t) => t && onStopped()} />
    </div>
  );
}

/** The take just stopped from the dock, in a sheet: Play it, Score, Keep, Discard. */
function NewTakeSheet({ baseUrl, open, onClose }: { baseUrl: string; open: boolean; onClose: () => void }) {
  const r = useRecorder(baseUrl);
  const shown = open && !!r.fresh;
  return (
    <>
      {shown && <RoomThemeStyle />}
      <ActionSheet
        open={shown}
        onOpenChange={(o) => !o && onClose()}
        title={r.fresh ? `New recording · ${clock(r.fresh.duration_s)}` : "New recording"}
        testId="dock-take-sheet"
      >
        <NewTakePanel baseUrl={baseUrl} heading={false} onDone={onClose} className="border-0 bg-transparent p-0 pb-1" />
      </ActionSheet>
    </>
  );
}

export function DockedPlayer() {
  const ctx = useAppPlayerCtx();
  const { pathname, search } = useLocation();
  const here = useListenHere();
  const v = ctx?.player.view ?? null;
  const onMusic = pathname.startsWith("/music");
  const onScore = onMusic && new URLSearchParams(search).has(SCORE_PARAM);
  const playing = ctx?.player.link === "ok" && !!v?.current && SHOWN.includes(v.state);
  const [recording, setRecording] = useState(false);
  const [take, setTake] = useState(false);
  // docked only while something plays or records (owner, 24 Oct: "when music is playing through the app, that apparatus
  // should anchor bottom"); idle, the Room page shows its own row at the end instead of a bar over the page
  const show = !!ctx?.enabled && !onScore && !here.viewOpen && (playing || recording);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setDocked(show);
    return () => setDocked(false);
  }, [show]);

  // its height, for the page's padding and anything else at the foot
  useLayoutEffect(() => {
    const root = document.documentElement;
    const el = ref.current;
    if (!show || !el) {
      root.style.removeProperty("--dock-h");
      return;
    }
    const set = () => root.style.setProperty("--dock-h", `${Math.ceil(el.getBoundingClientRect().height)}px`);
    set();
    const ro = new ResizeObserver(set);
    ro.observe(el);
    return () => {
      ro.disconnect();
      root.style.removeProperty("--dock-h");
    };
  }, [show]);

  if (!ctx?.enabled) return null;
  return (
    <>
      <RecordingWatch baseUrl={ctx.baseUrl} onChange={setRecording} />
      <NewTakeSheet baseUrl={ctx.baseUrl} open={take} onClose={() => setTake(false)} />
      {show && (
        <div
          ref={ref}
          className={cn(
            THEME_CLASS,
            "pointer-events-none fixed inset-x-0 bottom-0 z-40 px-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] pt-2 text-base antialiased",
          )}
          data-testid="docked-player"
        >
          <RoomThemeStyle />
          <div className="pointer-events-auto mx-auto w-full max-w-3xl">
            <MiniPlayer
              player={ctx.player}
              baseUrl={ctx.baseUrl}
              docked
              top={recording ? <RecordingRow baseUrl={ctx.baseUrl} onStopped={() => setTake(true)} /> : null}
            />
          </div>
        </div>
      )}
    </>
  );
}
