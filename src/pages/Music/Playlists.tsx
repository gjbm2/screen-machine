// Playlists: the saved ones (name, how many pieces, how long) with Play on each, and New playlist. Open one to play it
// from the top or from any row, rename it, put the pieces in order (up / down), take pieces out, or delete it. A piece
// that has left the library stays in its place, faded, "No longer in the library". Pieces go in from Browse or Mine (⋯).

import { useEffect, useRef, useState } from "react";
import { ArrowDown, ArrowUp, ArrowUpDown, Check, ChevronLeft, ChevronRight, ListMusic, Pencil, Play, Plus, Trash2, X } from "lucide-react";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { Pill, Spinner } from "@/pages/Room/bits";
import { CARD_CLASS, TAP_BUTTON_CLASS } from "@/pages/Room/theme";
import { clock, musicWords, plural, totalWords, type Playlist, type PlaylistEntry } from "./api";
import { Confirm, ListState, PIECE_LIST, PieceRow, PieceSheet, PRIMARY, SECONDARY, type PieceRef } from "./bits";
import { useMusic } from "./context";
import { PLAYER_BUSY } from "./usePlayer";

const ICON_BTN =
  "grid h-11 w-10 shrink-0 place-items-center rounded-xl text-stone-600 hover:bg-stone-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

function NewPlaylist({ onMade }: { onMade: (id: string) => void }) {
  const { api, playlists } = useMusic();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const make = async () => {
    const n = name.trim();
    if (!n) return;
    setBusy(true);
    setErr(null);
    try {
      const pl = await api.createPlaylist(n);
      await playlists.reload();
      setOpen(false);
      setName("");
      if (pl?.id) onMade(pl.id);
    } catch (e) {
      setErr(musicWords(e));
    } finally {
      setBusy(false);
    }
  };
  if (!open)
    return (
      <button type="button" onClick={() => setOpen(true)} className={cn(TAP_BUTTON_CLASS, PRIMARY, "inline-flex items-center gap-2 font-semibold")} data-testid="new-playlist">
        <Plus className="h-4 w-4" aria-hidden /> New playlist
      </button>
    );
  return (
    <div className="space-y-1">
      <div className="flex flex-wrap items-center gap-2">
        <Input
          aria-label="New playlist's name"
          autoFocus
          placeholder="Name it, e.g. Sunday morning"
          value={name}
          maxLength={80}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void make();
            if (e.key === "Escape") setOpen(false);
          }}
          className="h-11 min-w-0 flex-1 basis-56 rounded-xl bg-white text-base md:text-base"
        />
        <button type="button" onClick={() => void make()} className={cn(TAP_BUTTON_CLASS, PRIMARY, "inline-flex items-center gap-2 font-semibold")}>
          {busy ? <Spinner /> : <Check className="h-4 w-4" aria-hidden />} Make it
        </button>
        <button type="button" aria-label="Don't make one" onClick={() => setOpen(false)} className={cn("grid h-11 w-11 place-items-center rounded-xl", SECONDARY)}>
          <X className="h-4 w-4" aria-hidden />
        </button>
      </div>
      {err && <p className="text-[15px] text-rose-700">{err}</p>}
    </div>
  );
}

