// The virtual piano's sound, where it plays and how fast: one choice for the whole page (Test music's piano and the
// Piano card's recordings use the same), remembered in this browser. The lists come from show_service (GET
// /show/api/v1/test: `instruments`, only installed ones, `default_instrument`, `outputs`); a remembered choice the
// server no longer lists falls back to its default sound and to PC22's speakers (this device's own output stands).

import { useCallback, useSyncExternalStore } from "react";
import { storageGet, storageSet } from "./api";
import { myOutput } from "./listenStream";
import { SPEED_MAX, SPEED_MIN } from "./SpeedNudges";
import type { ShowInstrument, ShowOutput, ShowTestStartOpts, ShowTestView } from "./types";

const SOUND_KEY = "sm_room_piano_sound";
const OUTPUT_KEY = "sm_room_piano_output";
const SPEED_KEY = "sm_room_piano_speed";
const DEFAULT_OUTPUT = "pc22";

type Saved = { instrument: string | null; output: string | null; speed: number };

function savedSpeed(): number {
  const x = Number(storageGet(SPEED_KEY));
  return Number.isFinite(x) && x >= SPEED_MIN && x <= SPEED_MAX ? x : 1;
}

// A tiny store, so every user of the hook on the page sees a change at once (and other tabs via "storage").
const listeners = new Set<() => void>();
let saved: Saved | null = null;

function read(): Saved {
  if (!saved) saved = { instrument: storageGet(SOUND_KEY), output: storageGet(OUTPUT_KEY), speed: savedSpeed() };
  return saved;
}

function write(key: string, value: string) {
  storageSet(key, value);
  const now = read();
  saved =
    key === SOUND_KEY ? { ...now, instrument: value } : key === SPEED_KEY ? { ...now, speed: savedSpeed() } : { ...now, output: value };
  listeners.forEach((l) => l());
}

function onStorage(e: StorageEvent) {
  if (e.key !== SOUND_KEY && e.key !== OUTPUT_KEY && e.key !== SPEED_KEY) return;
  saved = null;
  listeners.forEach((l) => l());
}

function subscribe(l: () => void) {
  listeners.add(l);
  if (listeners.size === 1) window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(l);
    if (listeners.size === 0) window.removeEventListener("storage", onStorage);
  };
}

export interface PianoSound {
  /** The server offers a choice (an older show_service sends no instruments: nothing is shown or sent). */
  available: boolean;
  instruments: ShowInstrument[];
  outputs: ShowOutput[];
  instrument: string | null;
  output: string | null;
  setInstrument: (id: string) => void;
  setOutput: (id: string) => void;
  /** The playback speed (1 as written), remembered in this browser like the sound. */
  speed?: number;
  setSpeed?: (x: number) => void;
  /** What POST /test gets for the piano: the choice, or nothing on an older show_service. */
  opts: ShowTestStartOpts | undefined;
}

export function usePianoSound(view: Pick<ShowTestView, "instruments" | "default_instrument" | "outputs"> | null | undefined): PianoSound {
  const s = useSyncExternalStore(subscribe, read, read);
  const instruments = (view?.instruments ?? []).filter((i) => !!i?.id);
  const outputs = (view?.outputs ?? []).filter((o) => !!o?.id);
  const instrument = instruments.some((i) => i.id === s.instrument)
    ? s.instrument
    : (instruments.find((i) => i.id === view?.default_instrument)?.id ?? instruments[0]?.id ?? null);
  // this device is an output as soon as it's picked, before show_service's list has it
  const output = outputs.some((o) => o.id === s.output) || (!!s.output && s.output === myOutput())
    ? s.output
    : (outputs.find((o) => o.id === DEFAULT_OUTPUT)?.id ?? outputs[0]?.id ?? null);
  const available = instruments.length > 0;
  const setInstrument = useCallback((id: string) => write(SOUND_KEY, id), []);
  const setOutput = useCallback((id: string) => write(OUTPUT_KEY, id), []);
  const setSpeed = useCallback((x: number) => write(SPEED_KEY, String(x)), []);
  return {
    available,
    instruments,
    outputs,
    instrument,
    output,
    setInstrument,
    setOutput,
    speed: s.speed,
    setSpeed,
    opts: available ? { instrument, ...(outputs.length ? { output } : {}), ...(s.speed !== 1 ? { speed: s.speed } : {}) } : undefined,
  };
}
