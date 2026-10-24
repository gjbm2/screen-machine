// The Piano card (owner, 22 Oct): record what's played on the piano, keep (and name) or discard the take, and play
// takes back, its MIDI to the screens and its sound in step, with the same virtual piano sound and output as Test
// music (usePianoSound.ts; the chips are SoundChoice below, shared with TestShow.tsx). Everything goes through
// room_service's /show pass-through to show_service:
//   GET    /show/api/v1/piano/recordings              the recorder ("off" | "recording", how long, notes) and the takes
//   POST   /show/api/v1/piano/record {on}              Record / Stop; a stop saves the take ("last"), unless nothing was played
//   PATCH  /show/api/v1/piano/recordings/<id> {title, composer?}  Keep (with a new name) and Edit details
//   DELETE /show/api/v1/piano/recordings/<id>          Discard and Delete
//   GET    /show/api/v1/piano/recordings/<id>.mid      Download (the file's name is the title)
//   POST   /show/api/v1/test {source: "piano", piece: "rec:<id>", instrument, output, once: true}   Play (as the test
//          piano, so the screens, lamps and beat follow it); DELETE /show/api/v1/test/piano stops it.
//   Score opens the take's score on the Music page (/music?score=rec:<id>): "See the score" on the take just recorded
//   (owner, 24 Oct: "after a recording from /room, link to score produced should appear"), and a score button on every
//   take in the list. Record / Stop and its line are RecordButton.tsx's, shared with Record on a score.
// Polled every 1 s while recording or while a take plays, 5 s otherwise, only while the page is visible. While
// recording, the dot flashes on each key (WS /show/v1/show, music.note, as PianoRow.tsx). The whole card stays hidden
// until show_service has the recorder. Words only: no ids, files or numbers a person doesn't need.

import { useCallback, useEffect, useRef, useState } from "react";
import { Check, ChevronDown, Download, FileMusic, Pencil, Play, Square, Trash2, X } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { explainError, RoomApiError, type RoomApi } from "./api";
import { Pill, Spinner, Unroll } from "./bits";
import { clock, RecordButton, RecordingLine, scoreLink, useKeyFlash } from "./RecordButton";
import { CARD_CLASS, TAP_BUTTON_CLASS, THEME_CLASS } from "./theme";
import type { PianoRecording, PianoRecordingsView, ShowTestStartOpts, ShowTestState, ShowTestView } from "./types";
import { SpeedNudges } from "./SpeedNudges";
import { usePianoSound, type PianoSound } from "./usePianoSound";
import type { NewLogEntry } from "./useRoomConnection";

const POLL_FAST_MS = 1000;
const POLL_SLOW_MS = 5000;
/** Takes shown before "Show all". */
const SHOW_FIRST = 8;

const CHIP = "min-h-11 rounded-xl border px-3 text-base font-medium transition-colors disabled:cursor-default";
const CHIP_ON = "border-teal-300 bg-teal-50 text-teal-900";
const CHIP_OFF = "border-stone-200 bg-white hover:bg-stone-50";
const SECONDARY = "border border-stone-300 bg-white hover:bg-stone-50";

/** The test piano's states that count as playing (or about to). */
const PIANO_BUSY = ["preparing", "starting", "playing", "stopping"];
const REC = "rec:";
const recPiece = (id: string) => `${REC}${id}`;

/** "Today 22:15", "Yesterday 09:03", "Tue 14 Oct, 18:40" (local time). */
function when(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const now = new Date();
  const hm = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (d.toDateString() === now.toDateString()) return `Today ${hm}`;
  if (d.toDateString() === yesterday.toDateString()) return `Yesterday ${hm}`;
  const day = d.toLocaleDateString("en-GB", {
    weekday: "short",
    day: "numeric",
    month: "short",
    ...(d.getFullYear() !== now.getFullYear() ? { year: "numeric" as const } : {}),
  });
  return `${day}, ${hm}`;
}

const plural = (n: number, one: string) => `${n} ${one}${n === 1 ? "" : "s"}`;