function PlaylistView({ id, onBack }: { id: string; onBack: () => void }) {
  const { api, player, playlists, flash } = useMusic();
  const [pl, setPl] = useState<Playlist | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [sheet, setSheet] = useState<PieceRef | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  /** Change order: each row gets up / down / take out (its ⋯ goes while it's on, so the titles keep their room). */
  const [ordering, setOrdering] = useState(false);
  const saving = useRef(0);

  useEffect(() => {
    let stop = false;
    setPl(null);
    setError(null);
    api
      .playlist(id)
      .then((p) => !stop && setPl(p ? { ...p, items: Array.isArray(p.items) ? p.items : [] } : null))
      .catch((e) => !stop && setError(musicWords(e)));
    return () => {
      stop = true;
    };
  }, [api, id]);

  const v = player.view;
  const onThis = v?.queue?.playlist?.id === id;

  /** Save a new order or a removal at once; put it back if the room refuses. */
  const save = async (items: PlaylistEntry[]) => {
    if (!pl) return;
    const before = pl;
    setPl({ ...pl, items });
    setErr(null);
    const n = ++saving.current;
    try {
      const out = await api.savePlaylist(id, { items: items.map((x) => x.piece) });
      if (n === saving.current && out?.items) setPl(out);
      void playlists.reload();
    } catch (e) {
      if (n === saving.current) setPl(before);
      setErr(musicWords(e));
    }
  };
  const move = (i: number, by: number) => {
    if (!pl) return;
    const j = i + by;
    if (j < 0 || j >= pl.items.length) return;
    const items = [...pl.items];
    [items[i], items[j]] = [items[j], items[i]];
    void save(items);
  };
  const removeAt = (i: number) => pl && void save(pl.items.filter((_, k) => k !== i));
  const playFrom = async (index: number) => {
    if (!pl) return;
    const r = await player.act({ action: "play", playlist: id, index }, { state: "preparing", words: "Getting ready…", progress: null, error: null });
    if (r.ok) flash(`Playing ${pl.name}${index > 0 ? ` from ${pl.items[index]?.title ?? "there"}` : ""}`);
    else setErr(r.words);
  };
  const rename = async () => {
    const n = name.trim();
    if (!pl || !n || n === pl.name) {
      setRenaming(false);
      return;
    }
    setBusy("rename");
    setErr(null);
    try {
      const out = await api.savePlaylist(id, { name: n });
      setPl(out?.items ? out : { ...pl, name: n });
      setRenaming(false);
      void playlists.reload();
    } catch (e) {
      setErr(musicWords(e));
    } finally {
      setBusy(null);
    }
  };
  const remove = async () => {
    setConfirm(false);
    setBusy("delete");
    try {
      await api.deletePlaylist(id);
      flash(`Deleted ${pl?.name ?? "the playlist"}`);
      void playlists.reload();
      onBack();
    } catch (e) {
      setErr(musicWords(e));
    } finally {
      setBusy(null);
    }
  };

  const total = (pl?.items ?? []).reduce((s, x) => s + (x.missing ? 0 : (x.duration_s ?? 0)), 0);

  return (
    <div className="space-y-2" data-testid="playlist-view">
      <button type="button" onClick={onBack} className="-ml-1 -my-1 inline-flex min-h-11 items-center gap-1 rounded-xl px-1 text-[15px] font-medium text-teal-800 hover:bg-stone-100">
        <ChevronLeft className="h-5 w-5" aria-hidden /> All playlists
      </button>
      {error && <p className="text-base text-amber-800">{error}</p>}
      {!pl && !error && (
        <p className="flex items-center gap-2 text-base text-muted-foreground">
          <Spinner /> Opening it…
        </p>
      )}
      {pl && (
        <>
          <div className={cn(CARD_CLASS, "space-y-2 px-3 py-2 sm:px-4")}>
            {renaming ? (
              <div className="flex flex-wrap items-center gap-2">
                <Input
                  aria-label="Playlist's name"
                  autoFocus
                  value={name}
                  maxLength={80}
                  onChange={(e) => setName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void rename();
                    if (e.key === "Escape") setRenaming(false);
                  }}
                  className="h-11 min-w-0 flex-1 basis-48 rounded-xl bg-white text-base md:text-base"
                />
                <button type="button" onClick={() => void rename()} className={cn(TAP_BUTTON_CLASS, PRIMARY, "inline-flex items-center gap-2 font-semibold")}>
                  {busy === "rename" ? <Spinner /> : <Check className="h-4 w-4" aria-hidden />} Save
                </button>
              </div>
            ) : (
              <div className="flex items-start gap-2">
                <h3 className="min-w-0 flex-1 truncate py-1 text-lg font-semibold leading-snug">
                  {pl.name}
                  <span className="text-[15px] font-normal text-muted-foreground">
                    {" · "}
                    {[plural(pl.items.length, "piece"), totalWords(total)].filter(Boolean).join(" · ")}
                  </span>
                </h3>
                {onThis && v && PLAYER_BUSY.includes(v.state) && (
                  <Pill tone="live" pulse>
                    Playing
                  </Pill>
                )}
              </div>
            )}
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => void playFrom(0)}
                className={cn(TAP_BUTTON_CLASS, PRIMARY, "inline-flex items-center gap-2 font-semibold")}
                data-testid="playlist-play"
              >
                {player.busy === "play" ? <Spinner /> : <Play className="h-4 w-4" aria-hidden />} Play
              </button>
              <button
                type="button"
                onClick={() => {
                  setName(pl.name);
                  setRenaming(true);
                }}
                className={cn(TAP_BUTTON_CLASS, SECONDARY, "inline-flex items-center gap-2 px-4")}
              >
                <Pencil className="h-4 w-4" aria-hidden /> Rename
              </button>
              {pl.items.length > 1 && (
                <button
                  type="button"
                  aria-pressed={ordering}
                  onClick={() => setOrdering(!ordering)}
                  className={cn(TAP_BUTTON_CLASS, ordering ? "border border-teal-300 bg-teal-50 text-teal-900" : SECONDARY, "inline-flex items-center gap-2 px-4")}
                >
                  {ordering ? <Check className="h-4 w-4" aria-hidden /> : <ArrowUpDown className="h-4 w-4" aria-hidden />} {ordering ? "Done" : "Change order"}
                </button>
              )}
              <button type="button" onClick={() => setConfirm(true)} className={cn(TAP_BUTTON_CLASS, SECONDARY, "inline-flex items-center gap-2 px-4 text-rose-800")}>
                {busy === "delete" ? <Spinner /> : <Trash2 className="h-4 w-4" aria-hidden />} Delete
              </button>
            </div>
            {err && <p className="text-[15px] text-rose-700">{err}</p>}
          </div>

          {pl.items.length === 0 ? (
            <p className="text-base text-muted-foreground">Nothing in it yet. Add pieces from Browse or Mine with ⋯.</p>
          ) : (
            <ol className={PIECE_LIST} aria-label="Pieces in order">
              {pl.items.map((x, i) => (
                <PieceRow
                  key={`${x.piece}-${i}`}
                  joined
                  piece={{ id: x.piece, title: x.title, composer: x.composer }}
                  line={[x.composer, x.genre_label].filter(Boolean).join(" · ")}
                  meta={clock(x.duration_s)}
                  missing={x.missing}
                  onPlay={() => {
                    if (!ordering) void playFrom(i);
                  }}
                  onMore={x.missing || ordering ? undefined : () => setSheet({ id: x.piece, title: x.title, composer: x.composer })}
                  right={
                    !ordering ? (
                      x.missing ? (
                        <button type="button" aria-label={`Take ${x.title} out`} onClick={() => removeAt(i)} className={ICON_BTN}>
                          <X className="h-4 w-4" aria-hidden />
                        </button>
                      ) : null
                    ) : (
                    <span className="flex shrink-0 items-center">
                      <button type="button" aria-label={`Move ${x.title} up`} onClick={() => move(i, -1)} className={cn(ICON_BTN, i === 0 && "invisible")}>
                        <ArrowUp className="h-4 w-4" aria-hidden />
                      </button>
                      <button
                        type="button"
                        aria-label={`Move ${x.title} down`}
                        onClick={() => move(i, 1)}
                        className={cn(ICON_BTN, i === pl.items.length - 1 && "invisible")}
                      >
                        <ArrowDown className="h-4 w-4" aria-hidden />
                      </button>
                      <button type="button" aria-label={`Take ${x.title} out`} onClick={() => removeAt(i)} className={ICON_BTN}>
                        <X className="h-4 w-4" aria-hidden />
                      </button>
                    </span>
                    )
                  }
                />
              ))}
            </ol>
          )}
        </>
      )}

      <PieceSheet piece={sheet} onClose={() => setSheet(null)} />
      <Confirm
        open={confirm}
        title={`Delete ${pl?.name ?? "this playlist"}?`}
        body="The playlist goes for good. The pieces in it stay in the library."
        action="Delete"
        onCancel={() => setConfirm(false)}
        onConfirm={() => void remove()}
      />
    </div>
  );
}

