// Play on: where the piano music is heard, one control used on every page (owner, 23 Oct: "'play on' option prob.
// needs elevating to its own control on the page as too hard to find otherwise"; on the score page "there's nowhere
// to control where the music is going to come out. there should be one standard component re-used across all the
// various pages"). The Room page (under the modes), /music's Now playing and the score page (its header and the music
// stand's bar, compact) all mount <PlayOn baseUrl=… />, which does everything itself:
//   - the choices: show_service's outputs (PC22 speakers, Sonos, Screens only, and the browsers that have played it,
//     by name: GET /show/api/v1/test `outputs`), with this browser's own shown as This device (PC22's synth played on
//     this phone or tablet: listenStream.ts);
//   - which is picked: the room's output now (GET /show/api/v1/piano/devices `output`, read every 5 s, so a change on
//     another page or device shows here), else this browser's last choice;
//   - a pick: This device is registered by name first; the music player takes the output ({action: "set", output}:
//     it carries on with it, and remembers it while stopped or paused), and a test piece or recording playing on the
//     test piano starts again with it. Kept as this browser's choice too (usePianoSound: Test music and the Piano card
//     start with it).
//   - This device plays here; picking anything else (here or on any page) moves the output away and this device goes
//     quiet by itself. Also play on this device (a switch, shown while PC22 or Sonos is picked): the sound here as well
//     as the speakers. Screens only turns this device off too.
// Turning This device on happens in the tap itself (mobile browsers start sound only from one).

import { useCallback, useEffect, useMemo, useState } from "react";
import { Speaker } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import { MusicApi } from "@/pages/Music/api";
import { explainError, loadToken, RoomApi } from "./api";
import { Spinner } from "./bits";
import { ListenLine } from "./ListenHere";
import {
  fetchDevices,
  HERE_LABEL,
  listenOff,
  listenOn,
  myOutput,
  registerDevice,
  SCREENS_ONLY,
  setListenView,
  setRoomOutput as setListenRoomOutput,
  useListenHere,
} from "./listenStream";
import { CARD_CLASS, THEME_CLASS } from "./theme";
import type { ShowOutput, ShowTestView } from "./types";
import { usePianoSound } from "./usePianoSound";
import type { NewLogEntry } from "./useRoomConnection";

// one compact row of small pills (owner, 23 Oct: the 2x2 grid of big buttons was "ridiculously space inefficient")
const CHIP = "min-h-9 whitespace-nowrap rounded-full border px-3 text-[15px] font-medium transition-colors disabled:cursor-default";
const CHIP_ON = "border-teal-300 bg-teal-50 text-teal-900";
const CHIP_OFF = "border-stone-200 bg-white hover:bg-stone-50";
const LIST_MS = 30_000;
/** How often the room's output is read (another page may have moved it). */
const OUTPUT_MS = 5_000;
/** The player's and the test piano's states that count as playing (or about to). */
const PLAYER_BUSY = ["preparing", "playing", "paused"];
const PIANO_BUSY = ["preparing", "starting", "playing"];

/** The choices, this device last, its own entry named This device. */
function choicesOf(server: ShowOutput[]): ShowOutput[] {
  const mine = myOutput();
  return [...server.filter((o) => !o.device), ...server.filter((o) => o.device && o.id !== mine), { id: mine, label: HERE_LABEL, device: true }];
}

function outputLabel(id: string | null, server: ShowOutput[]): string | null {
  if (!id) return null;
  if (id === myOutput()) return HERE_LABEL;
  return server.find((o) => o.id === id)?.label ?? null;
}