/** An error in the card's words (explainError's are about screens for 409 / 400). */
function words(e: unknown): string {
  if (e instanceof RoomApiError) {
    if (e.status === 404) return "That take isn't there any more.";
    if (e.status === 502 || e.status === 503) return "The music side isn't answering just now. Try again in a moment.";
    if ((e.status === 400 || e.status === 409) && e.detail) return e.detail;
    if (e.status === 409) return "It was refused just now. Try again in a moment.";
  }
  return explainError(e);
}

/**
 * Poll a view: every 1 s while `fast` says so, else every 5 s, only while the page is visible (TestShow's usePolled,
 * plus: `setView` from an action reschedules at once, and a poll that left before it is dropped).
 */
function usePoll<T>(load: (s: AbortSignal) => Promise<T | null>, fast: (v: T) => boolean) {
  const [state, setState] = useState<{ view: T | null; at: number }>({ view: null, at: 0 });
  const [missing, setMissing] = useState(false);
  const gen = useRef(0);
  const quick = useRef(false);
  const reschedule = useRef<() => void>(() => undefined);
  useEffect(() => {
    let stop = false;
    let inflight = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const ac = new AbortController();
    const next = () => {
      clearTimeout(timer);
      if (!stop && !inflight) timer = setTimeout(poll, quick.current ? POLL_FAST_MS : POLL_SLOW_MS);
    };
    const poll = async () => {
      inflight = true;
      if (document.visibilityState === "visible") {
        const g = gen.current;
        try {
          const v = await load(ac.signal);
          if (stop) return;
          if (g === gen.current) {
            if (v === null) setMissing(true);
            else {
              setMissing(false);
              quick.current = fast(v);
              setState({ view: v, at: Date.now() });
            }
          }
        } catch {
          // the page's banner reports a lost connection; keep the last view
        }
      }
      inflight = false;
      next();
    };
    reschedule.current = next;
    void poll();
    return () => {
      stop = true;
      ac.abort();
      clearTimeout(timer);
      reschedule.current = () => undefined;
    };
  }, [load, fast]);
  const setView = useCallback(
    (v: T) => {
      gen.current += 1;
      quick.current = fast(v);
      setState({ view: v, at: Date.now() });
      reschedule.current();
    },
    [fast],
  );
  return { view: state.view, at: state.at, setView, missing };
}

const recordingFast = (v: PianoRecordingsView) => v.recording?.state === "recording";
const testFast = (v: ShowTestView) => PIANO_BUSY.includes(v.sources?.piano?.state ?? "off");

/** The piano's Sound, from show_service's list (labels exactly as it sends them). Nothing without it. Where it plays is
 *  its own control on the page (Play on: PlayOn.tsx), so the choice shows once. */
export function SoundChoice({
  sound,
  disabled,
  onInstrument,
}: {
  sound: PianoSound;
  disabled?: boolean;
  onInstrument?: (id: string) => void;
}) {
  if (!sound.available) return null;
  const group = (
    label: string,
    items: { id: string; label: string }[],
    value: string | null,
    pick: (id: string) => void,
  ) => (
    <div>
      <span className="text-base font-medium">{label}</span>
      <div role="radiogroup" aria-label={label} className="mt-2 grid grid-cols-2 gap-2">
        {items.map((it) => {
          const sel = it.id === value;
          return (
            <button
              key={it.id}
              type="button"
              role="radio"
              aria-checked={sel}
              disabled={disabled || sel}
              onClick={() => pick(it.id)}
              className={cn(CHIP, "py-2 text-left leading-snug", sel ? CHIP_ON : CHIP_OFF, !sel && disabled && "opacity-60")}
            >
              {it.label}
            </button>
          );
        })}
      </div>
    </div>
  );
  return group("Sound", sound.instruments, sound.instrument, onInstrument ?? sound.setInstrument);
}

