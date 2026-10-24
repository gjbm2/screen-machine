// Record on a score (owner, 24 Oct: "record should be on score page as well ... so user can record and/or record
// sections"): on every score that has MIDI, yours or the library's. One tap records:
//   Record          the chip records what's chosen at once: the whole piece, or "Record bars 5–8" once bars are chosen.
//   ˅ (its chevron) a sheet: Whole piece | These bars, the bars (From / To, or tap them on the score: the first tap
//                   sets From, the next To), and what happens to the take.
//   Whole piece     Stop keeps the take as a new recording named after the piece (under Mine).
//   These bars      what was played goes in place of those bars, in a NEW copy of the piece; this one stays as it is.
//                   The chosen bars are banded amber on the score (follow.tsx BarBand), and the score turns to them.
// While it records, the score's docked bar (and the music stand) show the red "● 1:23 Stop"; after Stop the new take
// opens in a sheet (Room/PianoRecorder.tsx NewTakePanel: Play it · Score · Keep · Discard).
// The recorder is the app's one (Room/PianoRecorder.tsx useRecorder: one poll for the whole app), so a take already
// running shows at once. show_service: POST /show/api/v1/piano/record {on, piece?, section?} (recorder.py); the
// section goes as the piece's own seconds from the score's bar map (each bar's first onset to the next bar's), with
// the bar numbers for its name. A take started somewhere else (the Room page) is stopped as it is.

import { useCallback, useEffect, useState } from "react";
import { ChevronDown, Circle, Square } from "lucide-react";
import { cn } from "@/lib/utils";
import { Spinner } from "@/pages/Room/bits";
import { ActionSheet } from "@/pages/Room/overlays";
import { NewTakePanel, useRecorder, type Recorder } from "@/pages/Room/PianoRecorder";
import { clock } from "@/pages/Room/RecordButton";
import { RoomThemeStyle, TAP_BUTTON_CLASS } from "@/pages/Room/theme";
import type { PianoRecordAsk, PianoRecording } from "@/pages/Room/types";
import { SECONDARY } from "./bits";
import { Segmented } from "./PlayBar";

/** A new section's length, in bars. */
const SECTION_BARS = 4;
/** A take is this score's when its time agrees with when the score started it (to this many seconds). */
const SAME_TAKE_S = 6;

type Bar = [number, number, number];
export type RecWhat = "whole" | "section";

const WHAT: [RecWhat, string][] = [
  ["whole", "Whole piece"],
  ["section", "These bars"],
];

/** What a score in this tab asked the running take to be (its piece, its bars), and when it started it. A take started
 *  anywhere else has no ask: Stop keeps it as it is. */
let asked: { ask: PianoRecordAsk; at: number; span: string | null } | null = null;

/** The ask of the take running now, if a score started it. */
function askOf(r: Recorder): typeof asked {
  if (!asked || !r.recording) return null;
  return Math.abs(r.elapsed - (Date.now() - asked.at) / 1000) <= SAME_TAKE_S ? asked : null;
}

export interface ScoreTake {
  baseUrl: string;
  r: Recorder;
  /** The recorder is there (show_service has one and has answered). */
  available: boolean;
  what: RecWhat;
  setWhat: (w: RecWhat) => void;
  /** The chosen bars (These bars), first and last, or null. */
  from: number | null;
  to: number | null;
  /** All the score's bar numbers. */
  nums: number[];
  setFrom: (n: number) => void;
  setTo: (n: number) => void;
  /** A tap on a bar on the score: the first sets From, the next To. */
  pick: (n: number) => void;
  /** "bars 5 to 8" / "bar 5", or "". */
  span: string;
  /** Bumped when the bars should come into view (These bars chosen, From changed in the sheet). */
  jump: number;
  /** This score started the take running. */
  mine: boolean;
  /** The running take's own words: "bars 5 to 8" when this score asked for them. */
  runningSpan: string | null;
  start: () => Promise<void>;
  /** Stop; the new take, or null. `sheet` false: the take sheet doesn't open (the stand shows its own line). */
  stop: (sheet?: boolean) => Promise<PianoRecording | null>;
  sheetOpen: boolean;
  setSheetOpen: (o: boolean) => void;
  takeOpen: boolean;
  setTakeOpen: (o: boolean) => void;
  /** The last stop made a copy with the chosen bars (else a take of the piece). */
  madeCopy: boolean;
}

