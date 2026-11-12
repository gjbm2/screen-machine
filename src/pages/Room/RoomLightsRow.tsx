// The top of the Room page's Lights fold and of the Lights page: one dimmer for every light that is on (owner, 12 Nov:
// "'house lights' ... doesn't really seem to work... nor 'lights low' ... surely there should just be a global dimmer
// here, really, for all active lights"). It is the room's brightness.lamps: the lamps following the music, the lamps
// on as house lights and the DMX lights all scale by it; a mode that sets it (Disco's 35 %) moves it too. Sent on
// letting go, held on screen until the room reports it.
//
// lightsWords(): the fold's closed summary from the lights' real states (owner, 24 Oct: it said "4 lamps follow the
// music" while all four were off): the Hue lamps from room_service's fixtures view (status per lamp), the DMX lights
// from the light subsystem's believed values in the room's status. "4 lamps off", "2 lamps follow the music · 2 on";
// "lights" when the kinds are mixed. No light is named here.

import { useEffect, useState } from "react";
import { Slider } from "@/components/ui/slider";
import type { RoomApi } from "./api";
import { Spinner } from "./bits";
import { dmxStates } from "./lightStates";
import { CARD_CLASS } from "./theme";
import type { FixturesView, RoomStatus } from "./types";
import type { NewLogEntry } from "./useRoomConnection";

export { lightsFollow } from "./lightStates";

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

/** The lights' dimmer (brightness.lamps) as the room believes it, 0-100; null when the room has none. */
export function dimmerValue(status: RoomStatus | null): number | null {
  const sub = status?.subsystems?.brightness;
  const v = sub?.members?.lamps?.believed ?? (sub?.believed as Record<string, unknown> | undefined)?.lamps;
  return typeof v === "number" ? v : null;
}

/** One dimmer for every light that is on: the room's brightness.lamps. */
export function RoomLightsRow({
  api,
  status,
  addLog,
  reportError,
}: {
  api: RoomApi;
  status: RoomStatus | null;
  addLog: (e: NewLogEntry) => void;
  reportError: (e: unknown) => void;
}) {
  const believed = dimmerValue(status);
  const [n, setN] = useState<number | null>(null);         // moving, or sent and not reported yet
  const [busy, setBusy] = useState(false);
  const [sentAt, setSentAt] = useState(0);
  useEffect(() => {
    if (n !== null && !busy && believed === n) setN(null);  // the room agrees: show its value again
  }, [believed, n, busy]);
  useEffect(() => {
    if (!sentAt) return;
    const t = window.setTimeout(() => setN(null), 8_000);   // never stuck on a guess
    return () => window.clearTimeout(t);
  }, [sentAt]);
  if (believed === null) return null;
  const shown = n ?? believed;

  const commit = async (v: number) => {
    setBusy(true);
    try {
      await api.setTargets({ "brightness.lamps": v });
      addLog({ type: "fixture", level: "info", summary: `Lights: ${v}%` });
    } catch (e) {
      reportError(e);
      setN(null);
    } finally {
      setBusy(false);
      setSentAt(Date.now());
    }
  };

  return (
    <div className={CARD_CLASS} data-testid="room-lights-row">
      <div className="flex min-h-11 items-center gap-3 px-3 sm:px-4">
        <span className="shrink-0 text-base font-medium" title="Dims every light that is on: the lamps and the DMX lights. The screens stay as they are.">
          Brightness
        </span>
        <Slider
          aria-label="Brightness of all the lights"
          className="flex-1 py-3 [&_[role=slider]]:h-6 [&_[role=slider]]:w-6"
          min={0}
          max={100}
          step={5}
          value={[shown]}
          onValueChange={([v]) => setN(v)}
          onValueCommit={([v]) => void commit(v)}
        />
        <span className="flex w-14 shrink-0 items-center justify-end gap-1 font-mono text-[15px] tabular-nums text-stone-700">
          {busy && <Spinner />}
          {shown}%
        </span>
      </div>
    </div>
  );
}