/** The test piano's progress in words: getting the sound ready (with how far), finished, or why it didn't play. */
export function PlayStatus({ state, sound }: { state: ShowTestState | null | undefined; sound?: PianoSound }) {
  if (!state) return null;
  if (state.state === "preparing") {
    const p = state.progress;
    const pct = typeof p === "number" && Number.isFinite(p) ? Math.round(Math.min(1, Math.max(0, p)) * 100) : null;
    return (
      <div className="space-y-1.5" role="status">
        <p className="flex items-center gap-2 text-base">
          <Spinner /> Getting ready…{pct ? ` ${pct}%` : ""}
        </p>
        {pct !== null && (
          <div className="h-1.5 overflow-hidden rounded-full bg-stone-100" aria-hidden>
            <div className="h-full rounded-full bg-teal-600 transition-[width] duration-500" style={{ width: `${pct}%` }} />
          </div>
        )}
      </div>
    );
  }
  if (state.state === "finished") return <p className="text-base text-muted-foreground">Finished.</p>;
  if (state.state === "failed") {
    const inst = sound?.instruments.find((i) => i.id === state.instrument)?.label;
    return (
      <div>
        <p className="text-base text-amber-800">
          It didn't play{inst ? ` with ${inst}` : ""}. Try again, or pick another sound.
        </p>
        {state.error && (
          <Unroll title={<span className="text-sm">Details</span>}>
            <p className="whitespace-pre-wrap break-words pb-1 text-sm text-muted-foreground">{state.error}</p>
          </Unroll>
        )}
      </div>
    );
  }
  return null;
}

type Confirm = { id: string; title: string; discard: boolean };

