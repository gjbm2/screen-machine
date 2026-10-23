// What every part of the Music page shares: the client, the player, the saved playlists, a short message at the
// bottom of the screen ("Added to Evening jazz"), pins changed on this page (so every list agrees at once), and a
// counter that tells the lists to look again after an upload, an import, a rename or a delete.

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { musicWords, type MusicApi, type PlaylistSummary } from "./api";
import type { PlayerHook } from "./usePlayer";

export type MusicTab = "browse" | "playlists" | "mine" | "web";

export interface PlaylistsHook {
  list: PlaylistSummary[] | null;
  error: string | null;
  reload: () => Promise<void>;
}

export interface MusicCtx {
  api: MusicApi;
  player: PlayerHook;
  playlists: PlaylistsHook;
  flash: (words: string) => void;
  /** A piece's pin, as changed on this page (else the list's own). */
  pinnedOf: (id: string, fallback: boolean) => boolean;
  setPinned: (id: string, on: boolean) => void;
  /** Bumped when the library changes here (upload, import, rename, delete): lists of it look again. */
  libraryRev: number;
  bumpLibrary: () => void;
  setTab: (t: MusicTab) => void;
  /** Open a saved playlist on the Playlists tab. */
  openPlaylist: (id: string) => void;
}

const Ctx = createContext<MusicCtx | null>(null);
export const MusicProvider = Ctx.Provider;

/** The Music page's shared parts, or null outside it (a component that also lives elsewhere: PlayBar). */
export function useMusicMaybe(): MusicCtx | null {
  return useContext(Ctx);
}

export function useMusic(): MusicCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("useMusic outside the Music page");
  return c;
}

/** The saved playlists (GET /show/api/v1/playlists), loaded once and again on reload(). */
export function usePlaylists(api: MusicApi, enabled = true): PlaylistsHook {
  const [list, setList] = useState<PlaylistSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const seq = useRef(0);
  const reload = useCallback(async () => {
    const n = ++seq.current;
    try {
      const r = await api.playlists();
      if (n !== seq.current) return;
      setList(Array.isArray(r?.playlists) ? r.playlists : []);
      setError(null);
    } catch (e) {
      if (n !== seq.current) return;
      setError(musicWords(e));
    }
  }, [api]);
  useEffect(() => {
    if (enabled) void reload();
  }, [enabled, reload]);
  return { list, error, reload };
}

/** A short message at the bottom of the screen for a few seconds. */
export function useFlash(ms = 3500): [string | null, (words: string) => void] {
  const [msg, setMsg] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout>>();
  const flash = useCallback(
    (words: string) => {
      setMsg(words);
      clearTimeout(timer.current);
      timer.current = setTimeout(() => setMsg(null), ms);
    },
    [ms],
  );
  useEffect(() => () => clearTimeout(timer.current), []);
  return [msg, flash];
}
