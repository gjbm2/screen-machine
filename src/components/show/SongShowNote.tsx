// The owner's verdict on how the show (lights and screens) interpreted a whole song (owner, 28 Nov: "it's the
// interpretation of a song generally ... We need a sensible input field for that"). Saved to the art director's
// feedback file (routes/show_notes.py), which the overnight eval scores every run against and the overnight loop reads.

import { useEffect, useState } from "react";
import { ThumbsDown, ThumbsUp } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { showNotes, type ShowNote } from "@/shared/api/showNotes";

export function SongShowNote({ song, title, onDone }: { song: string; title: string; onDone?: () => void }) {
  const [notes, setNotes] = useState<ShowNote[] | null>(null);
  const [verdict, setVerdict] = useState<"good" | "bad" | null>(null);
  const [words, setWords] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    showNotes.list(song).then((r) => setNotes(r.notes), () => setNotes([]));
  }, [song]);

  const save = async () => {
    if (!verdict || !words.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      const n = await showNotes.add(song, title, verdict, words.trim());
      setNotes((old) => [n, ...(old ?? [])]);
      setWords("");
      setVerdict(null);
      onDone?.();
    } catch (e) {
      setErr(String((e as Error).message || e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-2 rounded-xl bg-stone-50 p-3" data-testid="song-show-note">
      <div className="flex items-center gap-2">
        <Chip on={verdict === "good"} onClick={() => setVerdict("good")} label="Good">
          <ThumbsUp className="h-4 w-4" />
        </Chip>
        <Chip on={verdict === "bad"} onClick={() => setVerdict("bad")} label="Not right">
          <ThumbsDown className="h-4 w-4" />
        </Chip>
      </div>
      <textarea
        className="min-h-[4.5rem] w-full rounded-lg border border-stone-300 bg-white p-2 text-[15px]"
        placeholder="How the lights and screens played this song"
        value={words}
        onChange={(e) => setWords(e.target.value)}
        maxLength={1000}
      />
      <div className="flex items-center justify-end gap-2">
        {err && <span className="mr-auto text-sm text-red-700">{err}</span>}
        <Button size="sm" disabled={!verdict || !words.trim() || busy} onClick={() => void save()}>
          Save
        </Button>
      </div>
      {!!notes?.length && (
        <ul className="space-y-1 border-t border-stone-200 pt-2">
          {notes.slice(0, 5).map((n) => (
            <li key={`${n.at}-${n.words.slice(0, 12)}`} className="flex gap-2 text-sm leading-snug">
              {n.verdict === "good" ? (
                <ThumbsUp className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-700" />
              ) : (
                <ThumbsDown className="mt-0.5 h-3.5 w-3.5 shrink-0 text-red-700" />
              )}
              <span className="min-w-0 flex-1">
                {n.t_s != null && <span className="text-muted-foreground">{fmtT(n.t_s)} · </span>}
                {n.words}
              </span>
              <span className="shrink-0 text-muted-foreground">{new Date(n.at * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short" })}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Chip({ on, onClick, label, children }: { on: boolean; onClick: () => void; label: string; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={on}
      className={cn(
        "flex h-9 items-center gap-1.5 rounded-full border px-3 text-sm",
        on ? "border-primary bg-primary/10 text-foreground" : "border-stone-300 text-muted-foreground hover:text-foreground",
      )}
    >
      {children}
      {label}
    </button>
  );
}

function fmtT(s: number): string {
  const m = Math.floor(s / 60);
  return `${m}:${String(Math.round(s - m * 60)).padStart(2, "0")}`;
}
