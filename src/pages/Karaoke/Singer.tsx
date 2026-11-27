// Who sings (owner, 27 Nov: "optionally note who is singing which song (needs a quick drop-down / auto-complete in a
// given session because e.g. you don't want to have to type out everyone's name repeatedly"). The screens use it:
// "Clare, you're up next" (DisplayPage/karaoke/JoinOverlays.tsx), "Clare sings" on the intro card.
//
// - SingerBox: "Who's singing?" above the search. It remembers its name on this phone, so whoever adds from their
//   own phone types their name once; whoever adds for others picks each name from tonight's (the server's names of
//   the last 12 h: KaraokeState.singers) as a suggestion. Every song added from this page carries it (singerNow()).
// - SingerTag: a queue entry's singer, tapped to change (the same suggestions) or cleared.

import { useEffect, useId, useRef, useState } from "react";
import { MicVocal, X } from "lucide-react";
import { cn } from "@/lib/utils";

const KEY = "karaoke.singer";
const MAX = 30;

const tidy = (s: string) => s.replace(/\s+/g, " ").trim().slice(0, MAX);

/** The name songs added from this phone go under ("" : nobody said). */
export function singerNow(): string | null {
  try {
    return tidy(localStorage.getItem(KEY) ?? "") || null;
  } catch {
    return null;
  }
}

function Suggestions({ id, names }: { id: string; names: string[] }) {
  return (
    <datalist id={id}>
      {names.map((n) => (
        <option key={n} value={n} />
      ))}
    </datalist>
  );
}

export function SingerBox({ names }: { names: string[] }) {
  const [name, setName] = useState(() => singerNow() ?? "");
  const listId = useId();
  const save = (v: string) => {
    setName(v);
    try {
      if (tidy(v)) localStorage.setItem(KEY, tidy(v));
      else localStorage.removeItem(KEY);
    } catch {
      /* private mode: this page only */
    }
  };
  return (
    <label className="flex items-center gap-2 rounded-xl bg-card/80 px-3 py-2 text-sm ring-1 ring-stone-200/70">
      <MicVocal className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
      <span className="shrink-0 text-muted-foreground">Who's singing?</span>
      <input
        value={name}
        onChange={(e) => save(e.target.value)}
        list={listId}
        maxLength={MAX}
        placeholder="Name (optional)"
        autoComplete="off"
        enterKeyHint="done"
        onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
        className="min-w-0 flex-1 bg-transparent font-medium outline-none placeholder:font-normal placeholder:text-muted-foreground/70"
      />
      {name && (
        <button
          type="button"
          aria-label="No name"
          className="rounded-full p-1 text-muted-foreground hover:text-foreground"
          onClick={() => save("")}
        >
          <X className="h-4 w-4" />
        </button>
      )}
      <Suggestions id={listId} names={names} />
    </label>
  );
}

export function SingerTag({
  singer,
  names,
  onChange,
}: {
  singer?: string;
  names: string[];
  onChange: (name: string | null) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [v, setV] = useState(singer ?? "");
  const listId = useId();
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (editing) ref.current?.focus();
    else setV(singer ?? "");
  }, [editing, singer]);
  const done = () => {
    setEditing(false);
    if (tidy(v) !== (singer ?? "")) onChange(tidy(v) || null);
  };
  if (editing)
    return (
      <>
        <input
          ref={ref}
          value={v}
          onChange={(e) => setV(e.target.value)}
          onBlur={done}
          onKeyDown={(e) => {
            if (e.key === "Enter") done();
            if (e.key === "Escape") setEditing(false);
          }}
          list={listId}
          maxLength={MAX}
          placeholder="Who sings?"
          autoComplete="off"
          enterKeyHint="done"
          className="mr-1 w-28 rounded-full bg-stone-100 px-2.5 py-1 text-sm outline-none ring-1 ring-primary/40"
        />
        <Suggestions id={listId} names={names} />
      </>
    );
  return (
    <button
      type="button"
      onClick={() => setEditing(true)}
      aria-label={singer ? `Sung by ${singer}: change` : "Who sings this?"}
      className={cn(
        "mr-1 flex max-w-[7rem] items-center gap-1 rounded-full px-2 py-1 text-sm",
        singer ? "bg-primary/10 font-medium text-foreground" : "text-muted-foreground/70 hover:text-foreground",
      )}
    >
      <MicVocal className="h-3.5 w-3.5 shrink-0" aria-hidden />
      {singer && <span className="truncate">{singer}</span>}
    </button>
  );
}
