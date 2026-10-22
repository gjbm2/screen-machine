// Mine: what's been added here. "Upload a MIDI file" up front (a .mid or .midi up to 1 MB; title, composer and genre
// filled in from the file's name, all changeable), then the piano recordings (made with the Room page's Piano card)
// and the uploads and finds from the web, newest first. Tap to play; ⋯ for Play next, Add to the queue, Add to a
// playlist…, Pin, Rename, Download and Delete. After an upload it says what the cleaning took out ("Kept the piano;
// dropped the drums and bass.").

import { useEffect, useRef, useState } from "react";
import { Check, Download, Pencil, Play, Trash2, Upload, X } from "lucide-react";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { Notice, Spinner } from "@/pages/Room/bits";
import { Modal } from "@/pages/Room/overlays";
import { TAP_BUTTON_CLASS } from "@/pages/Room/theme";
import { GENRES, itemLine, MAX_UPLOAD_BYTES, musicWords, plural, whenWords, type MusicItem } from "./api";
import { Confirm, ListState, PieceRow, PieceSheet, PRIMARY, SECONDARY, SHEET_ROW, playGuess, type PieceRef } from "./bits";
import { useMusic } from "./context";

const PAGE = 100;

/** "Classical - Chopin - Nocturne_Op9_No2.mid" -> genre Classical, composer Chopin, title "Nocturne Op9 No2". */
function guessFromName(filename: string): { title: string; composer: string; genre: string } {
  const base = filename
    .replace(/\.(midi?|kar)$/i, "")
    .replace(/[_]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  const parts = base.split(/\s+-\s+/).map((p) => p.trim()).filter(Boolean);
  const genreOf = (s: string) => GENRES.find((g) => g.label.toLowerCase() === s.toLowerCase() || g.id === s.toLowerCase())?.id ?? "";
  if (parts.length >= 3 && genreOf(parts[0])) return { genre: genreOf(parts[0]), composer: parts[1], title: parts.slice(2).join(" - ") };
  if (parts.length === 2) return { genre: "", composer: parts[0], title: parts[1] };
  return { genre: "", composer: "", title: base || filename };
}

const FIELD = "h-11 rounded-xl bg-white text-base md:text-base";

/** Title, composer and genre, for an upload or a rename. */
function PieceFields({
  title,
  composer,
  genre,
  onChange,
  withMore,
  onSubmit,
}: {
  title: string;
  composer: string;
  genre: string;
  onChange: (p: Partial<{ title: string; composer: string; genre: string }>) => void;
  /** Composer and genre too (not for a recording, which has only a name). */
  withMore: boolean;
  onSubmit: () => void;
}) {
  return (
    <div className="space-y-3">
      <label className="block space-y-1">
        <span className="text-[15px] font-medium">{withMore ? "Title" : "Name"}</span>
        <Input autoFocus value={title} maxLength={120} onChange={(e) => onChange({ title: e.target.value })} onKeyDown={(e) => e.key === "Enter" && onSubmit()} className={FIELD} />
      </label>
      {withMore && (
        <>
          <label className="block space-y-1">
            <span className="text-[15px] font-medium">Composer</span>
            <Input
              value={composer}
              maxLength={120}
              placeholder="If you know it"
              onChange={(e) => onChange({ composer: e.target.value })}
              onKeyDown={(e) => e.key === "Enter" && onSubmit()}
              className={FIELD}
            />
          </label>
          <div className="space-y-1">
            <span className="text-[15px] font-medium">Genre</span>
            <div role="radiogroup" aria-label="Genre" className="flex flex-wrap gap-2">
              {[{ id: "", label: "Not sure" }, ...GENRES].map((g) => (
                <button
                  key={g.id || "none"}
                  type="button"
                  role="radio"
                  aria-checked={genre === g.id}
                  onClick={() => onChange({ genre: g.id })}
                  className={cn(
                    "min-h-10 rounded-full border px-3 text-[15px] font-medium",
                    genre === g.id ? "border-teal-300 bg-teal-50 text-teal-900" : "border-stone-200 bg-white text-stone-700 hover:bg-stone-50",
                  )}
                >
                  {g.label}
                </button>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

/** One list of Mine (recordings, or uploads and web finds), newest first. */
function useMineList(kind: "rec" | "user") {
  const { api, libraryRev } = useMusic();
  const [items, setItems] = useState<MusicItem[] | null>(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [limit, setLimit] = useState(PAGE);
  useEffect(() => {
    let stop = false;
    api
      .search({ kind, sort: "recent", limit })
      .then((r) => {
        if (stop) return;
        setItems(Array.isArray(r?.items) ? r.items : []);
        setTotal(typeof r?.total === "number" ? r.total : (r?.items?.length ?? 0));
        setError(null);
      })
      .catch((e) => !stop && setError(musicWords(e)));
    return () => {
      stop = true;
    };
  }, [api, kind, limit, libraryRev]);
  return { items, total, error, more: () => setLimit((l) => l + PAGE) };
}

export function Mine() {
  const { api, player, flash, bumpLibrary } = useMusic();
  const recs = useMineList("rec");
  const ups = useMineList("user");
  const fileInput = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [form, setForm] = useState({ title: "", composer: "", genre: "" });
  const [busy, setBusy] = useState(false);
  const [uploadErr, setUploadErr] = useState<string | null>(null);
  const [tooBig, setTooBig] = useState<string | null>(null);
  const [done, setDone] = useState<{ item: MusicItem; words: string | null } | null>(null);
  const [sheet, setSheet] = useState<PieceRef | null>(null);
  const [renaming, setRenaming] = useState<PieceRef | null>(null);
  const [renameForm, setRenameForm] = useState({ title: "", composer: "", genre: "" });
  const [renameErr, setRenameErr] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<PieceRef | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const pick = (f: File | undefined) => {
    setTooBig(null);
    setUploadErr(null);
    if (!f) return;
    if (f.size > MAX_UPLOAD_BYTES) {
      setTooBig(`${f.name} is ${(f.size / (1024 * 1024)).toFixed(1)} MB. The room takes MIDI files up to 1 MB.`);
      return;
    }
    setForm(guessFromName(f.name));
    setFile(f);
  };
  const upload = async () => {
    if (!file || busy) return;
    setBusy(true);
    setUploadErr(null);
    try {
      const r = await api.upload(file, { filename: file.name, title: form.title || undefined, composer: form.composer || undefined, genre: form.genre || undefined });
      setFile(null);
      setDone({ item: r.item, words: r.cleaned?.words ?? null });
      flash(`Added ${r.item?.title ?? "it"}`);
      bumpLibrary();
    } catch (e) {
      setUploadErr(musicWords(e));
    } finally {
      setBusy(false);
    }
  };
  const saveRename = async () => {
    if (!renaming || busy) return;
    const t = renameForm.title.trim();
    if (!t) return;
    setBusy(true);
    setRenameErr(null);
    try {
      const patch = renaming.kind === "user" ? { title: t, composer: renameForm.composer.trim() || null, genre: renameForm.genre || null } : { title: t };
      await api.edit(renaming.id, patch);
      setRenaming(null);
      flash("Renamed");
      bumpLibrary();
    } catch (e) {
      setRenameErr(musicWords(e));
    } finally {
      setBusy(false);
    }
  };
  const remove = async (p: PieceRef) => {
    setDeleting(null);
    setErr(null);
    try {
      await api.remove(p.id);
      flash(`Deleted ${p.title}`);
      if (done?.item.id === p.id) setDone(null);
      bumpLibrary();
    } catch (e) {
      setErr(musicWords(e));
    }
  };

  const extra = (p: PieceRef, close: () => void) => (
    <>
      <button
        type="button"
        className={SHEET_ROW}
        onClick={() => {
          close();
          setRenameErr(null);
          setRenameForm({ title: p.title, composer: p.composer ?? "", genre: p.genre ?? "" });
          setRenaming(p);
        }}
      >
        <Pencil className="h-5 w-5 text-stone-600" aria-hidden /> Rename
      </button>
      <a href={api.downloadUrl(p.id)} download={`${p.title}.mid`} className={SHEET_ROW} onClick={() => close()}>
        <Download className="h-5 w-5 text-stone-600" aria-hidden /> Download the MIDI file
      </a>
      <button
        type="button"
        className={cn(SHEET_ROW, "text-rose-800")}
        onClick={() => {
          close();
          setDeleting(p);
        }}
      >
        <Trash2 className="h-5 w-5" aria-hidden /> Delete
      </button>
    </>
  );

  const list = (l: ReturnType<typeof useMineList>, emptyWords: string, label: string) => (
    <>
      <ListState loading={l.items === null} error={l.error} empty={!l.items || l.items.length === 0} emptyWords={emptyWords} />
      {l.items && l.items.length > 0 && (
        <ul className="space-y-2" aria-label={label}>
          {l.items.map((it) => (
            <PieceRow
              key={it.id}
              piece={it}
              line={[itemLine(it), whenWords(it.added_at)].filter(Boolean).join(" · ")}
              onMore={() => setSheet(it)}
            />
          ))}
        </ul>
      )}
      {l.items && l.items.length < l.total && (
        <button type="button" onClick={l.more} className={cn(TAP_BUTTON_CLASS, SECONDARY, "w-full font-medium")}>
          Show more ({l.total - l.items.length} to go)
        </button>
      )}
    </>
  );

  return (
    <div className="space-y-5" data-testid="music-mine">
      <div className="space-y-2">
        <button type="button" onClick={() => fileInput.current?.click()} className={cn(TAP_BUTTON_CLASS, PRIMARY, "inline-flex items-center gap-2 font-semibold")} data-testid="upload-midi">
          <Upload className="h-4 w-4" aria-hidden /> Upload a MIDI file
        </button>
        <input
          ref={fileInput}
          type="file"
          accept=".mid,.midi,audio/midi,audio/x-midi"
          className="hidden"
          onChange={(e) => {
            pick(e.target.files?.[0]);
            e.target.value = "";
          }}
        />
        <p className="text-[15px] text-muted-foreground">A .mid file up to 1 MB. Drums and other instruments are taken out so it plays as piano.</p>
        {tooBig && <Notice tone="warn" className="text-base">{tooBig}</Notice>}
        {done && (
          <Notice tone="ok" className="text-base">
            <div className="flex items-start gap-2">
              <div className="min-w-0 flex-1">
                <p className="font-medium">Added “{done.item.title}”.</p>
                {done.words && <p>{done.words}</p>}
              </div>
              <button type="button" aria-label="Close" onClick={() => setDone(null)} className="-mr-1 -mt-1 grid h-9 w-9 shrink-0 place-items-center rounded-full hover:bg-black/5">
                <X className="h-4 w-4" aria-hidden />
              </button>
            </div>
            <button
              type="button"
              onClick={() => void player.act({ action: "play", piece: done.item.id }, playGuess(done.item)).then((r) => flash(r.ok ? `Playing ${done.item.title}` : r.words))}
              className={cn(TAP_BUTTON_CLASS, PRIMARY, "mt-2 inline-flex items-center gap-2 font-semibold")}
            >
              <Play className="h-4 w-4" aria-hidden /> Play it
            </button>
          </Notice>
        )}
      </div>

      <section className="space-y-2" aria-labelledby="mine-recs">
        <h3 id="mine-recs" className="text-[15px] font-semibold">
          Recordings{recs.total > 0 ? <span className="font-normal text-muted-foreground"> · {plural(recs.total, "take")}</span> : null}
        </h3>
        {list(recs, "No recordings yet. Record the piano with the Room page's Piano card.", "Recordings")}
      </section>

      <section className="space-y-2" aria-labelledby="mine-ups">
        <h3 id="mine-ups" className="text-[15px] font-semibold">
          Uploads and finds from the web{ups.total > 0 ? <span className="font-normal text-muted-foreground"> · {ups.total}</span> : null}
        </h3>
        {list(ups, "Nothing yet. Upload a MIDI file above, or find one under From the web.", "Uploads")}
      </section>

      {err && <p className="text-[15px] text-rose-700">{err}</p>}

      <PieceSheet piece={sheet} onClose={() => setSheet(null)} extra={extra} />

      <Modal open={!!file} onOpenChange={(o) => !o && !busy && setFile(null)} title="Add this piece" description={file?.name}>
        <div className="space-y-4">
          <PieceFields {...form} withMore onChange={(p) => setForm((f) => ({ ...f, ...p }))} onSubmit={() => void upload()} />
          {uploadErr && <p className="text-[15px] text-rose-700">{uploadErr}</p>}
          <button type="button" onClick={() => void upload()} className={cn(TAP_BUTTON_CLASS, PRIMARY, "inline-flex w-full items-center justify-center gap-2 font-semibold")}>
            {busy ? <Spinner /> : <Upload className="h-4 w-4" aria-hidden />} {busy ? "Adding it…" : "Add it"}
          </button>
        </div>
      </Modal>

      <Modal open={!!renaming} onOpenChange={(o) => !o && !busy && setRenaming(null)} title="Rename">
        <div className="space-y-4">
          <PieceFields {...renameForm} withMore={renaming?.kind === "user"} onChange={(p) => setRenameForm((f) => ({ ...f, ...p }))} onSubmit={() => void saveRename()} />
          {renameErr && <p className="text-[15px] text-rose-700">{renameErr}</p>}
          <button type="button" onClick={() => void saveRename()} className={cn(TAP_BUTTON_CLASS, PRIMARY, "inline-flex w-full items-center justify-center gap-2 font-semibold")}>
            {busy ? <Spinner /> : <Check className="h-4 w-4" aria-hidden />} Save
          </button>
        </div>
      </Modal>

      <Confirm
        open={!!deleting}
        title={`Delete ${deleting?.title ?? ""}?`}
        body="It goes for good. Playlists keep its place, marked as no longer in the library."
        action="Delete"
        onCancel={() => setDeleting(null)}
        onConfirm={() => deleting && void remove(deleting)}
      />
    </div>
  );
}