/** The score's recorder: what to record, the bars, Record and Stop (see the top of the file). */
export function useScoreTake({
  baseUrl,
  pieceId,
  bars,
  hereBar,
}: {
  baseUrl: string;
  pieceId: string;
  /** The score's bar map: [bar, t0, t1] in the piece's own seconds (null until it's there). */
  bars: Bar[] | null;
  /** Where a section starts unless chosen: the bar playing, else the first on the page in view. */
  hereBar: number | null;
}): ScoreTake {
  const r = useRecorder(baseUrl);
  const [what, setWhatState] = useState<RecWhat>("whole");
  const [from, setFromState] = useState<number | null>(null);
  const [to, setToState] = useState<number | null>(null);
  const [next, setNext] = useState<"from" | "to">("from");
  const [jump, setJump] = useState(0);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [takeOpen, setTakeOpen] = useState(false);
  const [madeCopy, setMadeCopy] = useState(false);

  // the section: from the bar here, SECTION_BARS on (the user can change both)
  const nums = bars?.map((b) => b[0]) ?? [];
  const first = from ?? (hereBar !== null && nums.includes(hereBar) ? hereBar : (nums[0] ?? null));
  const firstIdx = first !== null ? nums.indexOf(first) : -1;
  const last = to !== null && first !== null && to >= first ? to : firstIdx >= 0 ? nums[Math.min(nums.length - 1, firstIdx + SECTION_BARS - 1)] : null;
  const fromBar = bars?.find((b) => b[0] === first) ?? null;
  const toBar = bars?.find((b) => b[0] === last) ?? null;
  const span = first !== null && last !== null ? (first === last ? `bar ${first}` : `bars ${first} to ${last}`) : "";

  const setWhat = useCallback((w: RecWhat) => {
    setWhatState(w);
    if (w === "section") {
      setNext("from");
      setJump((n) => n + 1);
    }
  }, []);
  const setFrom = (n: number) => {
    setFromState(n);
    if (to !== null && to < n) setToState(null);
    setJump((j) => j + 1);
  };
  const setTo = (n: number) => setToState(n);
  const pick = (n: number) => {
    setWhatState("section");
    if (next === "from" || first === null || n < first) {
      setFromState(n);
      setToState(null);
      setNext("to");
    } else {
      setToState(n);
      setNext("from");
    }
  };

  const own = askOf(r);
  const mine = !!own && own.ask.piece === pieceId;

  // a take that ends (here or anywhere) forgets its ask
  useEffect(() => {
    if (!r.recording && r.ready) asked = null;
  }, [r.recording, r.ready]);

  const start = async () => {
    const section = what === "section" && fromBar && toBar ? { from_s: fromBar[1], to_s: toBar[2], from_bar: fromBar[0], to_bar: toBar[0] } : null;
    const ask: PianoRecordAsk = section ? { piece: pieceId, section } : { piece: pieceId };
    setTakeOpen(false);
    const ok = await r.start();
    asked = ok ? { ask, at: Date.now(), span: section ? span : null } : null;
  };
  const stop = async (sheet = true) => {
    const a = askOf(r);
    const t = await r.stop(a?.ask);
    asked = null;
    setMadeCopy(!!a?.ask.section);
    if (t && sheet) setTakeOpen(true);
    return t;
  };

  return {
    baseUrl,
    r,
    available: !r.missing && r.ready,
    what,
    setWhat,
    from: what === "section" ? first : null,
    to: what === "section" ? last : null,
    nums,
    setFrom,
    setTo,
    pick,
    span,
    jump,
    mine,
    runningSpan: own?.span ?? null,
    start,
    stop,
    sheetOpen,
    setSheetOpen,
    takeOpen,
    setTakeOpen,
    madeCopy,
  };
}

