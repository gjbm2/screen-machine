// The Room page's sections, one per room in rooms.json (its screens, lights and effects), then "Other"; the Lights page
// (/lights) uses the same rooms for its controls.

import type { RoomStatus, SiteEffect } from "./types";

/** One section of the page: a room (its screens, then its lights and effects), or "Other". */
export interface Section {
  key: string;
  title: string;
  screens: string[];
  /** The section's lamp ids, or null for none. */
  lights: string[] | null;
  /** Device cards (rooms.json "effects"), e.g. the glitterball. */
  effects: SiteEffect[];
}

/**
 * The page's sections from status.rooms, in file order, then "Other" for screens and lamps in no room. A section
 * with nothing to show is left out. `lampIds` are the lamps the room has (null while still loading). Without
 * rooms (an older room_service) there are none: the page shows the flat list instead.
 */
export function buildSections(status: RoomStatus | null, lampIds: string[] | null): Section[] {
  const screens = status?.screens ?? {};
  const rooms = status?.rooms ?? [];
  if (!rooms.length) return [];
  const placed = new Set<string>();
  const placedLamps = new Set<string>();
  const out: Section[] = rooms.map((r) => {
    const ids = (r.screens ?? []).filter((id) => id in screens && !placed.has(id));
    ids.forEach((id) => placed.add(id));
    (r.lights ?? []).forEach((id) => placedLamps.add(id));
    return {
      key: r.id,
      title: r.name || r.id,
      screens: ids,
      lights: r.lights?.length ? r.lights : null,
      effects: (r.effects ?? []).filter((e) => !!status?.subsystems?.[e.power.split(".")[0]]),
    };
  });
  const otherScreens = Object.keys(screens).filter((id) => !placed.has(id));
  const otherLamps = (lampIds ?? []).filter((id) => !placedLamps.has(id));
  out.push({ key: "_other", title: "Other", screens: otherScreens, lights: otherLamps.length ? otherLamps : null, effects: [] });
  const hasLamps = (ids: string[] | null) => !!ids && (lampIds === null || ids.some((id) => lampIds.includes(id)));
  return out.filter((s) => s.screens.length > 0 || hasLamps(s.lights) || s.effects.length > 0);
}
