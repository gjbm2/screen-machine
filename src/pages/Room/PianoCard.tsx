// The Piano fold (owner, 22 Oct; 24 Oct: one recorder, one list of recordings): Record what's played on the piano
// (PianoRecorder.tsx, the same recorder as Music › Mine: Record / Stop, then the new recording's Play it · Score · Keep ·
// Discard), then the newest three recordings as Music's rows (tap = play through the app's music player, so the dock
// and Now playing show it; the score button; ⋯ for Play next, Add to a playlist…, Edit details, Download, Delete), and
// "All recordings ›" (Music › Mine). The rows need the Music page's parts, so the card gives them a MusicProvider of
// its own: the app's player (the dock's), the library client, and a short message line in the card.
// Also here, shared with the Music page and Test music: SoundChoice (the piano's sound) and PlayStatus (the test
// piano's progress). Everything goes through room_service's /show pass-through to show_service. The whole card stays
// hidden until show_service has the recorder. Words only: no ids, files or numbers a person doesn't need.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Check, ChevronRight } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import { useAppPlayer } from "@/components/player/appPlayer";
import { cn } from "@/lib/utils";
import { MusicApi } from "@/pages/Music/api";
import { isDefaultTake, PieceRow, PieceSheet, type PieceRef } from "@/pages/Music/bits";
import { MusicProvider, useFlash, usePlaylists, type MusicCtx, type MusicTab } from "@/pages/Music/context";
import { PLAYER_BUSY, usePlayer } from "@/pages/Music/usePlayer";
import { Spinner, Unroll } from "./bits";
import { PianoRecorder, recPiece, REC_PREFIX, useRecorder } from "./PianoRecorder";
import { clock } from "./RecordButton";
import { CARD_CLASS } from "./theme";
import type { PianoRecording, ShowTestState } from "./types";
import { type PianoSound } from "./usePianoSound";
import type { NewLogEntry } from "./useRoomConnection";

/** Recordings shown in the fold before "All recordings ›". */
const SHOW_FIRST = 3;

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

/** "Concert grand (Steinway D, audience seat)" -> ["Concert grand", "Steinway D, audience seat"]. */
function splitLabel(label: string): [string, string | null] {
  const m = /^(.*?)\s*\((.+)\)\s*$/.exec(label);
  return m && m[1] ? [m[1], m[2]] : [label, null];
}

/** The piano's Sound, from show_service's list (labels exactly as it sends them). Nothing without it. Where it plays is
 *  its own control on the page (Play on: PlayOn.tsx), so the choice shows once. One-line rows (the name, and the
 *  label's detail under it), two to a line from sm up; a plain select on a phone. `bare`: no "Sound" heading (inside
 *  something that already says Sound); `scope`: a word after the heading ("Player", "This device"). */
