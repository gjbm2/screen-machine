// Who sings: one name or several (owner, 27 Nov: "optionally note who is singing which song (needs a quick drop-down /
// auto-complete in a given session because e.g. you don't want to have to type out everyone's name repeatedly"; "needs
// to be poss. to add more than 1 singer"). The screens use it: "Clare & Tom, you're up next"
// (DisplayPage/karaoke/JoinOverlays.tsx), "Clare & Tom sing" on the intro card.
//
// - NamePicker: the names as chips (× takes one off), a box to type a new one (Enter or a comma adds it), and
//   tonight's names (the server's names of the last 12 h: KaraokeState.singers) as one-tap chips, filtered as you type.
// - askSingers / SingerAsk: picking a song to queue asks "Who's singing?" there and then (owner, 27 Nov: a box of its
//   own above the search read as orphaned; "better would be if after selecting the song, the 'who's singing' drop
//   down menu appeared"). It starts empty (owner, 27 Nov: the same person rarely sings two in a row); Nobody adds it
//   unsung; closing it adds nothing. It remembers its names on this phone, so whoever adds from their own
//   phone sets them once; whoever adds for others taps each song's names from tonight's. Every song added from this
//   page carries them (singersNow()).
// - SingerTag: a queue entry's singers, tapped to change them (NamePicker in a dialog).
// Small on a phone (owner, 27 Nov: "you may need to make fonts slightly smaller for mobile display"), sm: and up as
// the rest of the panel.

import { useEffect, useState, type PointerEvent } from "react";
import { MicVocal, Plus, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import { THEME_CLASS } from "@/pages/Room/theme";

const KEY = "karaoke.singers";
const OLD_KEY = "karaoke.singer"; // the first version: one name
const MAX = 30;
const MAX_NAMES = 8;
const SUGGEST = 10;

const tidy = (s: string) => s.replace(/\s+/g, " ").trim().slice(0, MAX);
const has = (list: string[], n: string) => list.some((x) => x.toLowerCase() === n.toLowerCase());
export const namesText = (n: string[]) =>
  n.length < 2 ? (n[0] ?? "") : `${n.slice(0, -1).join(", ")} & ${n[n.length - 1]}`;

/** The names songs added from this phone go under ([]: nobody said). */
export function singersNow(): string[] {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? "null");
    if (Array.isArray(v)) return v.map((x) => tidy(String(x))).filter(Boolean);
    const one = tidy(localStorage.getItem(OLD_KEY) ?? "");
    return one ? [one] : [];
  } catch {
    return [];
  }
}

