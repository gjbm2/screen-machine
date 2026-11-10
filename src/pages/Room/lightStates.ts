// The room's lights in words, from what the page already reads (pure, so plain node can test it: lightStates.test.ts):
// the Hue lamps from room_service's fixtures view, the DMX (rig) lights from its `light` subsystem in the room's status.

import type { FixturesView, RoomStatus } from "./types";

type LightSubsystem = { believed?: unknown; members?: Record<string, { believed?: unknown }> };

function lightSubsystem(status: RoomStatus | null | undefined): LightSubsystem | null {
  const s = (status?.subsystems as Record<string, LightSubsystem> | undefined)?.light;
  return s && typeof s === "object" ? s : null;
}

/** The DMX lights' believed values by light id ("dmx" | "off" | {hex, bri}). From the members (status.subsystems.light
 *  .members), else the believed map; room_service gives a subsystem of ONE member its value bare, not as a map. */
function believedById(status: RoomStatus | null | undefined): Record<string, unknown> {
  const s = lightSubsystem(status);
  if (!s) return {};
  if (s.members && typeof s.members === "object") {
    return Object.fromEntries(Object.entries(s.members).map(([id, m]) => [id, m?.believed]));
  }
  const b = s.believed;
  return b && typeof b === "object" ? (b as Record<string, unknown>) : {};
}

/** The rig's DMX lights by id (room_service's light subsystem), or null when the room's status has no light
 *  subsystem at all (DMX off, or an older room_service): then nobody can say which lights there are. */
export function rigLightIds(status: RoomStatus | null | undefined): string[] | null {
  const s = lightSubsystem(status);
  if (!s) return null;
  if (s.members && typeof s.members === "object") return Object.keys(s.members);
  return s.believed && typeof s.believed === "object" ? Object.keys(s.believed as object) : [];
}

/** The DMX lights' states, as the summaries count them ("follow" | "on" | "off"), optionally only some lights. */
export function dmxStates(status: RoomStatus | null | undefined, only?: (id: string) => boolean): string[] {
  return Object.entries(believedById(status)).flatMap(([id, v]) => {
    if (only && !only(id)) return [];
    if (v === "dmx" || v === "show") return ["follow"];
    if (v === "off") return ["off"];
    if (v && typeof v === "object") return ["on"];
    return [];
  });
}

/** Whether any lamp or DMX light is set to follow the music (following, or waiting for it): the look shows on it. */
export function lightsFollow(view: FixturesView | null, status: RoomStatus | null): boolean {
  const hue = (view?.enabled ? view.fixtures : []).some((f) => f.kind === "hue" && f.control === "dmx");
  return hue || dmxStates(status).includes("follow");
}

/** The lights in a word for the page's top line: following the music, on, or off; null when the room has no lamps and
 *  no DMX lights. Hue lamps and DMX lights both count. */
export function lampsWord(view: FixturesView | null, status: RoomStatus | null): string | null {
  const lamps = view?.enabled ? view.fixtures.filter((f) => f.kind === "hue") : [];
  const dmx = dmxStates(status);
  if (!lamps.length && !dmx.length) return null;
  if (lightsFollow(view, status)) return "lights follow the music";
  return lamps.some((f) => f.status === "house" || f.light?.on) || dmx.includes("on") ? "lights on" : "lights off";
}