const CHIP_BASE =
  "inline-flex min-h-10 items-center gap-1.5 border border-stone-200 bg-white text-[15px] font-medium text-stone-700 hover:bg-stone-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60";

/** Record in one tap (what's chosen), and its chevron for the choice. Nothing while it records (the bar has Stop). */
export function RecordChip({ take }: { take: ScoreTake }) {
  if (!take.available || take.r.recording) return null;
  const busy = take.r.busy === "record";
  const words = take.what === "section" && take.span ? `Record ${take.span.replace(" to ", "–")}` : "Record";
  return (
    <span className="inline-flex" data-testid="score-record-chip">
      <button
        type="button"
        onClick={() => void take.start()}
        disabled={busy}
        className={cn(CHIP_BASE, "rounded-l-full border-r-0 pl-3.5 pr-2.5")}
        data-testid="score-record"
      >
        {busy ? <Spinner /> : <Circle className="h-3.5 w-3.5 fill-rose-700 text-rose-700" aria-hidden />} {words}
      </button>
      <button
        type="button"
        onClick={() => take.setSheetOpen(true)}
        aria-label="What to record: the whole piece or some bars"
        title="Whole piece or these bars"
        className={cn(CHIP_BASE, "rounded-r-full border-l-stone-200 px-2")}
        data-testid="score-record-choose"
      >
        <ChevronDown className="h-4 w-4" aria-hidden />
      </button>
    </span>
  );
}

/** The red "● 1:23 Stop" while the piano records (whoever started it). */
export function RecordTimer({ take, onStop, className }: { take: ScoreTake; onStop?: () => void; className?: string }) {
  const r = take.r;
  if (!r.recording) return null;
  return (
    <button
      type="button"
      onClick={() => (onStop ? onStop() : void take.stop())}
      disabled={r.busy === "record"}
      aria-label={`Recording, ${clock(r.elapsed)}. Stop`}
      className={cn(
        "inline-flex h-10 shrink-0 items-center gap-2 rounded-full border border-rose-300 bg-rose-50 px-3 text-[15px] font-semibold text-rose-900 hover:bg-rose-100 disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        className,
      )}
      data-testid="score-record-timer"
    >
      {r.busy === "record" ? <Spinner /> : <span aria-hidden className="h-2.5 w-2.5 rounded-full bg-rose-600 motion-safe:animate-pulse" />}
      <span className="font-mono tabular-nums">{clock(r.elapsed)}</span>
      <span className="inline-flex items-center gap-1 border-l border-rose-300 pl-2">
        <Square className="h-3.5 w-3.5 fill-current" aria-hidden /> Stop
      </span>
    </button>
  );
}

/** "Recording · bars 5 to 8 · 12 notes" (the docked bar's first line while it records). */
export function recordingWords(take: ScoreTake): string {
  const n = take.r.notes;
  const notes = `${n} note${n === 1 ? "" : "s"}`;
  if (!take.mine) return `Recording · ${notes}`;
  return take.runningSpan ? `Recording ${take.runningSpan} · ${notes}` : `Recording this piece · ${notes}`;
}

