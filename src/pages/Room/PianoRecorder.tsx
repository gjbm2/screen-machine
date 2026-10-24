// The piano's recorder, one for the whole app (owner, 24 Oct: Record from Music as well as the Room page). show_service
// has one recorder (recorder.py, through room_service's /show pass-through):
//   GET    /show/api/v1/piano/recordings              the recorder ("off" | "recording", how long, notes) and the takes
//   POST   /show/api/v1/piano/record {on}              Record / Stop; a stop saves the take ("last"), unless nothing was played
//   PATCH  /show/api/v1/piano/recordings/<id> {title}  Keep (with a new name)
//   DELETE /show/api/v1/piano/recordings/<id>          Discard
// useRecorder(baseUrl) reads it: one poll per address however many parts of the page use it (the Room page's Piano
// fold, Music's Mine, a header or dock pill), every 1 s while recording and every 5 s otherwise, only while the page is
// visible. The take just stopped ("fresh") is shared too, so Keep in one place clears it everywhere.
//   <PianoRecorder>   Record / Stop with its line (the dot flashes on each key), then the new take: Play it · Score ·
//                     Keep · Discard, with its name. `extra` sits beside Record (Mine's Upload a MIDI file).
//   <RecordPill>      a compact Record, or "● 1:23 Stop" while recording, for a header or the dock; Stop opens the new
//                     take in a sheet.
//   <NewTakePanel>    the new take on its own (PianoRecorder's and the pill's sheet).
// Play it plays the take through the app's music player (so the dock and Now playing show it), its name kept first.

import { useCallback, useEffect, useState, useSyncExternalStore, type ReactNode } from "react";
import { Check, Circle, FileMusic, Play, Square, Trash2 } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { useAppPlayerCtx } from "@/components/player/appPlayer";
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
import { useMusicMaybe } from "@/pages/Music/context";
import { explainError, loadToken, RoomApi, RoomApiError } from "./api";
import { Spinner } from "./bits";
import { ActionSheet } from "./overlays";
import { clock, RecordButton, RecordingLine, scoreLink, showSocketUrl, useKeyFlash } from "./RecordButton";
import { TAP_BUTTON_CLASS, THEME_CLASS } from "./theme";
import type { PianoRecordAsk, PianoRecording, PianoRecordingsView } from "./types";

const POLL_FAST_MS = 1000;
const POLL_SLOW_MS = 5000;
const SECONDARY = "border border-stone-300 bg-white hover:bg-stone-50";
const PRIMARY = "bg-primary text-primary-foreground hover:bg-primary/90";

export const REC_PREFIX = "rec:";
/** A take's id in the library and the player ("rec:<id>"). */
export const recPiece = (id: string) => `${REC_PREFIX}${id}`;

const plural = (n: number, one: string) => `${n} ${one}${n === 1 ? "" : "s"}`;

/** An error in the recorder's words (explainError's are about screens for 409 / 400). */
export function recorderWords(e: unknown): string {
  if (e instanceof RoomApiError) {
    if (e.status === 404) return "That recording isn't there any more.";
    if (e.status === 502 || e.status === 503) return "The music side isn't answering just now. Try again in a moment.";
    if ((e.status === 400 || e.status === 409) && e.detail) return e.detail;
    if (e.status === 409) return "It was refused just now. Try again in a moment.";
  }
  return explainError(e);
}

// ── The shared store: one per room_service address ──

interface RecState {
  view: PianoRecordingsView | null;
  /** When `view` was read (the elapsed time ticks on from it). */
  at: number;
  /** show_service has no recorder (an older one), or is down: the recorder isn't shown. */
  missing: boolean;
  /** The take just stopped, not yet kept or discarded. */
  fresh: PianoRecording | null;
  /** Its name as typed so far (shared, so the sheet and the card agree). */
  draft: string;
  /** A line after a stop ("Nothing was played…"). */
  note: string | null;
  /** The action under way: "record", "keep", "play", "score", "discard". */
  busy: string | null;
  err: string | null;
}

interface Store {
  api: RoomApi;
  state: RecState;
  listeners: Set<() => void>;
  subs: number;
  timer?: ReturnType<typeof setTimeout>;
  inflight: boolean;
  gen: number;
}

const stores = new Map<string, Store>();
const keyOf = (baseUrl: string) => baseUrl.replace(/\/+$/, "");

