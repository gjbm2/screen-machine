// The top of the Room page's Lights fold (ia, 24 Oct): the two switches that change every lamp at once, House lights
// and Lights low (show_service's actions, through room_service's /show pass-through; they used to sit in Look, where
// someone dimming the lamps couldn't find them). Each appears once, here, next to the lamps they change: side by side
// in one row (owner, 25 Oct: "vertical space is at a HIGH PREMIUM"), what each does in its tooltip.
//
// lightsWords(): the fold's closed summary from the lights' real states (owner, 24 Oct: it said "4 lamps follow the
// music" while all four were off): the Hue lamps from room_service's fixtures view (status per lamp), the DMX lights
// from the light subsystem's believed values in the room's status. "4 lamps off", "2 lamps follow the music · 2 on";
// "lights" when the kinds are mixed. No light is named here.

import { useEffect, useState } from "react";
import { Switch } from "@/components/ui/switch";
import type { RoomApi } from "./api";
import { Spinner } from "./bits";
import { NoteLine, useSend, useShowState, type ShowFeed } from "./MusicCard";
import { CARD_CLASS } from "./theme";
import type { FixturesView, RoomStatus } from "./types";
import type { NewLogEntry } from "./useRoomConnection";

/** A light's state in the summary: [plural, singular] verb phrase, in the order the summary lists them. */
const STATES: Record<string, [string, string]> = {
  follow: ["follow the music", "follows the music"],
  armed: ["wait for the music", "waits for the music"],
  on: ["on", "on"],
  off: ["off", "off"],
  taken: ["taken over by another app", "taken over by another app"],
  away: ["can't be reached", "can't be reached"],
};

const HUE_STATE: Record<string, keyof typeof STATES> = {
  following: "follow",
  armed: "armed",
  house: "on",
  off: "off",
  taken_over: "taken",
};

/** The DMX lights' believed values (status.subsystems.light: "dmx" | "off" | {hex, bri}), by light id. */
function dmxStates(status: RoomStatus | null | undefined, only?: (id: string) => boolean): string[] {
  const believed = (status?.subsystems as Record<string, { believed?: unknown }> | undefined)?.light?.believed;
  if (!believed || typeof believed !== "object") return [];
  return Object.entries(believed as Record<string, unknown>).flatMap(([id, v]) => {
    if (only && !only(id)) return [];
    if (v === "dmx") return ["follow"];
    if (v === "off") return ["off"];
    if (v && typeof v === "object") return ["on"];
    return [];
  });
}

/**
 * The Lights fold's summary: the lights by what they're doing. `view`: the fixtures view (null while loading);
 * `hueMissing`: room_service has no fixtures view (no Hue lamps); `effects`: the fold also holds room effects (the glitterball).
 */
export function lightsWords(view: FixturesView | null, hueMissing: boolean, status: RoomStatus | null, effects: boolean): string {
  const hueOn = !hueMissing && !(view && !view.enabled);
  if (hueOn && !view) return "Finding the lights…";
  const hue = hueOn && view ? view.fixtures.filter((f) => f.kind === "hue").map((f) => HUE_STATE[f.status ?? ""] ?? "away") : [];
  const dmx = dmxStates(status);
  const total = hue.length + dmx.length;
  if (!total) return effects ? "Room effects" : "No lights";
  const noun = (n: number) => (dmx.length === 0 ? (n === 1 ? "lamp" : "lamps") : n === 1 ? "light" : "lights");
  const counts = new Map<string, number>();
  for (const s of [...hue, ...dmx]) counts.set(s, (counts.get(s) ?? 0) + 1);
  const order = Object.keys(STATES);
  const parts = [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || order.indexOf(a[0]) - order.indexOf(b[0]))
    .map(([s, n], i) => {
      const [many, one] = STATES[s] ?? STATES.away;
      return `${n}${i === 0 ? ` ${noun(n)}` : ""} ${n === 1 ? one : many}`;
    });
  return parts.join(" · ");
}

/** Whether any lamp or DMX light is set to follow the music (following, or waiting for it): the look shows on it. */
export function lightsFollow(view: FixturesView | null, status: RoomStatus | null): boolean {
  const hue = (view?.enabled ? view.fixtures : []).some((f) => f.kind === "hue" && f.control === "dmx");
  return hue || dmxStates(status).includes("follow");
}

/** House lights and Lights low: two switches for every lamp at once (show_service's own actions). */
export function RoomLightsRow({ api, feed, addLog }: { api: RoomApi; feed: ShowFeed; addLog: (e: NewLogEntry) => void }) {
  const { card } = useShowState(feed);
  const main = useSend(feed, addLog);
  const { send, busy } = main;
  const [pendingHouse, setPendingHouse] = useState<boolean | null>(null);
  const [pendingLow, setPendingLow] = useState<boolean | null>(null);

  const setHouse = (on: boolean) => {
    setPendingHouse(on);
    void send("house", on ? "House lights" : "back from house lights", () => api.showAction("house", on), { latest: true }).then((ok) => {
      if (ok) setTimeout(() => setPendingHouse(null), 3_000);
      else if (ok === false) setPendingHouse(null);
    });
  };
  const setLow = (on: boolean) => {
    setPendingLow(on);
    void send("low", on ? "Lights low" : "lights back up", () => api.showAction("low", on), { latest: true }).then((ok) => {
      if (ok) setTimeout(() => setPendingLow(null), 3_000);
      else if (ok === false) setPendingLow(null);
    });
  };
  // the card's next patch agrees: drop the guess at once
  useEffect(() => {
    if (pendingHouse !== null && !!card?.house === pendingHouse) setPendingHouse(null);
  }, [card?.house, pendingHouse]);
  useEffect(() => {
    if (pendingLow !== null && !!card?.low === pendingLow) setPendingLow(null);
  }, [card?.low, pendingLow]);

  const houseOn = pendingHouse ?? !!card?.house;
  const lowOn = pendingLow ?? !!card?.low;

  const row = (key: "house" | "low", label: string, sub: string, on: boolean, set: (v: boolean) => void) => (
    <label title={sub} className="flex min-h-11 min-w-0 cursor-pointer items-center justify-between gap-2 px-3 sm:px-4">
      <span className="min-w-0 truncate text-base font-medium">
        {label}
        <span className="sr-only">: {sub}</span>
      </span>
      <span className="flex shrink-0 items-center gap-2">
        {busy(key) && <Spinner />}
        <Switch aria-label={label} checked={on} onCheckedChange={set} />
      </span>
    </label>
  );

  return (
    <div className={CARD_CLASS} data-testid="room-lights-row">
      <div className="grid grid-cols-2 divide-x divide-stone-100">
        {row("house", "House lights", "The lamps light the room as usual instead of following the music.", houseOn, setHouse)}
        {row("low", "Lights low", "Dims the lamps. The screens stay as they are.", lowOn, setLow)}
      </div>
      {main.note && <NoteLine note={main.note} className="border-t border-stone-100 px-3 py-2 sm:px-4" />}
    </div>
  );
}
