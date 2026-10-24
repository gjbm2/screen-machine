// The room at a glance, for every page's header (PageHeader.tsx): is a live mode running, and which. Read from the
// shared room link (roomLink.ts: one probe and one events socket for the page, started by any user), so it costs no
// polling. The modes' own labels ("Party", "Piano") are read once from room_service and again when modes.json changes.

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
    if (live && current && room.canControl) loadModes(rev);
    return () => {
      subs.delete(fn);
    };
  }, [live, current, rev, room.canControl]);
  return {
    onLan: room.onLan,
    liveMode: live ? (current ? modeLabel(current, modes) : "Live") : null,
  };
}