function storeFor(baseUrl: string): Store {
  const k = keyOf(baseUrl);
  let s = stores.get(k);
  if (!s) {
    s = {
      api: new RoomApi(k, loadToken(baseUrl).token),
      state: { view: null, at: 0, missing: false, fresh: null, draft: "", note: null, busy: null, err: null },
      listeners: new Set(),
      subs: 0,
      inflight: false,
      gen: 0,
    };
    stores.set(k, s);
  }
  return s;
}

function patch(s: Store, p: Partial<RecState>) {
  s.state = { ...s.state, ...p };
  s.listeners.forEach((l) => l());
}

const isRecording = (v: PianoRecordingsView | null) => v?.recording?.state === "recording";

function schedule(s: Store) {
  clearTimeout(s.timer);
  if (s.subs > 0 && !s.inflight) s.timer = setTimeout(() => void poll(s), isRecording(s.state.view) ? POLL_FAST_MS : POLL_SLOW_MS);
}

async function poll(s: Store) {
  if (s.inflight) return;
  s.inflight = true;
  if (document.visibilityState === "visible") {
    const g = s.gen;
    try {
      const v = await s.api.pianoRecordings();
      if (g === s.gen) {
        if (v === null) patch(s, { missing: true });
        else patch(s, { view: v, at: Date.now(), missing: false });
      }
    } catch {
      // the page says when the connection is lost; keep the last view
    }
  }
  s.inflight = false;
  schedule(s);
}

/** An action's answer: shown at once, and a poll that left before it is dropped. */
function setView(s: Store, v: PianoRecordingsView) {
  s.gen += 1;
  patch(s, { view: v, at: Date.now(), missing: false });
  schedule(s);
}

let visHooked = false;
function hookVisibility() {
  if (visHooked) return;
  visHooked = true;
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible") return;
    stores.forEach((s) => {
      if (s.subs > 0) void poll(s);
    });
  });
}

function subscribe(s: Store, l: () => void) {
  s.listeners.add(l);
  s.subs += 1;
  hookVisibility();
  if (s.subs === 1) void poll(s);
  return () => {
    s.listeners.delete(l);
    s.subs -= 1;
    if (s.subs === 0) clearTimeout(s.timer);
  };
}

export interface Recorder {
  api: RoomApi;
  /** show_service has no recorder, or isn't answering yet: nothing to show. */
  missing: boolean;
  /** The first reading has come. */
  ready: boolean;
  recording: boolean;
  /** Seconds recorded so far (ticks between polls). */
  elapsed: number;
  notes: number;
  /** The takes, newest first. */
  takes: PianoRecording[];
  fresh: PianoRecording | null;
  draft: string;
  setDraft: (t: string) => void;
  note: string | null;
  busy: string | null;
  err: string | null;
  start: () => Promise<boolean>;
  /** Stop; the take saved (or null: nothing was played). `ask`: a score's piece or section (Record on a score). */
  stop: (ask?: PianoRecordAsk) => Promise<PianoRecording | null>;
  /** Keep the fresh take under its typed name (renamed only when it changed). */
  keep: () => Promise<PianoRecording | null>;
  discard: () => Promise<boolean>;
  /** Forget the fresh take here (it stays kept, under its own name). */
  dismiss: () => void;
  refresh: () => Promise<void>;
}

