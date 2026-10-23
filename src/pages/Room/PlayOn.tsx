// Play on (owner, 23 Oct: "'play on' option prob. needs elevating to its own control on the page as too hard to find
// otherwise"): where the piano music is heard, as one row of chips: show_service's outputs (PC22 speakers, Sonos,
// Screens only; GET /show/api/v1/test `outputs`, labels as it sends them) and This device (PC22's synth streamed to
// this phone or tablet: listenHere.ts). The same single choice usePianoSound keeps for the page; the Sound chips stay
// with what they play (Test music, the Piano card, Now playing).
//   - This device: show_service is told "none" (the screens only), so PC22 and Sonos stay quiet, and this browser
//     plays the stream. Offered only while show_service lists "none".
//   - Also play on this device (a switch, shown while PC22 or Sonos is picked): the stream as well as the speakers.
//   - Screens only turns this device off too (otherwise it would be This device).
// PlayOnChoice is the chips (also in /music's Now playing); PlayOnCard is the Room page's own small card under the
// modes. A change there reaches what's playing: the music player takes it ({action: "set", output}: it restarts its
// piece with it, and remembers it while stopped), else a test piece or a recording playing on the test piano starts
// again with it (as a change in Test music or the Piano card always did).

import { useCallback, useEffect, useState } from "react";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import { MusicApi } from "@/pages/Music/api";
import { PLAYER_BUSY } from "@/pages/Music/usePlayer";
import { explainError, type RoomApi } from "./api";
import { Spinner } from "./bits";
import { ListenLine } from "./ListenHere";
import { HERE_LABEL, HERE_OUTPUT, listenOff, listenOn, SCREENS_ONLY, setListenView, useListenHere } from "./listenStream";
import { CARD_CLASS } from "./theme";
import type { ShowTestView } from "./types";
import { usePianoSound, type PianoSound } from "./usePianoSound";
import type { NewLogEntry } from "./useRoomConnection";

// one compact row of small pills (owner, 23 Oct: the 2x2 grid of big buttons was "ridiculously space inefficient")
const CHIP = "min-h-9 whitespace-nowrap rounded-full border px-3 text-[15px] font-medium transition-colors disabled:cursor-default";
const CHIP_ON = "border-teal-300 bg-teal-50 text-teal-900";
const CHIP_OFF = "border-stone-200 bg-white hover:bg-stone-50";
const LIST_MS = 30_000;
/** The test piano's states that count as playing (or about to). */
const PIANO_BUSY = ["preparing", "starting", "playing"];

/**
 * The Play on chips. `sound.output` is show_service's id; `onOutput` gets only show_service's ids (This device sends
 * "none"), and only when that changes. Turning This device on or off happens here, in the tap (mobile browsers start
 * sound only from one).
 */
export function PlayOnChoice({
  sound,
  disabled,
  onOutput,
  label = "Play on",
}: {
  sound: PianoSound;
  disabled?: boolean;
  onOutput: (id: string) => void;
  /** The chips' heading (null: none, when the card's title already says it). */
  label?: string | null;
}) {
  const here = useListenHere();
  const server = sound.outputs;
  if (!server.length) return null;
  const canHere = server.some((o) => o.id === SCREENS_ONLY);
  const items = canHere ? [...server, { id: HERE_OUTPUT, label: HERE_LABEL }] : server;
  const shown = canHere && here.on && sound.output === SCREENS_ONLY ? HERE_OUTPUT : sound.output;
  const speakers = canHere && shown !== HERE_OUTPUT && shown !== SCREENS_ONLY;

  const pick = (id: string) => {
    if (id === HERE_OUTPUT) {
      if (!here.on) listenOn();
      if (sound.output !== SCREENS_ONLY) onOutput(SCREENS_ONLY);
      return;
    }
    if (shown === HERE_OUTPUT || id === SCREENS_ONLY) listenOff();
    if (id !== sound.output) onOutput(id);
  };

  return (
    <div className="space-y-1.5">
      <div role="radiogroup" aria-label="Play on" className="flex flex-wrap items-center gap-1.5" data-testid="play-on-chips">
        {label && <span className="mr-1 text-[15px] font-medium text-stone-700">{label}</span>}
        {items.map((it) => {
          const sel = it.id === shown;
          return (
            <button
              key={it.id}
              type="button"
              role="radio"
              aria-checked={sel}
              disabled={disabled || sel}
              onClick={() => pick(it.id)}
              className={cn(CHIP, sel ? CHIP_ON : CHIP_OFF, !sel && disabled && "opacity-60")}
            >
              {it.label}
            </button>
          );
        })}
      </div>
      {speakers && (
        <label className="flex min-h-9 cursor-pointer items-center gap-2">
          <span className="text-sm text-muted-foreground">Also play on this device</span>
          <Switch
            aria-label="Also play on this device"
            checked={here.on}
            disabled={disabled}
            onCheckedChange={(v) => (v ? listenOn() : listenOff())}
          />
        </label>
      )}
      <ListenLine onOpen={() => setListenView(true)} />
    </div>
  );
}

/** The Room page's Play on card. Hidden until show_service lists where the piano can play. */
export function PlayOnCard({
  api,
  addLog,
  reportError,
}: {
  api: RoomApi;
  addLog: (e: NewLogEntry) => void;
  reportError: (e: unknown) => void;
}) {
  const [view, setView] = useState<ShowTestView | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const sound = usePianoSound(view);

  const load = useCallback(
    async (signal?: AbortSignal) => {
      try {
        const v = await api.showTest(signal);
        if (v) setView(v);
      } catch {
        /* not answering: keep the last list */
      }
    },
    [api],
  );
  useEffect(() => {
    const ac = new AbortController();
    void load(ac.signal);
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") void load(ac.signal);
    }, LIST_MS);
    return () => {
      ac.abort();
      window.clearInterval(id);
    };
  }, [load]);

  if (!sound.outputs.length) return null;

  const apply = async (id: string) => {
    sound.setOutput(id);
    setBusy(true);
    setErr(null);
    try {
      const music = new MusicApi(api.baseUrl);
      const pv = await music.player().catch(() => null);
      if (pv) await music.playerAction({ action: "set", output: id });
      if (!pv || !PLAYER_BUSY.includes(pv.state)) {
        const tv = await api.showTest();
        const p = tv?.sources?.piano;
        if (p?.piece && PIANO_BUSY.includes(p.state) && p.output !== id)
          await api.showTestStart("piano", p.piece, { instrument: p.instrument ?? sound.instrument, output: id, once: p.once });
        if (tv) setView(tv);
      }
      const where = sound.outputs.find((o) => o.id === id)?.label ?? id;
      addLog({ type: "fixture", level: "info", summary: `Play on: ${where}` });
    } catch (e) {
      reportError(e);
      setErr(explainError(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section aria-labelledby="room-play-on" data-testid="play-on">
      <div className="mb-2 flex items-center justify-between gap-2">
        <h2 id="room-play-on" className="text-lg font-semibold text-stone-800">
          Play on
        </h2>
        {busy && <Spinner />}
      </div>
      <div className={cn(CARD_CLASS, "space-y-2 px-3 py-2.5 sm:px-4")}>
        <PlayOnChoice sound={sound} disabled={busy} onOutput={(id) => void apply(id)} label={null} />
        {err && <p className="text-sm text-rose-700">{err}</p>}
      </div>
    </section>
  );
}