export function SoundChoice({
  sound,
  disabled,
  onInstrument,
  bare = false,
  scope,
}: {
  sound: PianoSound;
  disabled?: boolean;
  onInstrument?: (id: string) => void;
  bare?: boolean;
  scope?: string;
}) {
  if (!sound.available) return null;
  const pick = onInstrument ?? sound.setInstrument;
  const value = sound.instrument;
  return (
    <div>
      {!bare && (
        <span className="text-base font-medium">
          Sound{scope ? <span className="font-normal text-muted-foreground"> · {scope}</span> : null}
        </span>
      )}
      <select
        aria-label="Sound"
        value={value ?? ""}
        disabled={disabled}
        onChange={(e) => e.target.value && pick(e.target.value)}
        className={cn("h-11 w-full rounded-xl border border-stone-200 bg-white px-3 text-base sm:hidden", !bare && "mt-2", disabled && "opacity-60")}
      >
        {value === null && (
          <option value="" disabled>
            Choose a sound
          </option>
        )}
        {sound.instruments.map((it) => (
          <option key={it.id} value={it.id}>
            {it.label}
          </option>
        ))}
      </select>
      <div role="radiogroup" aria-label="Sound" className={cn("hidden gap-1.5 sm:grid sm:grid-cols-2", !bare && "mt-2")}>
        {sound.instruments.map((it) => {
          const sel = it.id === value;
          const [name, detail] = splitLabel(it.label);
          return (
            <button
              key={it.id}
              type="button"
              role="radio"
              aria-checked={sel}
              aria-label={it.label}
              disabled={disabled || sel}
              onClick={() => pick(it.id)}
              className={cn(
                "flex min-h-11 min-w-0 items-center gap-2 rounded-xl border px-3 py-1.5 text-left transition-colors disabled:cursor-default",
                sel ? "border-teal-300 bg-teal-50 text-teal-900" : "border-stone-200 bg-white hover:bg-stone-50",
                !sel && disabled && "opacity-60",
              )}
            >
              <span className="min-w-0 flex-1 leading-tight">
                <span className="block truncate text-[15px] font-medium">{name}</span>
                {detail && <span className="block truncate text-[13px] text-muted-foreground">{detail}</span>}
              </span>
              {sel && <Check className="h-4 w-4 shrink-0 text-teal-700" aria-hidden />}
            </button>
          );
        })}
      </div>
    </div>
  );
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


/** /music?<what>, keeping ?room= (the Room page's address). */
function musicHref(what: Record<string, string>): string {
  const q = new URLSearchParams(window.location.search);
  const out = new URLSearchParams();
  const room = q.get("room");
  if (room) out.set("room", room);
  for (const [k, v] of Object.entries(what)) out.set(k, v);
  return `/music?${out.toString()}`;
}

/** A recording as the Music page's rows and sheet see it (its library id is "rec:<id>"). */
function pieceOf(t: PianoRecording): PieceRef {
  return { id: recPiece(t.id), title: t.title, composer: t.composer ?? null, genre: t.genre ?? null, kind: "rec", editable: true };
}

/** "Ursula · 0:29 · Today 09:21"; a recording still named after its date leaves the date off. */
function takeLine(t: PianoRecording): string {
  return [t.composer, clock(t.duration_s), isDefaultTake(t.title) ? null : when(t.recorded_at)].filter(Boolean).join(" · ");
}

/** The Music page's shared parts for the rows here: the app's player (the dock's), the library, a message line. */
function useRoomMusic(baseUrl: string, onLibrary: () => void): { ctx: MusicCtx; msg: string | null } {
  const navigate = useNavigate();
  const api = useMemo(() => new MusicApi(baseUrl), [baseUrl]);
  const shared = useAppPlayer(baseUrl);
  const own = usePlayer(api, !shared);
  const player = shared ?? own;
  const playlists = usePlaylists(api, false);
  const [msg, flash] = useFlash();
  const [pins, setPins] = useState<Record<string, boolean>>({});
  const [libraryRev, setLibraryRev] = useState(0);
  const libRef = useRef(onLibrary);
  libRef.current = onLibrary;
  const ctx = useMemo<MusicCtx>(
    () => ({
      api,
      player,
      playlists,
      flash,
      pinnedOf: (id, fallback) => (id in pins ? pins[id] : fallback),
      setPinned: (id, on) => setPins((p) => ({ ...p, [id]: on })),
      libraryRev,
      bumpLibrary: () => {
        setLibraryRev((n) => n + 1);
        libRef.current();
      },
      setTab: (t: MusicTab) => navigate(musicHref({ tab: t })),
      openPlaylist: (id) => navigate(musicHref({ tab: "playlists", pl: id })),
    }),
    [api, player, playlists, flash, pins, libraryRev, navigate],
  );
  return { ctx, msg };
}

export function PianoCard({
  api,
  connected,
  addLog,
  bare = false,
  onSummary,
}: {
  api: { baseUrl: string };
  connected: boolean;
  addLog: (e: NewLogEntry) => void;
  /** Kept for the Room page's call (the recorder shows its own problems). */
  reportError?: (e: unknown) => void;
  /** Inside a fold that already says "Piano": no heading of its own. */
  bare?: boolean;
  /** Told the card in one line ("Record · last recording Today 14:05", "Recording · 1:23"), or null while the
   *  recorder isn't there (the card is hidden), e.g. for a fold's closed summary. */
  onSummary?: (line: string | null) => void;
}) {
  const r = useRecorder(api.baseUrl);
  const { ctx, msg } = useRoomMusic(api.baseUrl, r.refresh);
  const [sheet, setSheet] = useState<PieceRef | null>(null);
  const log = useCallback((what: string) => addLog({ type: "fixture", level: "info", summary: `Piano: ${what}` }), [addLog]);

  // The card in one line, for the fold it sits in (from the readings above: nothing new is fetched).
  const sumRef = useRef(onSummary);
  sumRef.current = onSummary;
  const pv = ctx.player.view;
  const playingRec = pv?.current?.id?.startsWith(REC_PREFIX) && PLAYER_BUSY.includes(pv.state) ? pv.current : null;
  const summaryLine = (() => {
    if (r.missing) return null;
    if (!r.ready) return undefined;
    if (r.recording) return `Recording · ${clock(r.elapsed)}`;
    if (playingRec) return `Playing · ${playingRec.title}`;
    const newest = r.takes.reduce<PianoRecording | null>((a, t) => (!a || t.recorded_at > a.recorded_at ? t : a), null);
    return newest ? `Record · last recording ${when(newest.recorded_at) || newest.title}` : "Record · no recordings yet";
  })();
  useEffect(() => {
    if (summaryLine !== undefined) sumRef.current?.(summaryLine);
  }, [summaryLine]);

  if (r.missing || !r.ready) return null;

  const rows = r.takes.filter((t) => t.id !== r.fresh?.id);
  const shown = rows.slice(0, SHOW_FIRST);
  const link = "inline-flex min-h-11 items-center gap-1 rounded-xl px-1 text-[15px] font-medium text-teal-800 hover:underline";

  return (
    <MusicProvider value={ctx}>
      <section aria-labelledby="room-piano">
        {bare ? (
          <span id="room-piano" className="sr-only">
            Piano
          </span>
        ) : (
          <h2 id="room-piano" className="mb-2 text-lg font-semibold text-stone-800">
            Piano
          </h2>
        )}
        <div className={cn(CARD_CLASS, "space-y-4 px-3 py-3 sm:px-4")}>
          <PianoRecorder baseUrl={api.baseUrl} connected={connected} onLog={log} />

          {rows.length > 0 ? (
            <section aria-labelledby="room-piano-recs" className="space-y-2">
              <h3 id="room-piano-recs" className="text-[15px] font-semibold">
                Recordings
              </h3>
              <ul className="space-y-2" aria-label="Recordings">
                {shown.map((t) => (
                  <PieceRow key={t.id} piece={pieceOf(t)} line={takeLine(t)} onMore={() => setSheet(pieceOf(t))} />
                ))}
              </ul>
              <Link to={musicHref({ tab: "mine" })} className={link}>
                {rows.length > SHOW_FIRST ? `All ${rows.length} recordings` : "All recordings"} <ChevronRight className="h-4 w-4" aria-hidden />
              </Link>
            </section>
          ) : (
            !r.fresh && !r.recording && <p className="text-base text-muted-foreground">No recordings yet. Press Record and play.</p>
          )}

          {msg && (
            <p className="text-[15px] text-teal-900" role="status">
              {msg}
            </p>
          )}

          {bare && (
            <Link to="/settings#music" className={cn(link, "text-stone-600")}>
              Piano settings <ChevronRight className="h-4 w-4" aria-hidden />
            </Link>
          )}
        </div>
        <PieceSheet piece={sheet} onClose={() => setSheet(null)} />
      </section>
    </MusicProvider>
  );
}