/** The piano's recorder at this room_service address (see the top of the file). */
export function useRecorder(baseUrl: string): Recorder {
  const s = storeFor(baseUrl);
  const sub = useCallback((l: () => void) => subscribe(s, l), [s]);
  const get = useCallback(() => s.state, [s]);
  const st = useSyncExternalStore(sub, get, get);
  const recording = isRecording(st.view);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!recording) return;
    setNow(Date.now());
    const id = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(id);
  }, [recording]);

  const rec = st.view?.recording;
  const elapsed = recording ? (rec?.since_s ?? rec?.duration_s ?? 0) + (st.at ? Math.max(0, now - st.at) / 1000 : 0) : 0;

  const act = useCallback(
    async <T,>(busy: string, fn: () => Promise<T>, fallback: T): Promise<T> => {
      patch(s, { busy, err: null });
      try {
        return await fn();
      } catch (e) {
        patch(s, { err: recorderWords(e) });
        return fallback;
      } finally {
        patch(s, { busy: null });
      }
    },
    [s],
  );

  const refresh = useCallback(async () => {
    try {
      const v = await s.api.pianoRecordings();
      if (v) setView(s, v);
    } catch {
      /* the next poll */
    }
  }, [s]);

  const start = useCallback(
    () =>
      act(
        "record",
        async () => {
          patch(s, { note: null, fresh: null, draft: "" });
          setView(s, await s.api.pianoRecord(true));
          return true;
        },
        false,
      ),
    [act, s],
  );

  const stop = useCallback(
    (ask?: PianoRecordAsk) =>
      act(
        "record",
        async () => {
          const v = await s.api.pianoRecord(false, ask);
          setView(s, v);
          if (v.last) patch(s, { fresh: v.last, draft: v.last.title, note: v.error ?? null });
          else patch(s, { note: v.error ?? "Nothing was played, so there's no recording to keep." });
          return v.last ?? null;
        },
        null as PianoRecording | null,
      ),
    [act, s],
  );

  const keep = useCallback(
    () =>
      act(
        "keep",
        async () => {
          const t = s.state.fresh;
          if (!t) return null;
          const title = s.state.draft.trim();
          let kept = t;
          try {
            if (title && title !== t.title) kept = { ...t, ...(await s.api.pianoRename(t.id, title)) };
          } catch (e) {
            if (e instanceof RoomApiError && e.status === 404) patch(s, { fresh: null }); // deleted meanwhile (another phone)
            throw e;
          }
          patch(s, { fresh: null, draft: "" });
          await refresh();
          return kept;
        },
        null as PianoRecording | null,
      ),
    [act, refresh, s],
  );

  const discard = useCallback(
    () =>
      act(
        "discard",
        async () => {
          const t = s.state.fresh;
          if (!t) return false;
          try {
            await s.api.pianoDelete(t.id);
          } catch (e) {
            if (!(e instanceof RoomApiError && e.status === 404)) throw e; // already gone is what was wanted
          }
          patch(s, { fresh: null, draft: "" });
          await refresh();
          return true;
        },
        false,
      ),
    [act, refresh, s],
  );

  const setDraft = useCallback((t: string) => patch(s, { draft: t }), [s]);
  const dismiss = useCallback(() => patch(s, { fresh: null, draft: "" }), [s]);

  return {
    api: s.api,
    missing: st.missing,
    ready: !!st.view,
    recording,
    elapsed,
    notes: rec?.notes ?? 0,
    takes: st.view?.recordings ?? [],
    fresh: st.fresh,
    draft: st.draft,
    setDraft,
    note: st.note,
    busy: st.busy,
    err: st.err,
    start,
    stop,
    keep,
    discard,
    dismiss,
    refresh,
  };
}

/** "New recording · 0:21 · 80 notes". */
const freshLine = (t: PianoRecording) => `New recording · ${clock(t.duration_s)} · ${plural(t.notes, "note")}`;

/** The app's music player here: the Music page's, else the docked one (null: Play it isn't offered). */
function usePlayerHere() {
  const music = useMusicMaybe();
  const app = useAppPlayerCtx();
  return music?.player ?? (app?.enabled ? app.player : null);
}