function NamePicker({
  value,
  onChange,
  names,
  alwaysSuggest = false,
  autoFocus = false,
  max = SUGGEST,
  empty = "Name (optional)",
}: {
  value: string[];
  onChange: (v: string[]) => void;
  names: string[];
  /** Show tonight's names even before the box is touched (the dialog). */
  alwaysSuggest?: boolean;
  autoFocus?: boolean;
  /** At most this many of tonight's names (all of them that match once typing). */
  max?: number;
  /** The box's word while no name is in. */
  empty?: string;
}) {
  const [text, setText] = useState("");
  const [focused, setFocused] = useState(false);
  const add = (raw: string) => {
    const n = tidy(raw);
    if (!n || has(value, n) || value.length >= MAX_NAMES) return setText("");
    onChange([...value, names.find((k) => k.toLowerCase() === n.toLowerCase()) ?? n]);
    setText("");
  };
  const q = tidy(text).toLowerCase();
  const suggestions = names.filter((n) => !has(value, n) && (!q || n.toLowerCase().includes(q))).slice(0, q ? SUGGEST : max);
  const showSuggest = (alwaysSuggest || focused) && suggestions.length > 0;
  // A chip tapped keeps the typing box focused (so the list stays open and the keyboard up).
  const keep = (e: PointerEvent) => e.preventDefault();
  return (
    <div className="min-w-0 flex-1">
      <div className="flex min-w-0 flex-wrap items-center gap-1">
        {value.map((n) => (
          <span
            key={n}
            className="flex max-w-[10rem] items-center gap-0.5 rounded-full bg-primary/10 py-0.5 pl-2 pr-0.5 text-xs font-medium sm:text-sm"
          >
            <span className="truncate">{n}</span>
            <button
              type="button"
              aria-label={`Not ${n}`}
              onPointerDown={keep}
              onClick={() => onChange(value.filter((x) => x !== n))}
              className="rounded-full p-0.5 text-muted-foreground hover:text-foreground"
            >
              <X className="h-3 w-3 sm:h-3.5 sm:w-3.5" />
            </button>
          </span>
        ))}
        <input
          value={text}
          onChange={(e) => (e.target.value.includes(",") ? add(e.target.value.replace(/,/g, "")) : setText(e.target.value))}
          onFocus={() => setFocused(true)}
          onBlur={() => {
            setFocused(false);
            if (tidy(text)) add(text);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              if (tidy(text)) add(text);
              else (e.target as HTMLInputElement).blur();
            } else if (e.key === "Backspace" && !text && value.length) onChange(value.slice(0, -1));
          }}
          autoFocus={autoFocus}
          maxLength={MAX}
          placeholder={value.length ? "+ another" : empty}
          autoComplete="off"
          autoCapitalize="words"
          enterKeyHint="done"
          className="min-w-[5.5rem] flex-1 bg-transparent py-0.5 text-xs font-medium outline-none placeholder:font-normal placeholder:text-muted-foreground/70 sm:text-sm"
        />
      </div>
      {showSuggest && (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {suggestions.map((n) => (
            <button
              key={n}
              type="button"
              onPointerDown={keep}
              onClick={() => add(n)}
              className="flex items-center gap-0.5 rounded-full px-2 py-0.5 text-xs text-muted-foreground ring-1 ring-stone-300/80 hover:text-foreground sm:text-sm"
            >
              <Plus className="h-3 w-3" />
              {n}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** Tonight's singers as chips, added before anyone sings (owner, 27 Nov); × takes one off. ``onChange`` gets the
 *  names added and removed. */
export function Roster({
  names,
  onChange,
}: {
  names: string[];
  onChange: (add: string[], remove: string[]) => void;
}) {
  return (
    <div className="rounded-xl px-3 py-2 ring-1 ring-stone-200/70">
      <NamePicker
        value={names}
        names={[]}
        empty="Add a name"
        onChange={(next) =>
          onChange(
            next.filter((n) => !has(names, n)),
            names.filter((n) => !has(next, n)),
          )
        }
      />
    </div>
  );
}

let asker: ((title: string) => Promise<string[] | null>) | null = null;

/** Ask who sings a song about to be queued: the names ([] for nobody), or null if the person closed it (don't add). */
export const askSingers = (title: string): Promise<string[] | null> =>
  asker ? asker(title) : Promise.resolve(singersNow());

const remember = (v: string[]) => {
  try {
    localStorage.setItem(KEY, JSON.stringify(v));
    localStorage.removeItem(OLD_KEY);
  } catch {
    /* private mode: this page only */
  }
};

/** The one "Who's singing?" dialog askSingers opens (mounted once on the karaoke page). */
export function SingerAsk({ names }: { names: string[] }) {
  const [q, setQ] = useState<{ title: string; done: (v: string[] | null) => void } | null>(null);
  const [draft, setDraft] = useState<string[]>([]);
  useEffect(() => {
    asker = (title) =>
      new Promise((done) => {
        setDraft([]);
        setQ({ title, done });
      });
    return () => {
      asker = null;
    };
  }, []);
  const finish = (v: string[] | null) => {
    if (v) remember(v);
    q?.done(v);
    setQ(null);
  };
  return (
    <Dialog open={!!q} onOpenChange={(v) => !v && finish(null)}>
      <DialogContent className={cn(THEME_CLASS, "w-[calc(100vw-2rem)] max-w-md gap-3 p-4 sm:p-5")}>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-base">
            <MicVocal className="h-4 w-4 text-muted-foreground" aria-hidden /> Who's singing?
          </DialogTitle>
          <DialogDescription className="truncate text-xs sm:text-sm">{q?.title}</DialogDescription>
        </DialogHeader>
        <div className="rounded-xl px-3 py-2 ring-1 ring-stone-200/70">
          <NamePicker value={draft} onChange={setDraft} names={names} alwaysSuggest />
        </div>
        <div className="flex justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={() => finish([])}>
            Nobody
          </Button>
          <Button size="sm" onClick={() => finish(draft)} data-testid="singers-add">
            Add
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function SingerTag({
  singers,
  names,
  onChange,
}: {
  singers: string[];
  names: string[];
  onChange: (names: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<string[]>(singers);
  const close = (save: boolean) => {
    setOpen(false);
    if (save && draft.join("\n") !== singers.join("\n")) onChange(draft);
  };
  return (
    <>
      <button
        type="button"
        onClick={() => {
          setDraft(singers);
          setOpen(true);
        }}
        aria-label={singers.length ? `Sung by ${namesText(singers)}: change` : "Who sings this?"}
        className={cn(
          "mr-0.5 flex max-w-[5.5rem] items-center gap-1 rounded-full px-1.5 py-0.5 text-xs sm:mr-1 sm:max-w-[8rem] sm:px-2 sm:py-1 sm:text-sm",
          singers.length ? "bg-primary/10 font-medium text-foreground" : "text-muted-foreground/70 hover:text-foreground",
        )}
      >
        <MicVocal className="h-3 w-3 shrink-0 sm:h-3.5 sm:w-3.5" aria-hidden />
        {singers.length > 0 && <span className="truncate">{namesText(singers)}</span>}
      </button>
      <Dialog open={open} onOpenChange={(v) => !v && close(true)}>
        <DialogContent className={cn(THEME_CLASS, "w-[calc(100vw-2rem)] max-w-md gap-3 p-4 sm:p-5")}>
          <DialogHeader>
            <DialogTitle className="text-base">Who's singing?</DialogTitle>
            <DialogDescription className="text-xs sm:text-sm">
              {singers.length ? namesText(singers) : "Nobody yet"}
            </DialogDescription>
          </DialogHeader>
          <div className="rounded-xl px-3 py-2 ring-1 ring-stone-200/70">
            <NamePicker value={draft} onChange={setDraft} names={names} alwaysSuggest />
          </div>
          <div className="flex justify-end gap-2">
            {draft.length > 0 && (
              <Button variant="ghost" size="sm" onClick={() => setDraft([])}>
                Nobody
              </Button>
            )}
            <Button size="sm" onClick={() => close(true)}>
              Done
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