/** Where the piano plays: the choices, what's picked, and a pick. One per mount; each reads the room for itself. */
function usePlayOn(baseUrl: string, addLog?: (e: NewLogEntry) => void) {
  const room = useMemo(() => new RoomApi(baseUrl, loadToken(baseUrl).token), [baseUrl]);
  const music = useMemo(() => new MusicApi(baseUrl), [baseUrl]);
  const [view, setView] = useState<ShowTestView | null>(null);
  const [roomOutput, setRoomOutput] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const local = usePianoSound(view);
  const here = useListenHere();

  const loadList = useCallback(
    async (signal?: AbortSignal) => {
      const v = await room.showTest(signal).catch(() => null);
      if (v) setView(v);
    },
    [room],
  );
  const readOutput = useCallback(
    async (signal?: AbortSignal) => {
      const d = await fetchDevices(signal, baseUrl);
      if (d) setRoomOutput(d.output ?? null);
    },
    [baseUrl],
  );
  useEffect(() => {
    const ac = new AbortController();
    void loadList(ac.signal);
    void readOutput(ac.signal);
    const a = window.setInterval(() => document.visibilityState === "visible" && void loadList(ac.signal), LIST_MS);
    const b = window.setInterval(() => document.visibilityState === "visible" && void readOutput(ac.signal), OUTPUT_MS);
    return () => {
      ac.abort();
      window.clearInterval(a);
      window.clearInterval(b);
    };
  }, [loadList, readOutput]);

  const server = local.outputs;
  const known = (id: string | null) => !!id && (id === myOutput() || server.some((o) => o.id === id));
  const output = known(roomOutput) ? roomOutput : local.output;
  // this device's line ("Quiet here · the room plays on …") follows what this control reads too
  useEffect(() => {
    if (here.on && roomOutput) setListenRoomOutput(roomOutput, outputLabel(roomOutput, server));
  }, [here.on, roomOutput, server]);

  const apply = async (id: string) => {
    local.setOutput(id);
    setRoomOutput(id);
    setBusy(true);
    setErr(null);
    try {
      if (id === myOutput()) await registerDevice(local.instrument, baseUrl);
      const pv = await music.player().catch(() => null);
      if (pv) await music.playerAction({ action: "set", output: id });
      if (!pv || !PLAYER_BUSY.includes(pv.state)) {
        const tv = await room.showTest();
        const p = tv?.sources?.piano;
        if (p?.piece && PIANO_BUSY.includes(p.state) && p.output !== id)
          await room.showTestStart("piano", p.piece, { instrument: p.instrument ?? local.instrument, output: id, once: p.once });
        if (tv) setView(tv);
      }
      void readOutput();
      addLog?.({ type: "fixture", level: "info", summary: `Play on: ${outputLabel(id, server) ?? id}` });
    } catch (e) {
      setErr(explainError(e));
    } finally {
      setBusy(false);
    }
  };

  /** A tap on a choice: This device starts its sound here and now (the tap), the rest stop it. */
  const pick = (id: string) => {
    const mine = myOutput();
    const isDevice = (x: string) => x === mine || server.some((o) => o.id === x && o.device);
    if (id === mine) {
      if (!here.on || here.also) listenOn(false);
    } else if (here.on && (!here.also || id === SCREENS_ONLY || isDevice(id))) listenOff();
    if (id !== output || id === mine) void apply(id);
  };

  const speakers = !!output && output !== SCREENS_ONLY && !output.startsWith("device:");
  return { server, output, busy, err, pick, speakers, here };
}

/**
 * Play on, complete: the choices as one row of pills (with the Also switch and this device's line), or `compact`: one
 * small button saying where it plays that opens them (the score's bars).
 */
export function PlayOn({
  baseUrl,
  compact,
  label = "Play on",
  addLog,
  className,
}: {
  /** room_service's address (the page's API base). */
  baseUrl: string;
  compact?: boolean;
  /** The pills' heading (null: none, when a card's title already says it). */
  label?: string | null;
  addLog?: (e: NewLogEntry) => void;
  className?: string;
}) {
  const p = usePlayOn(baseUrl, addLog);
  if (!p.server.length) return null;

  const body = (
    <div className={cn("space-y-1.5", !compact && className)}>
      <div role="radiogroup" aria-label="Play on" className="flex flex-wrap items-center gap-1.5" data-testid="play-on-chips">
        {label && <span className="mr-1 text-[15px] font-medium text-stone-700">{label}</span>}
        {choicesOf(p.server).map((it) => {
          const sel = it.id === p.output;
          return (
            <button
              key={it.id}
              type="button"
              role="radio"
              aria-checked={sel}
              disabled={p.busy || (sel && it.id !== myOutput()) || (sel && p.here.on && !p.here.also)}
              onClick={() => p.pick(it.id)}
              className={cn(CHIP, sel ? CHIP_ON : CHIP_OFF, !sel && p.busy && "opacity-60")}
            >
              {it.label}
            </button>
          );
        })}
        {p.busy && <Spinner className="ml-1" />}
      </div>
      {p.speakers && (
        <label className="flex min-h-9 cursor-pointer items-center gap-2">
          <span className="text-sm text-muted-foreground">Also play on this device</span>
          <Switch
            aria-label="Also play on this device"
            checked={p.here.on && p.here.also}
            disabled={p.busy}
            onCheckedChange={(v) => (v ? listenOn(true) : listenOff())}
          />
        </label>
      )}
      <ListenLine onOpen={() => setListenView(true)} />
      {p.err && <p className="text-sm text-rose-700">{p.err}</p>}
    </div>
  );
  if (!compact) return body;

  const where = outputLabel(p.output, p.server) ?? "Play on";
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          title="Where the music plays"
          className={cn(
            "inline-flex h-9 max-w-[11rem] shrink-0 items-center gap-1.5 rounded-full border border-stone-200 bg-white px-3 text-[15px] font-medium text-stone-700 hover:bg-stone-50",
            className,
          )}
          data-testid="play-on-compact"
        >
          <Speaker className="h-4 w-4 shrink-0" aria-hidden />
          <span className="truncate">{where}</span>
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" className={cn(THEME_CLASS, "z-[70] w-[min(22rem,calc(100vw-2rem))] rounded-2xl p-3")}>
        {body}
      </PopoverContent>
    </Popover>
  );
}

/** The Room page's Play on card. Hidden until show_service lists where the piano can play. */
export function PlayOnCard({ baseUrl, addLog }: { baseUrl: string; addLog: (e: NewLogEntry) => void }) {
  return (
    <section aria-labelledby="room-play-on" data-testid="play-on">
      <h2 id="room-play-on" className="mb-2 text-lg font-semibold text-stone-800">
        Play on
      </h2>
      <div className={cn(CARD_CLASS, "px-3 py-2.5 sm:px-4")}>
        <PlayOn baseUrl={baseUrl} addLog={addLog} label={null} />
      </div>
    </section>
  );
}
