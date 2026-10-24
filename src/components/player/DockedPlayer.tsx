// The docked player (owner, 24 Oct: "when music is playing through the app, that apparatus should anchor bottom
// throughout the app, probably"): the mini player (Room/NowPlayingRow.tsx's MiniPlayer: title, composer, the shared
// Transport, Score, Play on, Playlist, Open Music) fixed at the bottom of the screen on every page a person uses
// (appPlayer.tsx's DOCK_PATHS: /, /room, /music, /lights, /scheduler, /settings):
//   - on the Room page, always (idle: "Nothing playing", what else is on, Play when there's a queue, Playlist);
//   - elsewhere, while the music player plays, prepares or is paused.
// Not shown on a score (/music?score=: the score has its own play bar), or while the live music screen is open (it
// has its own too). When the sound plays on this device too, the dock carries this device's line and Stop sound here,
// and ListenHere leaves its own bar off (dockState.ts): one bar, never two.
// Its height is --dock-h on <html>, so the page gets the room back under it (index.css: body's padding-bottom) and
// anything else fixed to the foot can sit above it. Mounted once, in App.tsx.

import { useEffect, useLayoutEffect, useRef } from "react";
import { useLocation } from "react-router-dom";
import { cn } from "@/lib/utils";
import { SCORE_PARAM } from "@/pages/Music/bits";
import { useListenHere } from "@/pages/Room/listenStream";
import { MiniPlayer } from "@/pages/Room/NowPlayingRow";
import { RoomThemeStyle, THEME_CLASS } from "@/pages/Room/theme";
import { useAppPlayerCtx } from "./appPlayer";
import { setDocked } from "./dockState";

const SHOWN = ["preparing", "playing", "paused"];

export function DockedPlayer() {
  const ctx = useAppPlayerCtx();
  const { pathname, search } = useLocation();
  const here = useListenHere();
  const v = ctx?.player.view ?? null;
  const onMusic = pathname.startsWith("/music");
  const onRoom = pathname.startsWith("/room");
  const onScore = onMusic && new URLSearchParams(search).has(SCORE_PARAM);
  const playing = ctx?.player.link === "ok" && !!v?.current && SHOWN.includes(v.state);
  const show = !!ctx?.enabled && !onScore && !here.viewOpen && (onRoom || playing);
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

  if (!show || !ctx) return null;
  return (
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
        <MiniPlayer player={ctx.player} baseUrl={ctx.baseUrl} docked />
      </div>
    </div>
  );
}
