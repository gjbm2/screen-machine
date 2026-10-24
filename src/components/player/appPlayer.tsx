// The app's one music player poll (GET /show/api/v1/player through room_service's /show pass-through), shared by the
// docked player and the pages that show the player too (the Room page's mini player, /music, the pianola's play bar),
// so a page and the dock never poll twice or disagree. usePlayer's pace: 1 s while it prepares or plays, 5 s otherwise,
// only while the page is visible. It polls only on the pages that get the dock (DOCK_PATHS); never on the TVs'
// /display pages or the other tools. The address is the Room page's (resolveRoomUrl: ?room=, the remembered one,
// same-origin /rs over HTTPS, else this host's room_service), read again as the route changes.

import { createContext, useContext, useMemo, type ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { MusicApi } from "@/pages/Music/api";
import { usePlayer, type PlayerHook } from "@/pages/Music/usePlayer";
import { resolveRoomUrl } from "@/pages/Room/api";

/** The pages a person uses that get the docked player: /, /room, /music, /lights, /scheduler, /settings. */
export const DOCK_PATHS = /^\/(room|music|lights|scheduler|settings)?\/?$/;

interface AppPlayerCtx {
  baseUrl: string;
  /** Polling: this page gets the dock. */
  enabled: boolean;
  player: PlayerHook;
}

const Ctx = createContext<AppPlayerCtx | null>(null);

export function AppPlayerProvider({ children }: { children: ReactNode }) {
  const { pathname, search } = useLocation();
  const enabled = DOCK_PATHS.test(pathname);
  // eslint-disable-next-line react-hooks/exhaustive-deps -- read again as the route changes (the Room page may move it)
  const baseUrl = useMemo(() => resolveRoomUrl(window.location.search), [pathname, search]);
  const api = useMemo(() => new MusicApi(baseUrl), [baseUrl]);
  const player = usePlayer(api, enabled);
  const value = useMemo(() => ({ baseUrl, enabled, player }), [baseUrl, enabled, player]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAppPlayerCtx(): AppPlayerCtx | null {
  return useContext(Ctx);
}

/** The app's player for this address, when it's polling; null: keep your own. */
export function useAppPlayer(baseUrl: string): PlayerHook | null {
  const ctx = useContext(Ctx);
  return ctx && ctx.enabled && ctx.baseUrl === baseUrl ? ctx.player : null;
}