export function PianoCard({
  api,
  connected,
  addLog,
  reportError,
  bare = false,
  onSummary,
}: {
  api: RoomApi;
  connected: boolean;
  addLog: (e: NewLogEntry) => void;
  reportError: (e: unknown) => void;
  /** Inside a fold that already says "Piano": no heading of its own (its pill, when there is one, stays). */
  bare?: boolean;
  /** Told the card in one line ("Record · last take Today 14:05", "Recording · 1:23"), or null while the recorder
   *  isn't there (the card is hidden), e.g. for a fold's closed summary. */
  onSummary?: (line: string | null) => void;
}) {
  const loadRecs = useCallback((s: AbortSignal) => api.pianoRecordings(s), [api]);
  const loadTest = useCallback((s: AbortSignal) => api.showTest(s), [api]);
  const recs = usePoll<PianoRecordingsView>(loadRecs, recordingFast);
  const test = usePoll<ShowTestView>(loadTest, testFast);
  const sound = usePianoSound(test.view);

  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [fresh, setFresh] = useState<PianoRecording | null>(null);
  const [draft, setDraft] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [renameText, setRenameText] = useState("");
  const [renameComposer, setRenameComposer] = useState("");
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  const [all, setAll] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  const rec = recs.view?.recording;
  const recording = rec?.state === "recording";
  const flash = useKeyFlash(api.showEventsUrl().replace(/\/v1\/events$/, "/v1/show"), recording);
  const navigate = useNavigate();

  // the elapsed time ticks between polls
  useEffect(() => {
    if (!recording) return;
    setNow(Date.now());
    const id = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(id);
  }, [recording]);

  const run = useCallback(
    async (key: string, what: string | null, fn: () => Promise<void>) => {
      setBusy(key);
      setErr(null);
      try {
        await fn();
        if (what) addLog({ type: "fixture", level: "info", summary: `Piano: ${what}` });
      } catch (e) {
        reportError(e);
        setErr(words(e));
      } finally {
        setBusy(null);
      }
    },
    [addLog, reportError],
  );

  const refreshRecs = async () => {
    const v = await api.pianoRecordings();
    if (v) recs.setView(v);
  };
  const refreshTest = async () => {
    const v = await api.showTest();
    if (v) test.setView(v);
  };

  // The card in one line, for the fold it sits in (from the readings above: nothing new is fetched).
  const sumRef = useRef(onSummary);
  sumRef.current = onSummary;
  const summaryLine = (() => {
    if (recs.missing) return null;
    if (!recs.view) return undefined;
    if (recording) return `Recording · ${clock(rec?.since_s ?? rec?.duration_s ?? 0)}`;
    const p = test.view?.sources?.piano;
    const pid = p?.piece?.startsWith(REC) ? p.piece.slice(REC.length) : null;
    const takes = recs.view.recordings ?? [];
    if (pid && PIANO_BUSY.includes(p?.state ?? "off")) return `Playing · ${takes.find((r) => r.id === pid)?.title ?? p?.label ?? "a take"}`;
    const newest = takes.reduce<PianoRecording | null>((a, r) => (!a || r.recorded_at > a.recorded_at ? r : a), null);
    const at = newest ? when(newest.recorded_at) : "";
    return newest ? `Record · last take ${at || newest.title}` : "Record · no takes yet";
  })();
  useEffect(() => {
    if (summaryLine !== undefined) sumRef.current?.(summaryLine);
  }, [summaryLine]);

  if (recs.missing || !recs.view) return null;

  const list = recs.view.recordings ?? [];
  const piano = test.view?.sources?.piano ?? null;
  const pianoBusy = PIANO_BUSY.includes(piano?.state ?? "off");
  const playingId = piano?.piece?.startsWith(REC) ? piano.piece.slice(REC.length) : null;
  const canPlay = !!test.view && !test.missing;
  const playingTitle = playingId && pianoBusy ? (list.find((r) => r.id === playingId)?.title ?? piano?.label ?? null) : null;
  const elapsed = recording ? (rec?.since_s ?? rec?.duration_s ?? 0) + (recs.at ? Math.max(0, now - recs.at) / 1000 : 0) : 0;

  // ── Record / Stop ──
  const startRecording = () =>
    run("record", "recording", async () => {
      setNote(null);
      setFresh(null);
      recs.setView(await api.pianoRecord(true));
    });
  const stopRecording = () =>
    run("record", "stopped recording", async () => {
      const v = await api.pianoRecord(false);
      recs.setView(v);
      if (v.last) {
        setFresh(v.last);
        setDraft(v.last.title);
        setOpen(null);
        if (v.error) setNote(v.error);
      } else setNote(v.error ?? "Nothing was played, so there's no take to keep.");
    });

  // ── The new take ──
  const keep = (t: PianoRecording) =>
    run("keep", `kept "${draft.trim() || t.title}"`, async () => {
      const title = draft.trim();
      try {
        if (title && title !== t.title) await api.pianoRename(t.id, title);
      } catch (e) {
        if (e instanceof RoomApiError && e.status === 404) setFresh(null); // deleted meanwhile (another phone)
        throw e;
      }
      setFresh(null);
      await refreshRecs();
    });

  // ── The new take's score: its name kept first (as Keep), then the score page ──
  const seeScore = (t: PianoRecording) =>
    run("score", null, async () => {
      const title = draft.trim();
      if (title && title !== t.title) await api.pianoRename(t.id, title);
      setFresh(null);
      navigate(scoreLink(recPiece(t.id)));
    });

  // ── Play a take (as the test piano, play-once): whatever else plays stops first ──
  const stopOthers = async () => {
    for (const src of ["audio", "piano"] as const) {
      if (PIANO_BUSY.includes(test.view?.sources?.[src]?.state ?? "off")) await api.showTestStop(src);
    }
    // the renderer's own test piano and the fake band would play over it (as TestShow's stopOthers)
    const [tp, band] = await Promise.all([api.testPiano().catch(() => null), api.testShow().catch(() => null)]);
    if (tp?.running) await api.testPianoSend({ on: false });
    if (band?.running) await api.testShowSend({ action: "stop" });
  };
  const play = (r: PianoRecording, o?: ShowTestStartOpts) =>
    run(`play:${r.id}`, `playing "${r.title}"`, async () => {
      await stopOthers();
      await api.showTestStart("piano", recPiece(r.id), { ...sound.opts, ...o, once: true });
      await refreshTest();
    });
  const stopPlaying = (r: PianoRecording) =>
    run(`play:${r.id}`, "stopped playing", async () => {
      await api.showTestStop("piano");
      await refreshTest();
    });
  // a new sound or output while a take plays: it starts again with it (as a new piece does in Test music)
  const playingRec = playingId && pianoBusy ? (list.find((r) => r.id === playingId) ?? null) : null;
  const changeSound = (o: ShowTestStartOpts) => {
    if (o.instrument) sound.setInstrument(o.instrument);
    if (o.output) sound.setOutput(o.output);
    if (playingRec) void play(playingRec, o);
  };
  // a new speed while a take plays: it carries on from where it was, at the new speed
  const changeSpeed = (x: number) => {
    sound.setSpeed?.(x);
    if (playingRec) void play(playingRec, { speed: x, start_s: piano?.pos_s ?? 0 });
  };

  // ── Rename, Delete / Discard ──
  const saveRename = (r: PianoRecording) => {
    const title = renameText.trim();
    const composer = renameComposer.trim();
    if (!title || (title === r.title && composer === (r.composer ?? ""))) {
      setRenaming(null);
      return;
    }
    void run(`rename:${r.id}`, `saved "${title}"`, async () => {
      await api.pianoEdit(r.id, { title, composer: composer || null });
      setRenaming(null);
      await refreshRecs();
    });
  };
  const remove = (c: Confirm) =>
    run(c.discard ? "discard" : `delete:${c.id}`, `${c.discard ? "discarded" : "deleted"} "${c.title}"`, async () => {
      if (playingId === c.id && pianoBusy) await api.showTestStop("piano").catch(() => undefined);
      try {
        await api.pianoDelete(c.id);
      } catch (e) {
        if (!(e instanceof RoomApiError && e.status === 404)) throw e; // already gone is what was wanted
      }
      if (fresh?.id === c.id) setFresh(null);
      if (open === c.id) setOpen(null);
      await refreshRecs();
    });

  const rows = list.filter((r) => r.id !== fresh?.id);
  const shown = all ? rows : rows.slice(0, SHOW_FIRST);

  return (
    <section aria-labelledby="room-piano">
      <div className={cn("mb-2 flex items-center justify-between gap-2", bare && !recording && !playingTitle && "hidden")}>
        {bare ? (
          <span id="room-piano" className="sr-only">
            Piano
          </span>
        ) : (
          <h2 id="room-piano" className="text-lg font-semibold text-stone-800">
            Piano
          </h2>
        )}
        {recording ? (
          <Pill tone="bad" pulse>
            Recording
          </Pill>
        ) : playingTitle ? (
          <Pill tone="live" pulse>
            {piano?.state === "preparing" ? "Getting ready" : "Playing"} · {playingTitle}
          </Pill>
        ) : null}
      </div>
      <div className={cn(CARD_CLASS, "space-y-4 px-3 py-3 sm:px-4")}>
        {/* Record / Stop: the card's main action */}
        <div className="flex flex-wrap items-center gap-3">
          <RecordButton
            recording={recording}
            busy={busy === "record"}
            disabled={!recording && !connected}
            elapsed={elapsed}
            onClick={() => void (recording ? stopRecording() : startRecording())}
          />
          {recording ? (
            <RecordingLine notes={rec?.notes ?? 0} flash={flash} />
          ) : (
            <p className="min-w-0 flex-1 text-sm text-muted-foreground">Record what's played on the piano, then keep it or play it back.</p>
          )}
        </div>

        {note && !recording && <p className="text-base text-muted-foreground">{note}</p>}

        {/* The take just recorded: name it, keep it or discard it */}
        {fresh && (
          <div className="space-y-3 rounded-xl border border-teal-300 bg-teal-50/70 p-3" data-testid="piano-new-take">
            <p className="text-base font-semibold">
              New take · {clock(fresh.duration_s)} · {plural(fresh.notes, "note")}
            </p>
            <label className="block">
              <span className="text-sm text-muted-foreground">Name</span>
              <Input
                value={draft}
                maxLength={120}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") void keep(fresh);
                }}
                className="mt-1 h-11 rounded-xl bg-white text-base md:text-base"
              />
            </label>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                disabled={!!busy}
                onClick={() => void seeScore(fresh)}
                className={cn(TAP_BUTTON_CLASS, "inline-flex items-center gap-2 bg-primary font-semibold text-primary-foreground hover:bg-primary/90 disabled:opacity-60")}
              >
                {busy === "score" ? <Spinner /> : <FileMusic className="h-4 w-4" aria-hidden />} See the score
              </button>
              <button
                type="button"
                disabled={!!busy}
                onClick={() => void keep(fresh)}
                className={cn(TAP_BUTTON_CLASS, SECONDARY, "inline-flex items-center gap-2 font-semibold disabled:opacity-60")}
              >
                {busy === "keep" ? <Spinner /> : <Check className="h-4 w-4" aria-hidden />} Keep
              </button>
              <button
                type="button"
                disabled={!!busy}
                onClick={() => setConfirm({ id: fresh.id, title: draft.trim() || fresh.title, discard: true })}
                className={cn(TAP_BUTTON_CLASS, SECONDARY, "inline-flex items-center gap-2 text-rose-800 disabled:opacity-60")}
              >
                {busy === "discard" ? <Spinner /> : <Trash2 className="h-4 w-4" aria-hidden />} Discard
              </button>
            </div>
          </div>
        )}

        {/* The takes, folded (owner, 24 Oct: Record first, the takes list inside, folded) */}
        {rows.length > 0 || (canPlay && list.length > 0) ? (
        <Unroll title="Takes" hint={plural(rows.length, "take")} testId="piano-takes" contentClassName="space-y-4">
        {canPlay && list.length > 0 && (
          <SoundChoice
            sound={sound}
            disabled={!!busy && busy.startsWith("play:")}
            onInstrument={(id) => changeSound({ instrument: id })}
          />
        )}
        {canPlay && list.length > 0 && sound.available && (
          <SpeedNudges speed={sound.speed ?? 1} onSpeed={changeSpeed} disabled={!!busy && busy.startsWith("play:")} />
        )}

        {/* The takes, newest first: tap one for Play, Rename, Download, Delete */}
        {rows.length > 0 ? (
          <ul className="space-y-2" aria-label="Recordings">
            {shown.map((r) => {
              const isOpen = open === r.id;
              const playing = playingId === r.id && pianoBusy;
              const status = playingId === r.id ? piano : null;
              return (
                <li key={r.id} className={cn("rounded-xl border", playing ? "border-teal-300 bg-teal-50/40" : "border-stone-200")}>
                  <div className="flex items-center">
                  <button
                    type="button"
                    aria-expanded={isOpen}
                    onClick={() => {
                      setOpen(isOpen ? null : r.id);
                      setRenaming(null);
                    }}
                    className="flex min-h-11 min-w-0 flex-1 items-center gap-3 rounded-xl px-3 py-2 text-left hover:bg-stone-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-base font-medium">{r.title}</span>
                      <span className="block text-sm text-muted-foreground">
                        {r.composer ? `${r.composer} · ` : ""}
                        {clock(r.duration_s)}
                        {when(r.recorded_at) ? ` · ${when(r.recorded_at)}` : ""}
                        {playing ? (status?.state === "preparing" ? " · getting ready" : " · playing") : ""}
                      </span>
                    </span>
                    <ChevronDown
                      aria-hidden
                      className={cn("h-4 w-4 shrink-0 text-muted-foreground transition-transform motion-reduce:transition-none", isOpen && "rotate-180")}
                    />
                  </button>
                  <Link
                    to={scoreLink(recPiece(r.id))}
                    aria-label={`See the score of ${r.title}`}
                    title="See the score"
                    className="mr-1 grid h-11 w-11 shrink-0 place-items-center rounded-full text-stone-600 hover:bg-stone-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <FileMusic className="h-5 w-5" aria-hidden />
                  </Link>
                  </div>
                  {isOpen && (
                    <div className="space-y-3 border-t border-stone-200 px-3 py-3">
                      {renaming === r.id ? (
                        <div
                          className="space-y-2"
                          onKeyDown={(e) => {
                            if (e.key === "Enter") saveRename(r);
                            if (e.key === "Escape") setRenaming(null);
                          }}
                        >
                          <Input
                            aria-label="Title"
                            placeholder="Title"
                            autoFocus
                            value={renameText}
                            maxLength={120}
                            onChange={(e) => setRenameText(e.target.value)}
                            className="h-11 rounded-xl bg-white text-base md:text-base"
                          />
                          <Input
                            aria-label="Composer"
                            placeholder="Composer (whose piece it is)"
                            value={renameComposer}
                            maxLength={120}
                            onChange={(e) => setRenameComposer(e.target.value)}
                            className="h-11 rounded-xl bg-white text-base md:text-base"
                          />
                          <div className="flex gap-2">
                            <button
                              type="button"
                              disabled={!!busy}
                              onClick={() => saveRename(r)}
                              className={cn(TAP_BUTTON_CLASS, "inline-flex items-center gap-2 bg-primary font-semibold text-primary-foreground hover:bg-primary/90 disabled:opacity-60")}
                            >
                              {busy === `rename:${r.id}` ? <Spinner /> : <Check className="h-4 w-4" aria-hidden />} Save
                            </button>
                            <button
                              type="button"
                              onClick={() => setRenaming(null)}
                              className={cn(TAP_BUTTON_CLASS, SECONDARY, "inline-flex items-center gap-2 px-4")}
                            >
                              <X className="h-4 w-4" aria-hidden /> Cancel
                            </button>
                          </div>
                        </div>
                      ) : (
                        <div className="flex flex-wrap gap-2">
                          {canPlay && (
                            <button
                              type="button"
                              disabled={!connected || !!busy}
                              onClick={() => void (playing ? stopPlaying(r) : play(r))}
                              className={cn(
                                TAP_BUTTON_CLASS,
                                "inline-flex items-center gap-2 font-semibold disabled:opacity-60",
                                playing ? SECONDARY : "bg-primary text-primary-foreground hover:bg-primary/90",
                              )}
                            >
                              {busy === `play:${r.id}` ? (
                                <Spinner />
                              ) : playing ? (
                                <Square className="h-4 w-4" aria-hidden />
                              ) : (
                                <Play className="h-4 w-4" aria-hidden />
                              )}
                              {playing ? "Stop" : "Play"}
                            </button>
                          )}
                          <button
                            type="button"
                            disabled={!!busy}
                            onClick={() => {
                              setRenaming(r.id);
                              setRenameText(r.title);
                              setRenameComposer(r.composer ?? "");
                            }}
                            className={cn(TAP_BUTTON_CLASS, SECONDARY, "inline-flex items-center gap-2 px-4 disabled:opacity-60")}
                          >
                            <Pencil className="h-4 w-4" aria-hidden /> Edit details
                          </button>
                          <Link
                            to={scoreLink(recPiece(r.id))}
                            className={cn(TAP_BUTTON_CLASS, SECONDARY, "inline-flex items-center gap-2 px-4")}
                          >
                            <FileMusic className="h-4 w-4" aria-hidden /> See the score
                          </Link>
                          <a
                            href={api.pianoRecordingUrl(r.id)}
                            download={`${r.title}.mid`}
                            className={cn(TAP_BUTTON_CLASS, SECONDARY, "inline-flex items-center gap-2 px-4")}
                          >
                            <Download className="h-4 w-4" aria-hidden /> Download
                          </a>
                          <button
                            type="button"
                            disabled={!!busy}
                            onClick={() => setConfirm({ id: r.id, title: r.title, discard: false })}
                            className={cn(TAP_BUTTON_CLASS, SECONDARY, "inline-flex items-center gap-2 px-4 text-rose-800 disabled:opacity-60")}
                          >
                            {busy === `delete:${r.id}` ? <Spinner /> : <Trash2 className="h-4 w-4" aria-hidden />} Delete
                          </button>
                        </div>
                      )}
                      {status && <PlayStatus state={status} sound={sound} />}
                    </div>
                  )}
                  {!isOpen && status && ["preparing", "failed"].includes(status.state) && (
                    <div className="px-3 pb-2">
                      <PlayStatus state={status} sound={sound} />
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        ) : null}
        {!all && rows.length > SHOW_FIRST && (
          <button
            type="button"
            onClick={() => setAll(true)}
            className={cn(TAP_BUTTON_CLASS, SECONDARY, "w-full font-medium")}
          >
            Show all {rows.length}
          </button>
        )}
        </Unroll>
        ) : (
          !fresh && !recording && <p className="text-base text-muted-foreground">No takes yet.</p>
        )}

        {err && <p className="text-sm text-rose-700">{err}</p>}
      </div>

      <AlertDialog open={!!confirm} onOpenChange={(o) => !o && setConfirm(null)}>
        <AlertDialogContent className={cn(THEME_CLASS, "rounded-2xl")}>
          <AlertDialogHeader>
            <AlertDialogTitle>{confirm?.discard ? "Discard this take?" : `Delete ${confirm?.title ?? ""}?`}</AlertDialogTitle>
            <AlertDialogDescription className="text-base">
              {confirm?.discard ? "The take you just recorded goes for good." : "It goes for everyone, for good."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="h-11 rounded-xl text-base">Keep it</AlertDialogCancel>
            <AlertDialogAction
              className="h-11 rounded-xl bg-rose-700 text-base hover:bg-rose-800"
              onClick={() => {
                const c = confirm;
                setConfirm(null);
                if (c) void remove(c);
              }}
            >
              {confirm?.discard ? "Discard" : "Delete"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}
