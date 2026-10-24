// The Music page's shared pieces: a piece's row (tap = play now, a score button, ⋯ = more), the sheet behind ⋯ (Play
// next, Add to the queue, Add to a playlist…, Score, Pin, and for your own pieces Edit details, Download the MIDI file
// and Delete), a search box, chips, and a confirm dialog. Same light, warm skin as /room. The row and sheet also serve
// the Room page's Piano fold (its last recordings), inside a MusicProvider of its own.

import { useCallback, useEffect, useState, type ReactNode } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import {
  Check,
  ChevronLeft,
  Download,
  FileMusic,
  ListEnd,
  ListMusic,
  ListPlus,
  MoreHorizontal,
  Pencil,
  Pin,
  PinOff,
  Play,
  Plus,
  Search,
  Trash2,
  X,
} from "lucide-react";
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
import { Spinner } from "@/pages/Room/bits";
import { ActionSheet } from "@/pages/Room/overlays";
import { pageFrom, scoreLink } from "@/pages/Room/RecordButton";
import { THEME_CLASS, TAP_BUTTON_CLASS } from "@/pages/Room/theme";
import { musicWords, plural, totalWords, type MusicItem, type PlayerView } from "./api";
import { useMusic } from "./context";
import { EditDetails } from "./EditDetails";
import { PLAYER_BUSY } from "./usePlayer";

export const PRIMARY = "bg-primary text-primary-foreground hover:bg-primary/90";
export const SECONDARY = "border border-stone-300 bg-white hover:bg-stone-50";
export const CHIP = "inline-flex min-h-10 items-center gap-1.5 rounded-full border px-3.5 text-[15px] font-medium transition-colors";
export const CHIP_ON = "border-teal-300 bg-teal-50 text-teal-900";
export const CHIP_OFF = "border-stone-200 bg-white text-stone-700 hover:bg-stone-50";
export const SHEET_ROW =
  "flex min-h-12 w-full items-center gap-3 rounded-xl px-3 py-2 text-left text-base hover:bg-stone-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

/** What a row, the sheet and the player need of a piece. */
export interface PieceRef {
  id: string;
  title: string;
  composer?: string | null;
  genre?: string | null;
  kind?: string;
  pinned?: boolean;
  editable?: boolean;
}

/** The page's address carries an open score: /music?score=<piece id> (any other page can link to it). */
export const SCORE_PARAM = "score";

/** Has this piece a MIDI file to write a score from? Everything but the test patterns (bare ids, no "lib:" etc.). */
export function hasScore(p: { id: string; kind?: string | null }): boolean {
  return p.kind ? p.kind !== "phrase" : /^(lib|user|rec|file):/.test(p.id);
}

/** A recording still under the name it was given when it was made ("Take - Sat 24 Oct 09:26"): its date is its name. */
export function isDefaultTake(title: string | null | undefined): boolean {
  return /^Take - /.test(title ?? "");
}

/** Open a piece's score: on /music, ?score=<id> with the other params kept, a new history entry (so Back closes it);
 *  from any other page, /music?score=<id>. Either way the entry says where it was opened from (the score's back). */
export function useOpenScore(): (id: string) => void {
  const [, setParams] = useSearchParams();
  const { pathname } = useLocation();
  const navigate = useNavigate();
  return useCallback(
    (id: string) => {
      const from = pageFrom(pathname);
      if (from !== "Music") {
        const l = scoreLink(id, from);
        navigate(l.to, { state: l.state });
        return;
      }
      setParams(
        (p) => {
          const next = new URLSearchParams(p);
          next.set(SCORE_PARAM, id);
          return next;
        },
        { state: { score: true, from } },
      );
    },
    [navigate, pathname, setParams],
  );
}

/** The guess shown while "play now" is on its way (the reply replaces it). */
export function playGuess(it: PieceRef): Partial<PlayerView> {
  const current = { kind: "lib", pinned: false, editable: false, ...it } as MusicItem;
  return { state: "preparing", words: "Getting ready…", current, progress: null, error: null };
}

/** Is this piece the one the player is on (and is it playing)? */
export function useNowOn(id: string): "playing" | "preparing" | null {
  const v = useMusic().player.view;
  if (!v?.current || v.current.id !== id || !PLAYER_BUSY.includes(v.state)) return null;
  return v.state === "preparing" ? "preparing" : "playing";
}