/** The take just recorded: its name, then Play it · Score · Keep · Discard. */
export function NewTakePanel({
  baseUrl,
  onDone,
  onLog,
  heading = true,
  className,
}: {
  baseUrl: string;
  /** Its own "New recording · 0:21 · 80 notes" line (a sheet says it in its title instead). */
  heading?: boolean;
  /** After Play it, Score, Keep or Discard (a sheet closes). */
  onDone?: () => void;
  /** "kept …", "discarded …", "playing …" (the Room page's activity log). */
  onLog?: (what: string) => void;
  className?: string;
}) {
  const r = useRecorder(baseUrl);
  const player = usePlayerHere();
  const music = useMusicMaybe();
  const navigate = useNavigate();
  const [confirm, setConfirm] = useState(false);
  const [doing, setDoing] = useState<string | null>(null);
  const t = r.fresh;
  if (!t) return null;
  const name = r.draft.trim() || t.title;

  const done = (what: string | null) => {
    if (what) onLog?.(what);
    music?.bumpLibrary();
    onDone?.();
  };
  // each keeps the name first (as Keep does)
  const keepAs = async (what: string) => {
    setDoing(what);
    const k = await r.keep();
    setDoing(null);
    return k;
  };
  const keep = async () => {
    const k = await keepAs("keep");
    if (k) done(`kept "${name}"`);
    return k;
  };
  const playIt = async () => {
    if (!player) return;
    const k = await keepAs("play");
    if (!k) return;
    done(`playing "${name}"`);
    const res = await player.act({ action: "play", piece: recPiece(k.id) });
    if (!res.ok) music?.flash(res.words);
  };
  const score = async () => {
    const k = await keepAs("score");
    if (!k) return;
    done(null);
    const l = scoreLink(recPiece(k.id));
    navigate(l.to, { state: l.state });
  };

  const busy = r.busy;
  return (
    <div className={cn("space-y-3 rounded-xl border border-teal-300 bg-teal-50/70 p-3", className)} data-testid="piano-new-take">
      {heading && <p className="text-base font-semibold">{freshLine(t)}</p>}
      <label className="block">
        <span className="text-sm text-muted-foreground">Name</span>
        <Input
          value={r.draft}
          maxLength={120}
          onChange={(e) => r.setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void keep();
          }}
          className="mt-1 h-11 rounded-xl bg-white text-base md:text-base"
        />
      </label>
      <div className="flex flex-wrap gap-2">
        {player && (
          <button
            type="button"
            disabled={!!busy}
            onClick={() => void playIt()}
            className={cn(TAP_BUTTON_CLASS, PRIMARY, "inline-flex items-center gap-2 font-semibold disabled:opacity-60")}
          >
            {doing === "play" ? <Spinner /> : <Play className="h-4 w-4" aria-hidden />} Play it
          </button>
        )}
        <button
          type="button"
          disabled={!!busy}
          onClick={() => void score()}
          className={cn(TAP_BUTTON_CLASS, SECONDARY, "inline-flex items-center gap-2 px-4 font-semibold disabled:opacity-60")}
        >
          {doing === "score" ? <Spinner /> : <FileMusic className="h-4 w-4" aria-hidden />} Score
        </button>
        <button
          type="button"
          disabled={!!busy}
          onClick={() => void keep()}
          className={cn(TAP_BUTTON_CLASS, SECONDARY, "inline-flex items-center gap-2 px-4 font-semibold disabled:opacity-60")}
        >
          {doing === "keep" ? <Spinner /> : <Check className="h-4 w-4" aria-hidden />} Keep
        </button>
        <button
          type="button"
          disabled={!!busy}
          onClick={() => setConfirm(true)}
          className={cn(TAP_BUTTON_CLASS, SECONDARY, "inline-flex items-center gap-2 px-4 text-rose-800 disabled:opacity-60")}
        >
          {busy === "discard" ? <Spinner /> : <Trash2 className="h-4 w-4" aria-hidden />} Discard
        </button>
      </div>
      {r.err && <p className="text-sm text-rose-700">{r.err}</p>}

      <AlertDialog open={confirm} onOpenChange={(o) => !o && setConfirm(false)}>
        <AlertDialogContent className={cn(THEME_CLASS, "rounded-2xl")}>
          <AlertDialogHeader>
            <AlertDialogTitle>Discard this recording?</AlertDialogTitle>
            <AlertDialogDescription className="text-base">The recording you just made goes for good.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="h-11 rounded-xl text-base">Keep it</AlertDialogCancel>
            <AlertDialogAction
              className="h-11 rounded-xl bg-rose-700 text-base hover:bg-rose-800"
              onClick={() => {
                setConfirm(false);
                void r.discard().then((ok) => ok && done(`discarded "${name}"`));
              }}
            >
              Discard
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

/**
 * Record / Stop with its line, then the new take. Nothing but `extra` when show_service has no recorder.
 */
export function PianoRecorder({
  baseUrl,
  connected = true,
  hint = "Record what's played on the piano, then keep it or play it back.",
  extra,
  onLog,
  onError,
}: {
  baseUrl: string;
  /** False: Record waits for the connection (Stop always works). */
  connected?: boolean;
  /** The line beside Record while it isn't recording (null: none). */
  hint?: ReactNode;
  /** Beside Record, always shown (Mine's Upload a MIDI file). */
  extra?: ReactNode;
  onLog?: (what: string) => void;
  onError?: (msg: string) => void;
}) {
  const r = useRecorder(baseUrl);
  const flash = useKeyFlash(showSocketUrl(r.api.baseUrl), r.recording);
  useEffect(() => {
    if (r.err) onError?.(r.err);
  }, [r.err, onError]);

  const show = !r.missing && r.ready;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        {show && (
          <RecordButton
            recording={r.recording}
            busy={r.busy === "record"}
            disabled={!r.recording && !connected}
            elapsed={r.elapsed}
            onClick={() =>
              void (r.recording
                ? r.stop().then((t) => t && onLog?.("stopped recording"))
                : r.start().then((ok) => ok && onLog?.("recording")))
            }
          />
        )}
        {extra}
        {show && r.recording ? (
          <RecordingLine notes={r.notes} flash={flash} />
        ) : show && hint && !extra ? (
          <p className="min-w-0 flex-1 text-sm text-muted-foreground">{hint}</p>
        ) : null}
      </div>
      {show && r.note && !r.recording && <p className="text-base text-muted-foreground">{r.note}</p>}
      {show && <NewTakePanel baseUrl={baseUrl} onLog={onLog} />}
      {show && !r.fresh && r.err && <p className="text-sm text-rose-700">{r.err}</p>}
    </div>
  );
}

