// The Room page's sections, one per room in rooms.json (its screens, lights and effects), then "Other"; the Lights page
// (/lights) uses the same rooms for its controls.

import { roomOfLight } from "../Lights/lib.ts";
import { rigLightIds } from "./lightStates.ts";
import type { RoomStatus, SiteEffect } from "./types";

/** One section of the page: a room (its screens, then its lights and effects), or "Other". */
export interface Section {
  key: string;
  title: string;
  /** Its screens that room_service runs (status.screens: the ones that can go live). */
  screens: string[];
  /** Its art-only screens (no live block, so not in status.screens; from GET /api/deployment): art cards only. */
  artScreens: string[];
  /** The section's lights: Hue lamps by fixture id, DMX lights as "light.<id>" (each in one room only, by the shared
   *  rule in Lights/lib.ts roomOfLight), or null for none. */
  lights: string[] | null;
  /** Device cards (rooms.json "effects"), e.g. the glitterball. */
  effects: SiteEffect[];
}

/** An art-only screen a room lists (GET /api/deployment `screens` with art_only). */
export interface ArtOnlyScreen {
  id: string;
  room?: string | null;
}

const isDmxEntry = (id: string) => id === "light" || id.startsWith("light.");

/**
 * The page's sections from status.rooms, in file order, then "Other" for screens and lights in no room. A section
 * with nothing to show is left out. `lampIds` are the Hue lamps the room has (null while still loading). The DMX
 * lights are room_service's light subsystem's members, each given to one room by roomOfLight (contract C1): a light no
 * room claims is the only room's on a site of one room, else Other's. `artOnly`: the deployment's art-only screens;
 * only those a room lists show (in that room), never in Other. Without rooms (an older room_service) there are none:
 * the page shows the flat list instead.
 */
export function buildSections(status: RoomStatus | null, lampIds: string[] | null, artOnly: ArtOnlyScreen[] = []): Section[] {
  const screens = status?.screens ?? {};
  const rooms = status?.rooms ?? [];
  if (!rooms.length) return [];
  const rig = rigLightIds(status);
  const placed = new Set<string>();
  const placedLamps = new Set<string>();
  const out: Section[] = rooms.map((r) => {
    const ids = (r.screens ?? []).filter((id) => id in screens && !placed.has(id));
    ids.forEach((id) => placed.add(id));
    const art = artOnly.filter((a) => a.room === r.id && !(a.id in screens) && !placed.has(a.id)).map((a) => a.id);
    art.forEach((id) => placed.add(id));
    const listed = r.lights ?? [];
    const hue = listed.filter((id) => !isDmxEntry(id) && !placedLamps.has(id));
    hue.forEach((id) => placedLamps.add(id));
    // the room's DMX lights by id; while room_service says nothing about its lights, what rooms.json says
    const dmx = rig ? rig.filter((id) => roomOfLight(rooms, id, true)?.id === r.id).map((id) => `light.${id}`) : listed.filter(isDmxEntry);
    const lights = [...hue, ...dmx];
    return {
      key: r.id,
      title: r.name || r.id,
      screens: ids,
      artScreens: art,
      lights: lights.length ? lights : null,
      effects: (r.effects ?? []).filter((e) => !!status?.subsystems?.[e.power.split(".")[0]]),
    };
  });
  const otherScreens = Object.keys(screens).filter((id) => !placed.has(id));
  const otherLamps = [
    ...(lampIds ?? []).filter((id) => !placedLamps.has(id)),
    ...(rig ?? []).filter((id) => !roomOfLight(rooms, id, true)).map((id) => `light.${id}`),
  ];
  out.push({ key: "_other", title: "Other", screens: otherScreens, artScreens: [], lights: otherLamps.length ? otherLamps : null, effects: [] });
  // a DMX entry counts as there (LightsSection draws nothing when none of its lights exist); a Hue lamp once known
  const hasLamps = (ids: string[] | null) => !!ids && ids.some((id) => isDmxEntry(id) || lampIds === null || lampIds.includes(id));
  return out.filter((s) => s.screens.length > 0 || s.artScreens.length > 0 || hasLamps(s.lights) || s.effects.length > 0);
}

/** Whether a screen may go live (its live block is enabled): a screen whose live is switched off isn't counted as
 *  one that "joins in when it goes live". Absent policy counts as allowed. */
export function liveAllowed(sc: { policy?: { live_enabled?: boolean }; live_enabled?: boolean } | null | undefined): boolean {
  return (sc?.policy?.live_enabled ?? sc?.live_enabled) !== false;
}

/** Whether the site has Sonos, for words that name it: off when the deployment's `sonos` capability is off, or when
 *  room_service's sonos subsystem has no members (it always registers one, empty without sonos.json); while the room's
 *  status isn't known yet (no subsystems), assumed there, so the words don't flicker. */
export function siteHasSonos(
  subsystems: Record<string, { members?: Record<string, unknown> } | undefined> | null | undefined,
  capability?: boolean,
): boolean {
  if (capability === false) return false;
  if (!subsystems) return true;
  return Object.keys(subsystems.sonos?.members ?? {}).length > 0;
}
