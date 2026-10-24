// Record on a score (owner, 24 Oct: "record should be on score page as well (if it's 'mine') so user can record
// and/or record sections"): only on the listener's own pieces (uploads and recordings, the ones with a pencil). One
// Record button and a small choice:
//   Whole piece    play it through; Stop keeps the take as a new recording named after the piece.
//   This section   pick the bars (from the bar playing, or the first on the page in view, four bars on); play them;
//                  Stop puts what was played in place of those bars, in a NEW copy of the piece. This one stays as it is.
// Either way the new recording's score is offered at once ("See the score"). The recorder is the Room page's
// (show_service recorder.py; POST /show/api/v1/piano/record {on, piece?, section?}): the section goes as the piece's own
// seconds from the score's bar map (each bar's first onset to the next bar's), with the bar numbers for its name.
// The Record / Stop button and its line are RecordButton.tsx's, as on the Room page's Piano card.

import { useCallback, useEffect, useRef, useState } from "react";
import { FileMusic, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { CARD_CLASS, TAP_BUTTON_CLASS } from "@/pages/Room/theme";
import { RecordButton, RecordingLine, showSocketUrl, useKeyFlash } from "@/pages/Room/RecordButton";
import type { PianoRecording, PianoRecordingsView } from "@/pages/Room/types";
import { musicWords } from "./api";
import { CHIP, CHIP_OFF, CHIP_ON, PRIMARY, useOpenScore } from "./bits";
import { useMusic } from "./context";

const POLL_MS = 1000;
/** A new section's length, in bars. */
const SECTION_BARS = 4;

type Bar = [number, number, number];
type What = "whole" | "section";

export function ScoreRecord({
  pieceId,
  bars,
  hereBar,
  onClose,
}: {
  pieceId: string;
  /** The score's bar map: [bar, t0, t1] in the piece's own seconds (null until it's there). */
  bars: Bar[] | null;
  /** Where a section starts unless chosen: the bar playing, else the first on the page in view. */
  hereBar: number | null;
  onClose: () => void;
}) {
  const { api, bumpLibrary } = useMusic();
  const openScore = useOpenScore();
  const [what, setWhat] = useState<What>("whole");
  const [from, setFrom] = useState<number | null>(null);
  const [to, setTo] = useState<number | null>(null);
  const [view, setView] = useState<{ v: PianoRecordingsView; at: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [done, setDone] = useState<{ take: PianoRecording | null; made?: string; note?: string } | null>(null);
  const [now, setNow] = useState(() => Date.now());
  /** This panel started the take running (else Stop keeps it plainly: it was started somewhere else). */
  const startedHere = useRef(false);

  const recording = view?.v.recording?.state === "recording";
  const flash = useKeyFlash(showSocketUrl(api.baseUrl), recording);

  // the recorder's state: once on opening, every second while it records
  const load = useCallback(
    async (signal?: AbortSignal) => {
      try {
        const v = await api.pianoRecordings(signal);
        if (v?.recording) setView({ v, at: Date.now() });
      } catch {
        /* the next poll, or the button, says what's wrong */
      }
    },
    [api],
  );
  useEffect(() => {
    const ac = new AbortController();
    void load(ac.signal);
    return () => ac.abort();
  }, [load]);
  useEffect(() => {
    if (!recording) return;
    const ac = new AbortController();
    const poll = window.setInterval(() => {
      if (document.visibilityState === "visible") void load(ac.signal);
    }, POLL_MS);
    const tick = window.setInterval(() => setNow(Date.now()), 500);
    return () => {
      ac.abort();
      window.clearInterval(poll);
      window.clearInterval(tick);
    };
  }, [recording, load]);

  // the section: from the bar here, SECTION_BARS on (the user can change both)
  const nums = bars?.map((b) => b[0]) ?? [];
  const first = from ?? (hereBar !== null && nums.includes(hereBar) ? hereBar : (nums[0] ?? null));
  const firstIdx = first !== null ? nums.indexOf(first) : -1;
  const last = to !== null && first !== null && to >= first ? to : firstIdx >= 0 ? nums[Math.min(nums.length - 1, firstIdx + SECTION_BARS - 1)] : null;
  const fromBar = bars?.find((b) => b[0] === first) ?? null;
  const toBar = bars?.find((b) => b[0] === last) ?? null;
  const sectionReady = !!fromBar && !!toBar;
  const span = first !== null && last !== null ? (first === last ? `bar ${first}` : `bars ${first} to ${last}`) : "";

  const v = view?.v;
  const elapsed = recording
    ? (v?.recording.since_s ?? v?.recording.duration_s ?? 0) + (view ? Math.max(0, now - view.at) / 1000 : 0)
    : 0;

  const start = async () => {
    setBusy(true);
    setErr(null);
    setDone(null);
    try {
      const r = await api.pianoRecord(true);
      startedHere.current = true;
      setView({ v: r, at: Date.now() });
    } catch (e) {
      setErr(musicWords(e));
    } finally {
      setBusy(false);
    }
  };
  const stop = async () => {
    setBusy(true);
    setErr(null);
    try {
      const mine = startedHere.current;
      const ask =
        !mine ? undefined
        : what === "section" && fromBar && toBar
          ? { piece: pieceId, section: { from_s: fromBar[1], to_s: toBar[2], from_bar: fromBar[0], to_bar: toBar[0] } }
          : { piece: pieceId };
      const r = await api.pianoRecord(false, ask);
      startedHere.current = false;
      setView({ v: r, at: Date.now() });
      if (r.last) {
        setDone({ take: r.last, made: r.made, note: r.error });
        bumpLibrary();
      } else setDone({ take: null, note: r.error ?? "Nothing was played, so nothing was kept." });
    } catch (e) {
      setErr(musicWords(e));
    } finally {
      setBusy(false);
    }
  };

  const select = "min-h-10 rounded-full border border-stone-200 bg-white px-3 text-[15px] font-medium text-stone-700";
  const locked = recording || busy;

  return (
    <div className={cn(CARD_CLASS, "space-y-3 px-4 py-3")} data-testid="score-record">
      <div className="flex items-center gap-2">
        <h2 className="flex-1 text-base font-semibold text-stone-800">Record on the piano</h2>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="-mr-2 grid h-11 w-11 place-items-center rounded-full text-stone-500 hover:bg-stone-100"
        >
          <X className="h-5 w-5" aria-hidden />
        </button>
      </div>

      <div role="radiogroup" aria-label="What to record" className="flex flex-wrap gap-1.5">
        {(
          [
            ["whole", "Whole piece"],
            ["section", "This section"],
          ] as [What, string][]
        ).map(([k, words]) => (
          <button
            key={k}
            type="button"
            role="radio"
            aria-checked={what === k}
            disabled={locked && what !== k}
            onClick={() => setWhat(k)}
            className={cn(CHIP, what === k ? CHIP_ON : CHIP_OFF, "disabled:opacity-50")}
          >
            {words}
          </button>
        ))}
      </div>

      {what === "section" &&
        (bars && bars.length > 0 ? (
          <div className="flex flex-wrap items-center gap-2 text-[15px]">
            <span>Bars</span>
            <select
              aria-label="From bar"
              value={first ?? ""}
              disabled={locked}
              onChange={(e) => {
                const n = Number(e.target.value);
                setFrom(n);
                if (to !== null && to < n) setTo(null);
              }}
              className={select}
            >
              {nums.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
            <span>to</span>
            <select
              aria-label="To bar"
              value={last ?? ""}
              disabled={locked}
              onChange={(e) => setTo(Number(e.target.value))}
              className={select}
            >
              {nums
                .filter((n) => first === null || n >= first)
                .map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
            </select>
          </div>
        ) : (
          <p className="text-[15px] text-muted-foreground">Finding the bars…</p>
        ))}

      <p className="text-sm text-muted-foreground">
        {what === "section"
          ? `Play ${span || "the bars"}, then Stop. What you play takes their place in a new copy of this piece; this one stays as it is.`
          : "Play the piece, then Stop. It's kept as a new recording of this piece."}
      </p>

      <div className="flex flex-wrap items-center gap-3">
        <RecordButton
          recording={recording}
          busy={busy}
          disabled={!recording && what === "section" && !sectionReady}
          elapsed={elapsed}
          onClick={() => void (recording ? stop() : start())}
        />
        {recording && <RecordingLine notes={v?.recording.notes ?? 0} flash={flash} />}
      </div>
      {recording && !startedHere.current && (
        <p className="text-sm text-muted-foreground">The piano was already recording (started somewhere else): Stop keeps that take as it is.</p>
      )}

      {done?.take && (
        <div className="space-y-2 rounded-xl border border-teal-300 bg-teal-50/70 p-3" role="status">
          <p className="text-base font-semibold">
            {done.made === "version" ? "New copy with your bars" : "New take"} · {done.take.title}
          </p>
          <button
            type="button"
            onClick={() => openScore(`rec:${done.take!.id}`)}
            className={cn(TAP_BUTTON_CLASS, PRIMARY, "inline-flex items-center gap-2 font-semibold")}
          >
            <FileMusic className="h-4 w-4" aria-hidden /> See the score
          </button>
        </div>
      )}
      {done?.note && <p className="text-[15px] text-amber-800">{done.note}</p>}
      {err && <p className="text-[15px] text-rose-700">{err}</p>}
    </div>
  );
}
