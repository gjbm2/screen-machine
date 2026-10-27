// The room at a glance, for every page's header (PageHeader.tsx, AppNav's mode menu, the app menu): the mode it is in
// (and going to), and whether a screen is live. Read from the shared room link (roomLink.ts: one probe and one events
// socket for the page, started by any user), so it costs no polling. The modes themselves (labels, icons) are read once
// from room_service and again when modes.json changes.

import { useEffect, useState } from "react";
import { liveStateOf, roomApi, useRoomLink } from "@/components/room/roomLink";
import type { ModeDef } from "@/pages/Room/types";
import { modeLabel } from "@/pages/Room/words";

let modes: Record<string, ModeDef> | null = null;
let modesRev: string | null = null;
let loading: Promise<void> | null = null;
const subs = new Set<() => void>();

function loadModes(rev: string) {
  if (loading || (modes && modesRev === rev)) return;
  loading = roomApi()
    .modes()
    .then((m) => {
      const v = m?.modes;
      if (v && typeof v === "object") {
        modes = v;
        modesRev = rev;
      }
    })
    .catch(() => {
      /* the mode's plain name will do */
    })
    .finally(() => {
      loading = null;
      subs.forEach((f) => f());
    });
}

export interface RoomGlance {
  /** The room answers this page (so Room, Music and Lights links work from here). */
  onLan: boolean | null;
  /** A screen is live (or going live): the running mode's label, e.g. "Party". Null when every screen shows art. */
  liveMode: string | null;
  /** The mode the room is in, and the one it's switching to (null when not switching). Null without the room. */
  current: string | null;
  target: string | null;
  /** The modes, in the file's order (null until read). */
  modes: Record<string, ModeDef> | null;
  /** This page may switch the room. */
  canControl: boolean;
}

export function useRoomGlance(): RoomGlance {
  const room = useRoomLink();
  const [, bump] = useState(0);
  const status = room.status;
  const current = status?.mode?.current ?? null;
  const live = Object.values(status?.screens ?? {}).some((s) => {
    const ls = liveStateOf(s);
    return ls === "live" || ls === "entering";
  });
  const rev = status?.config?.revision ?? "";
  useEffect(() => {
    const fn = () => bump((n) => n + 1);
    subs.add(fn);
    if (current && room.canControl) loadModes(rev);
    return () => {
      subs.delete(fn);
    };
  }, [current, rev, room.canControl]);
  return {
    onLan: room.onLan,
    liveMode: live ? (current ? modeLabel(current, modes) : "Live") : null,
    current,
    target: status?.mode?.target && status.mode.target !== current ? status.mode.target : null,
    modes,
    canControl: room.canControl,
  };
}