export function PieceRow({
  piece,
  line,
  onMore,
  onPlay,
  missing,
  right,
  testId,
}: {
  piece: PieceRef;
  /** The second line: composer · performer · genre · 4:32. */
  line?: ReactNode;
  onMore?: () => void;
  /** Tap the row; default: play it now. */
  onPlay?: () => void;
  /** No longer in the library (a playlist's gap): shown faded with those words. */
  missing?: boolean;
  /** Extra buttons before ⋯ (a playlist's up / down / remove). */
  right?: ReactNode;
  testId?: string;
}) {
  const { player, flash, pinnedOf } = useMusic();
  const openScore = useOpenScore();
  const on = useNowOn(piece.id);
  const pinned = pinnedOf(piece.id, !!piece.pinned);
  const play =
    onPlay ??
    (() => {
      void player.act({ action: "play", piece: piece.id }, playGuess(piece)).then((r) => flash(r.ok ? `Playing ${piece.title}` : r.words));
    });
  return (
    <li
      data-testid={testId}
      className={cn("flex items-center gap-1 rounded-xl border bg-white", on ? "border-teal-300 bg-teal-50/50" : "border-stone-200")}
    >
      <button
        type="button"
        onClick={play}
        className={cn(
          "flex min-h-14 min-w-0 flex-1 items-center gap-3 rounded-xl px-3 py-2 text-left hover:bg-stone-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          missing && "opacity-60",
        )}
      >
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-1.5">
            <span className="truncate text-base font-medium">{piece.title}</span>
            {pinned && <Pin className="h-3.5 w-3.5 shrink-0 fill-teal-700 text-teal-700" aria-label="Pinned" />}
          </span>
          {missing ? (
            <span className="block text-sm text-muted-foreground">No longer in the library</span>
          ) : (
            line && <span className="block truncate text-sm text-muted-foreground">{line}</span>
          )}
        </span>
        {on && (
          <span className="inline-flex shrink-0 items-center gap-1.5 text-sm font-medium text-teal-800">
            {on === "preparing" ? <Spinner className="h-3.5 w-3.5" /> : <span aria-hidden className="h-2 w-2 rounded-full bg-teal-600 motion-safe:animate-pulse" />}
            <span className="sr-only sm:not-sr-only">{on === "preparing" ? "Getting ready" : "Playing"}</span>
          </span>
        )}
      </button>
      {right}
      {!missing && hasScore(piece) && (
        <button
          type="button"
          onClick={() => openScore(piece.id)}
          aria-label={`Score of ${piece.title}`}
          title="Score"
          className="grid h-11 w-11 shrink-0 place-items-center rounded-xl text-stone-600 hover:bg-stone-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <FileMusic className="h-5 w-5" aria-hidden />
        </button>
      )}
      {onMore && (
        <button
          type="button"
          onClick={onMore}
          aria-label={`More for ${piece.title}`}
          className="grid h-11 w-11 shrink-0 place-items-center rounded-xl text-stone-600 hover:bg-stone-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <MoreHorizontal className="h-5 w-5" aria-hidden />
        </button>
      )}
    </li>
  );
}

/**
 * The sheet behind a row's ⋯: Play now · Play next · Add to the queue · Add to a playlist… · Score · Pin / Unpin, and
 * for your own pieces (`editable`: uploads, finds from the web, recordings) Edit details · Download the MIDI file ·
 * Delete (asked first), then anything the list adds (`extra`).
 */