export function Playlists({ openId, onOpen }: { openId: string | null; onOpen: (id: string | null) => void }) {
  const { player, playlists, flash } = useMusic();
  useEffect(() => {
    void playlists.reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (openId) return <PlaylistView id={openId} onBack={() => onOpen(null)} />;

  const list = playlists.list;
  const v = player.view;
  const playingId = v && PLAYER_BUSY.includes(v.state) ? (v.queue?.playlist?.id ?? null) : null;
  const play = async (id: string, name: string) => {
    const r = await player.act({ action: "play", playlist: id, index: 0 }, { state: "preparing", words: "Getting ready…", progress: null, error: null });
    flash(r.ok ? `Playing ${name}` : r.words);
  };

  return (
    <div className="space-y-2" data-testid="music-playlists">
      <NewPlaylist onMade={(id) => onOpen(id)} />
      <ListState
        loading={list === null}
        error={list ? null : playlists.error}
        empty={!list || list.length === 0}
        emptyWords="No playlists yet. Make one, then add pieces to it from Browse or Mine with ⋯."
      />
      {list && list.length > 0 && (
        <ul className={PIECE_LIST} aria-label="Playlists">
          {list.map((pl) => (
            <li key={pl.id} className={cn("flex items-center gap-1", playingId === pl.id ? "bg-teal-50/70" : "bg-white")}>
              <button
                type="button"
                aria-label={`Play ${pl.name}`}
                onClick={() => void play(pl.id, pl.name)}
                className="ml-1 grid h-10 w-10 shrink-0 place-items-center rounded-full bg-teal-600 text-white hover:bg-teal-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <Play className="h-4 w-4 fill-white" aria-hidden />
              </button>
              <button
                type="button"
                onClick={() => onOpen(pl.id)}
                className="flex min-h-12 min-w-0 flex-1 items-center gap-2 py-1 pl-2 pr-2 text-left hover:bg-stone-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
              >
                <ListMusic className="h-4 w-4 shrink-0 text-stone-500" aria-hidden />
                <span className="min-w-0 truncate text-base font-medium">{pl.name}</span>
                <span className="min-w-0 flex-1 truncate text-sm text-muted-foreground">
                  {playingId === pl.id && <span className="font-medium text-teal-800">Playing · </span>}
                  {[plural(pl.count, "piece"), totalWords(pl.duration_s)].filter(Boolean).join(" · ")}
                </span>
                <ChevronRight className="h-5 w-5 shrink-0 text-stone-400" aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      )}
      {list && playlists.error && <p className="text-[15px] text-amber-800">{playlists.error}</p>}
    </div>
  );
}