/** The chevron's sheet: Whole piece | These bars, the bars, the words, Record. */
export function RecordSheet({ take, editable }: { take: ScoreTake; editable: boolean }) {
  const r = take.r;
  const recording = r.recording;
  const locked = recording || r.busy === "record";
  const select = "min-h-10 rounded-full border border-stone-200 bg-white px-3 text-[15px] font-medium text-stone-700";
  const sectionReady = take.from !== null && take.to !== null;
  const words =
    take.what === "section"
      ? `Play ${take.span || "the bars"}, then Stop. What you play takes their place in a new copy of this piece; this one stays as it is.`
      : editable
        ? "Play the piece, then Stop. It's kept as a new recording of this piece."
        : "Play it, then Stop. Your take is kept under Mine, named after this piece.";
  return (
    <>
      {take.sheetOpen && <RoomThemeStyle />}
      <ActionSheet open={take.sheetOpen} onOpenChange={take.setSheetOpen} title="Record on the piano" testId="score-record-sheet">
        <div className="space-y-4 pb-1">
          <Segmented label="What to record" value={take.what} options={WHAT} onChange={take.setWhat} disabled={locked} />
          {take.what === "section" &&
            (take.nums.length > 0 ? (
              <div className="space-y-2">
                <div className="flex flex-wrap items-center gap-2 text-[15px]">
                  <span>Bars</span>
                  <select
                    aria-label="From bar"
                    value={take.from ?? ""}
                    disabled={locked}
                    onChange={(e) => take.setFrom(Number(e.target.value))}
                    className={select}
                  >
                    {take.nums.map((n) => (
                      <option key={n} value={n}>
                        {n}
                      </option>
                    ))}
                  </select>
                  <span>to</span>
                  <select aria-label="To bar" value={take.to ?? ""} disabled={locked} onChange={(e) => take.setTo(Number(e.target.value))} className={select}>
                    {take.nums
                      .filter((n) => take.from === null || n >= take.from)
                      .map((n) => (
                        <option key={n} value={n}>
                          {n}
                        </option>
                      ))}
                  </select>
                </div>
                <p className="text-sm text-muted-foreground">Or tap the bars on the score: the first tap is where it starts, the next where it ends.</p>
              </div>
            ) : (
              <p className="text-[15px] text-muted-foreground">Finding the bars…</p>
            ))}
          <p className="text-[15px] leading-snug text-stone-700">{words}</p>
          {recording && !take.mine && (
            <p className="text-sm text-muted-foreground">The piano is already recording (started somewhere else): Stop keeps that take as it is.</p>
          )}
          <div className="flex flex-wrap gap-2">
            {recording ? (
              <RecordTimer take={take} onStop={() => void take.stop().then(() => take.setSheetOpen(false))} className="h-11" />
            ) : (
              <button
                type="button"
                disabled={r.busy === "record" || (take.what === "section" && !sectionReady)}
                onClick={() => void take.start().then(() => take.setSheetOpen(false))}
                className={cn(TAP_BUTTON_CLASS, "inline-flex items-center gap-2 bg-rose-700 font-semibold text-white hover:bg-rose-800 disabled:opacity-60")}
              >
                {r.busy === "record" ? <Spinner /> : <Circle className="h-4 w-4 fill-current" aria-hidden />}
                {take.what === "section" && take.span ? `Record ${take.span}` : "Record"}
              </button>
            )}
            <button type="button" onClick={() => take.setSheetOpen(false)} className={cn(TAP_BUTTON_CLASS, SECONDARY)}>
              {take.what === "section" && !recording ? "Choose on the score" : "Done"}
            </button>
          </div>
          {r.err && <p className="text-[15px] text-rose-700">{r.err}</p>}
        </div>
      </ActionSheet>
    </>
  );
}

/** The take just stopped, in a sheet: Play it · Score · Keep · Discard. */
export function TakeSheet({ take }: { take: ScoreTake }) {
  const r = take.r;
  const shown = take.takeOpen && !!r.fresh;
  useEffect(() => {
    if (!r.fresh && take.takeOpen) take.setTakeOpen(false);
  }, [r.fresh, take]);
  return (
    <>
      {shown && <RoomThemeStyle />}
      <ActionSheet
        open={shown}
        onOpenChange={(o) => !o && take.setTakeOpen(false)}
        title={take.madeCopy ? "New copy with your bars" : "New take"}
        description={r.fresh ? `${clock(r.fresh.duration_s)} · ${r.fresh.notes} note${r.fresh.notes === 1 ? "" : "s"}` : undefined}
        testId="score-take-sheet"
      >
        {r.note && <p className="mb-2 text-[15px] text-amber-800">{r.note}</p>}
        <NewTakePanel baseUrl={take.baseUrl} heading={false} onDone={() => take.setTakeOpen(false)} className="border-0 bg-transparent p-0 pb-1" />
      </ActionSheet>
    </>
  );
}