/**
 * A compact Record for a header or the dock: "● Record" (when `idle`), or "● 1:23 · Stop" while recording, whoever
 * started it. Stop opens the new take in a sheet (unless `sheet` is false: then `onStopped` gets it).
 */
export function RecordPill({
  baseUrl,
  idle = true,
  sheet = true,
  onStopped,
  className,
}: {
  baseUrl: string;
  /** Offer Record while not recording (false: the pill shows only while recording, e.g. in the dock). */
  idle?: boolean;
  sheet?: boolean;
  onStopped?: (take: PianoRecording | null) => void;
  className?: string;
}) {
  const r = useRecorder(baseUrl);
  const [open, setOpen] = useState(false);
  // the sheet closes once the take is kept or discarded anywhere
  useEffect(() => {
    if (!r.fresh) setOpen(false);
  }, [r.fresh]);
  if (r.missing || !r.ready) return null;

  const base =
    "inline-flex h-9 shrink-0 items-center gap-2 rounded-full border px-3 text-[15px] font-semibold shadow-sm transition-colors disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
  const stopIt = async () => {
    const t = await r.stop();
    onStopped?.(t);
    if (t && sheet) setOpen(true);
  };

  return (
    <>
      {r.recording ? (
        <button
          type="button"
          onClick={() => void stopIt()}
          disabled={r.busy === "record"}
          aria-label={`Recording, ${clock(r.elapsed)}. Stop`}
          className={cn(base, "border-rose-300 bg-rose-50 text-rose-900 hover:bg-rose-100", className)}
          data-testid="record-pill"
        >
          {r.busy === "record" ? (
            <Spinner />
          ) : (
            <span aria-hidden className="h-2.5 w-2.5 rounded-full bg-rose-600 motion-safe:animate-pulse" />
          )}
          <span className="font-mono tabular-nums">{clock(r.elapsed)}</span>
          <span className="inline-flex items-center gap-1 border-l border-rose-300 pl-2">
            <Square className="h-3.5 w-3.5 fill-current" aria-hidden /> Stop
          </span>
        </button>
      ) : idle ? (
        <button
          type="button"
          onClick={() => void r.start()}
          disabled={r.busy === "record"}
          className={cn(base, "border-stone-200/80 bg-card text-stone-800 hover:bg-stone-50", className)}
          data-testid="record-pill"
        >
          {r.busy === "record" ? <Spinner /> : <Circle className="h-3.5 w-3.5 fill-rose-600 text-rose-600" aria-hidden />} Record
        </button>
      ) : null}
      {sheet && (
        <ActionSheet
          open={open && !!r.fresh}
          onOpenChange={(o) => !o && setOpen(false)}
          title={r.fresh ? freshLine(r.fresh) : "New recording"}
          testId="record-pill-sheet"
        >
          <NewTakePanel baseUrl={baseUrl} heading={false} onDone={() => setOpen(false)} className="border-0 bg-transparent p-0 pb-1" />
        </ActionSheet>
      )}
    </>
  );
}