export function PieceSheet({
  piece,
  onClose,
  extra,
  noScore,
}: {
  piece: PieceRef | null;
  onClose: () => void;
  extra?: (p: PieceRef, close: () => void) => ReactNode;
  /** On the score itself: no "Score" row (it would reopen the same score). */
  noScore?: boolean;
}) {
  const { api, player, playlists, flash, pinnedOf, setPinned, bumpLibrary } = useMusic();
  const [choosing, setChoosing] = useState(false);
  const [editing, setEditing] = useState<PieceRef | null>(null);
  const [deleting, setDeleting] = useState<PieceRef | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [naming, setNaming] = useState(false);
  const openScore = useOpenScore();

  useEffect(() => {
    setChoosing(false);
    setErr(null);
    setNaming(false);
    setNewName("");
  }, [piece?.id]);

  // Edit details and the delete question outlive the sheet (it closes as they open)
  const after = (
    <>
      <EditDetails piece={editing} onClose={() => setEditing(null)} />
      <Confirm
        open={!!deleting}
        title={`Delete ${deleting?.title ?? ""}?`}
        body="It goes for good. Playlists keep its place, marked as no longer in the library."
        action="Delete"
        onCancel={() => setDeleting(null)}
        onConfirm={() => {
          const p = deleting;
          setDeleting(null);
          if (!p) return;
          void api
            .remove(p.id)
            .then(() => {
              flash(`Deleted ${p.title}`);
              bumpLibrary();
            })
            .catch((e) => flash(musicWords(e)));
        }}
      />
    </>
  );
  if (!piece) return after;
  const pinned = pinnedOf(piece.id, !!piece.pinned);

  const run = async (what: string, fn: () => Promise<string | null>) => {
    setBusy(what);
    setErr(null);
    try {
      const said = await fn();
      if (said) flash(said);
      onClose();
    } catch (e) {
      setErr(musicWords(e));
    } finally {
      setBusy(null);
    }
  };
  const viaPlayer = (what: string, body: Parameters<typeof player.act>[0], said: string, guess?: Partial<PlayerView>) =>
    run(what, async () => {
      const r = await player.act(body, guess);
      if (!r.ok) throw new Error(r.words);
      return said;
    });

  const addTo = (id: string, name: string) =>
    run(`pl:${id}`, async () => {
      const pl = await api.playlist(id);
      await api.savePlaylist(id, { items: [...(pl.items ?? []).map((x) => x.piece), piece.id] });
      void playlists.reload();
      return `Added to ${name}`;
    });
  const addToNew = () => {
    const name = newName.trim();
    if (!name) return;
    void run("pl:new", async () => {
      await api.createPlaylist(name, [piece.id]);
      void playlists.reload();
      return `Made ${name}, with ${piece.title} in it`;
    });
  };

  const icon = (what: string, I: typeof Play) => (busy === what ? <Spinner className="h-5 w-5" /> : <I className="h-5 w-5 text-stone-600" aria-hidden />);

  return (
    <>
    {after}
    <ActionSheet
      open={!!piece}
      onOpenChange={(o) => !o && onClose()}
      title={piece.title}
      description={[piece.composer, choosing ? "Add to a playlist" : null].filter(Boolean).join(" · ") || undefined}
      testId="piece-sheet"
    >
      {!choosing ? (
        <div className="space-y-1 pb-1">
          <button
            type="button"
            className={SHEET_ROW}
            onClick={() => void viaPlayer("play", { action: "play", piece: piece.id }, `Playing ${piece.title}`, playGuess(piece))}
          >
            {icon("play", Play)} Play now
          </button>
          <button type="button" className={SHEET_ROW} onClick={() => void viaPlayer("next", { action: "play_next", piece: piece.id }, "It plays next")}>
            {icon("next", ListPlus)} Play next
          </button>
          <button type="button" className={SHEET_ROW} onClick={() => void viaPlayer("add", { action: "add", piece: piece.id }, "Added to the queue")}>
            {icon("add", ListEnd)} Add to the queue
          </button>
          <button
            type="button"
            className={SHEET_ROW}
            onClick={() => {
              setChoosing(true);
              void playlists.reload();
            }}
          >
            <ListMusic className="h-5 w-5 text-stone-600" aria-hidden /> Add to a playlist…
          </button>
          {!noScore && hasScore(piece) && (
            <button
              type="button"
              className={SHEET_ROW}
              onClick={() => {
                onClose();
                openScore(piece.id);
              }}
            >
              <FileMusic className="h-5 w-5 text-stone-600" aria-hidden /> Score
            </button>
          )}
          <button
            type="button"
            className={SHEET_ROW}
            onClick={() =>
              void run("pin", async () => {
                await api.pin(piece.id, !pinned);
                setPinned(piece.id, !pinned);
                return pinned ? "Unpinned" : "Pinned: quick to find under Pinned";
              })
            }
          >
            {busy === "pin" ? <Spinner className="h-5 w-5" /> : pinned ? <PinOff className="h-5 w-5 text-stone-600" aria-hidden /> : <Pin className="h-5 w-5 text-stone-600" aria-hidden />}
            {pinned ? "Unpin" : "Pin"}
          </button>
          {piece.editable && (
            <>
              <button
                type="button"
                className={SHEET_ROW}
                onClick={() => {
                  onClose();
                  setEditing(piece);
                }}
              >
                <Pencil className="h-5 w-5 text-stone-600" aria-hidden /> Edit details
              </button>
              <a href={api.downloadUrl(piece.id)} download={`${piece.title}.mid`} className={SHEET_ROW} onClick={() => onClose()}>
                <Download className="h-5 w-5 text-stone-600" aria-hidden /> Download the MIDI file
              </a>
              <button
                type="button"
                className={cn(SHEET_ROW, "text-rose-800")}
                onClick={() => {
                  onClose();
                  setDeleting(piece);
                }}
              >
                <Trash2 className="h-5 w-5" aria-hidden /> Delete
              </button>
            </>
          )}
          {extra?.(piece, onClose)}
        </div>
      ) : (
        <div className="space-y-1 pb-1">
          <button type="button" className={cn(SHEET_ROW, "text-[15px] text-muted-foreground")} onClick={() => setChoosing(false)}>
            <ChevronLeft className="h-5 w-5" aria-hidden /> Back
          </button>
          {playlists.list === null && !playlists.error && (
            <p className="flex items-center gap-2 px-3 py-2 text-base text-muted-foreground">
              <Spinner /> Finding the playlists…
            </p>
          )}
          {playlists.error && !playlists.list && <p className="px-3 py-2 text-base text-amber-800">{playlists.error}</p>}
          {(playlists.list ?? []).map((pl) => (
            <button key={pl.id} type="button" className={SHEET_ROW} onClick={() => void addTo(pl.id, pl.name)}>
              {busy === `pl:${pl.id}` ? <Spinner className="h-5 w-5" /> : <ListMusic className="h-5 w-5 text-stone-600" aria-hidden />}
              <span className="min-w-0 flex-1">
                <span className="block truncate font-medium">{pl.name}</span>
                <span className="block text-sm text-muted-foreground">
                  {[plural(pl.count, "piece"), totalWords(pl.duration_s)].filter(Boolean).join(" · ")}
                </span>
              </span>
            </button>
          ))}
          {naming ? (
            <div className="flex items-center gap-2 px-1 pt-1">
              <Input
                aria-label="New playlist's name"
                autoFocus
                placeholder="Name it"
                value={newName}
                maxLength={80}
                onChange={(e) => setNewName(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && addToNew()}
                className="h-11 min-w-0 flex-1 rounded-xl bg-white text-base md:text-base"
              />
              <button type="button" onClick={addToNew} className={cn(TAP_BUTTON_CLASS, PRIMARY, "inline-flex items-center gap-2 px-4 font-semibold")}>
                {busy === "pl:new" ? <Spinner /> : <Check className="h-4 w-4" aria-hidden />} Make it
              </button>
            </div>
          ) : (
            <button type="button" className={SHEET_ROW} onClick={() => setNaming(true)}>
              <Plus className="h-5 w-5 text-stone-600" aria-hidden /> New playlist
            </button>
          )}
        </div>
      )}
      {err && <p className="px-3 pb-2 text-[15px] text-rose-700">{err}</p>}
    </ActionSheet>
    </>
  );
}

export function SearchBox({
  value,
  onChange,
  onSubmit,
  placeholder,
  label,
  autoFocus,
}: {
  value: string;
  onChange: (v: string) => void;
  onSubmit?: () => void;
  placeholder: string;
  label: string;
  autoFocus?: boolean;
}) {
  return (
    <div className="relative min-w-0 flex-1">
      <Search className="pointer-events-none absolute left-3.5 top-1/2 h-5 w-5 -translate-y-1/2 text-stone-400" aria-hidden />
      <Input
        type="search"
        inputMode="search"
        enterKeyHint="search"
        aria-label={label}
        placeholder={placeholder}
        value={value}
        autoFocus={autoFocus}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") onSubmit?.();
        }}
        className="h-12 rounded-xl bg-white pl-11 pr-11 text-base shadow-sm md:text-base [&::-webkit-search-cancel-button]:hidden"
      />
      {value && (
        <button
          type="button"
          aria-label="Clear the search"
          onClick={() => onChange("")}
          className="absolute right-1 top-1/2 grid h-10 w-10 -translate-y-1/2 place-items-center rounded-full text-stone-500 hover:bg-stone-100"
        >
          <X className="h-4 w-4" aria-hidden />
        </button>
      )}
    </div>
  );
}

/** Are you sure? (Delete a playlist, an upload, a recording.) */
export function Confirm({
  open,
  title,
  body,
  action,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  title: string;
  body: string;
  action: string;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <AlertDialog open={open} onOpenChange={(o) => !o && onCancel()}>
      <AlertDialogContent className={cn(THEME_CLASS, "rounded-2xl")}>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription className="text-base">{body}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel className="h-11 rounded-xl text-base">Keep it</AlertDialogCancel>
          <AlertDialogAction className="h-11 rounded-xl bg-rose-700 text-base hover:bg-rose-800" onClick={onConfirm}>
            {action}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

/** "Loading…" / an error / "nothing here" for a list. */
export function ListState({ loading, error, empty, emptyWords }: { loading: boolean; error: string | null; empty: boolean; emptyWords: ReactNode }) {
  if (error) return <p className="py-2 text-base text-amber-800">{error}</p>;
  if (loading && empty)
    return (
      <p className="flex items-center gap-2 py-3 text-base text-muted-foreground">
        <Spinner /> Looking…
      </p>
    );
  if (empty) return <div className="py-3 text-base text-muted-foreground">{emptyWords}</div>;
  return null;
}
