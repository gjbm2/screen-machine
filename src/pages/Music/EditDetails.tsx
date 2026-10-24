// A piece's details: title, composer and genre, for an upload as for a recording (the score's PDF prints the title and
// composer). PieceFields is the form (also Mine's "Add this piece" after an upload); EditDetails is the one "Edit
// details" dialog, opened from a piece's ⋯ (PieceSheet) wherever the piece is listed, and from its score.
// PATCH /show/api/v1/library/item/<id> {title, composer, genre}.

import { useEffect, useState } from "react";
import { Check } from "lucide-react";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { Spinner } from "@/pages/Room/bits";
import { Modal } from "@/pages/Room/overlays";
import { TAP_BUTTON_CLASS } from "@/pages/Room/theme";
import { GENRES, musicWords, type MusicItem } from "./api";
import { useMusic } from "./context";

const FIELD = "h-11 rounded-xl bg-white text-base md:text-base";
const PRIMARY = "bg-primary text-primary-foreground hover:bg-primary/90";

export type PieceDetails = { title: string; composer: string; genre: string };

/** Title, composer and genre, for an upload or a piece's details (a recording's too). */
export function PieceFields({
  title,
  composer,
  genre,
  onChange,
  onSubmit,
}: PieceDetails & {
  onChange: (p: Partial<PieceDetails>) => void;
  onSubmit: () => void;
}) {
  return (
    <div className="space-y-3">
      <label className="block space-y-1">
        <span className="text-[15px] font-medium">Title</span>
        <Input autoFocus value={title} maxLength={120} onChange={(e) => onChange({ title: e.target.value })} onKeyDown={(e) => e.key === "Enter" && onSubmit()} className={FIELD} />
      </label>
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
    </div>
  );
}

/** The "Edit details" dialog for one piece (null: closed). Save, or close it to leave it as it was. */
export function EditDetails({
  piece,
  onClose,
  onSaved,
}: {
  piece: { id: string; title: string; composer?: string | null; genre?: string | null } | null;
  onClose: () => void;
  onSaved?: (item: MusicItem | null) => void;
}) {
  const { api, flash, bumpLibrary } = useMusic();
  const [form, setForm] = useState<PieceDetails>({ title: "", composer: "", genre: "" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // filled in as it opens (by id: a caller may pass a new object on every render)
  const pid = piece?.id ?? null;
  useEffect(() => {
    if (piece) setForm({ title: piece.title, composer: piece.composer ?? "", genre: piece.genre ?? "" });
    setErr(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid]);

  const save = async () => {
    if (!piece || busy) return;
    const t = form.title.trim();
    if (!t) return;
    setBusy(true);
    setErr(null);
    try {
      const item = await api.edit(piece.id, { title: t, composer: form.composer.trim() || null, genre: form.genre || null });
      flash("Saved");
      bumpLibrary();
      onSaved?.(item ?? null);
      onClose();
    } catch (e) {
      setErr(musicWords(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal open={!!piece} onOpenChange={(o) => !o && !busy && onClose()} title="Edit details">
      <div className="space-y-4">
        <PieceFields {...form} onChange={(p) => setForm((f) => ({ ...f, ...p }))} onSubmit={() => void save()} />
        {err && <p className="text-[15px] text-rose-700">{err}</p>}
        <div className="flex gap-2">
          <button type="button" onClick={() => void save()} className={cn(TAP_BUTTON_CLASS, PRIMARY, "inline-flex flex-1 items-center justify-center gap-2 font-semibold")}>
            {busy ? <Spinner /> : <Check className="h-4 w-4" aria-hidden />} Save
          </button>
          <button
            type="button"
            onClick={() => !busy && onClose()}
            className={cn(TAP_BUTTON_CLASS, "border border-stone-300 bg-white px-4 hover:bg-stone-50")}
          >
            Cancel
          </button>
        </div>
      </div>
    </Modal>
  );
}
