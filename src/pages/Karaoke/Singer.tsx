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

import { useEffect, useRef, useState, useSyncExternalStore, type PointerEvent } from "react";
import { Camera, Images, ListMusic, Loader2, MicVocal, Plus, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { karaoke, type SingerRiff } from "@/shared/api/karaoke";
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

// ---- the singers' own photos (owner, 28 Nov: "optionally for singers to upload photo (e.g. from phone camera),
// exposed ... by e.g. clicking their lozenge"; the screens show them, and the photo scenes: routes/karaoke/singer_photos.py).
// Every name's photo URL, from /state (setPhotos) or an upload's reply, shared by every chip on the page.
let photoMap: Record<string, string> = {};
let riffMap: Record<string, SingerRiff[]> = {};
const photoSubs = new Set<() => void>();
/** The photos (and, from /state, the riffs on them: singer_riffs.py) every chip shows. */
export function setPhotos(m: Record<string, string> | undefined, riffs?: Record<string, SingerRiff[]>) {
  const next = m ?? {};
  const nextRiffs = riffs ?? riffMap;
  if (JSON.stringify([next, nextRiffs]) === JSON.stringify([photoMap, riffMap])) return;
  photoMap = next;
  riffMap = nextRiffs;
  photoSubs.forEach((f) => f());
}
const photoOf = (m: Record<string, string>, n: string) =>
  m[n] ?? Object.entries(m).find(([k]) => k.toLowerCase() === n.toLowerCase())?.[1];
const subscribe = (f: () => void) => (photoSubs.add(f), () => photoSubs.delete(f));
function usePhotos() {
  return useSyncExternalStore(subscribe, () => photoMap);
}
function useRiffs() {
  return useSyncExternalStore(subscribe, () => riffMap);
}

const PHOTO_PX = 1600; // as the server keeps it: a phone's 12 MP photo goes up as ~300 kB, not 5 MB

/** The picked file, upright (the browser reads its orientation) and at most PHOTO_PX, as a JPEG; else the file. */
async function shrink(file: File): Promise<Blob> {
  try {
    const bmp = await createImageBitmap(file, { imageOrientation: "from-image" });
    const k = Math.min(1, PHOTO_PX / Math.max(bmp.width, bmp.height));
    const c = document.createElement("canvas");
    c.width = Math.round(bmp.width * k);
    c.height = Math.round(bmp.height * k);
    c.getContext("2d")!.drawImage(bmp, 0, 0, c.width, c.height);
    bmp.close();
    const b = await new Promise<Blob | null>((ok) => c.toBlob(ok, "image/jpeg", 0.88));
    return b ?? file;
  } catch {
    return file;
  }
}

/** One name as a chip: its photo (or a small camera) and the name; tapping it takes or picks a photo (the phone offers
 *  its camera or its gallery), or with one already, a new one or none. × takes the name off. */
function NameChip({
  name,
  onRemove,
  keep,
  onShow,
}: {
  name: string;
  onRemove: () => void;
  keep: (e: PointerEvent) => void;
  /** Their songs only in the queue (Singers' chips): then a tap always opens the chip's menu. */
  onShow?: (name: string) => void;
}) {
  const photo = photoOf(usePhotos(), name);
  const riffMapNow = useRiffs();
  const riffs = riffMapNow[name] ?? Object.entries(riffMapNow).find(([k]) => k.toLowerCase() === name.toLowerCase())?.[1];
  // the riffs once the generator has started on them (queued only: no generator yet, nothing to show)
  const hasRiffs = !!riffs?.some((r) => r.state === "done" || r.state === "making");
  const [gallery, setGallery] = useState(false);
  const file = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const pick = () => file.current?.click();
  const send = async (f: File | undefined) => {
    if (!f) return;
    setBusy(true);
    setFailed(false);
    try {
      setPhotos((await karaoke.singerPhoto(name, await shrink(f))).photos);
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  };
  const face = busy ? (
    <Loader2 className="h-3 w-3 animate-spin sm:h-3.5 sm:w-3.5" />
  ) : photo ? (
    <img src={photo} alt="" className="h-5 w-5 rounded-full object-cover sm:h-6 sm:w-6" />
  ) : (
    <Camera className={cn("h-3 w-3 sm:h-3.5 sm:w-3.5", failed ? "text-red-600" : "opacity-50")} />
  );
  const label = (
    <>
      {face}
      <span className="truncate">{name}</span>
    </>
  );
  const btn = "flex min-w-0 items-center gap-1 rounded-full outline-none";
  return (
    <span
      className={cn(
        "flex max-w-[10rem] items-center gap-0.5 rounded-full bg-primary/10 py-0.5 pr-0.5 text-xs font-medium sm:text-sm",
        photo && !busy ? "pl-0.5" : "pl-1.5",
      )}
    >
      <input
        ref={file}
        type="file"
        accept="image/*"
        hidden
        onChange={(e) => {
          void send(e.target.files?.[0]);
          e.target.value = "";
        }}
      />
      {photo || onShow ? (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button type="button" className={btn} aria-label={onShow ? name : `${name}'s photo`}>
              {label}
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className={THEME_CLASS} onCloseAutoFocus={(e) => e.preventDefault()}>
            {onShow && (
              <DropdownMenuItem onSelect={() => onShow(name)}>
                <ListMusic className="mr-2 h-4 w-4" /> Their songs
              </DropdownMenuItem>
            )}
            {photo && hasRiffs && (
              <DropdownMenuItem onSelect={() => setGallery(true)}>
                <Images className="mr-2 h-4 w-4" /> Riffs · {riffs!.filter((r) => r.state === "done").length}
              </DropdownMenuItem>
            )}
            <DropdownMenuItem onSelect={pick}>
              <Camera className="mr-2 h-4 w-4" /> {photo ? "New photo" : "Add photo"}
            </DropdownMenuItem>
            {photo && (
              <DropdownMenuItem
                onSelect={async () => setPhotos((await karaoke.dropSingerPhoto(name).catch(() => null))?.photos ?? photoMap)}
              >
                <X className="mr-2 h-4 w-4" /> Remove photo
              </DropdownMenuItem>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      ) : (
        <button type="button" className={btn} onClick={pick} aria-label={`A photo of ${name}`}>
          {label}
        </button>
      )}
      {photo && riffs && <RiffGallery open={gallery} onOpenChange={setGallery} name={name} photo={photo} riffs={riffs} />}
      <button
        type="button"
        aria-label={`Not ${name}`}
        onPointerDown={keep}
        onClick={onRemove}
        className="rounded-full p-0.5 text-muted-foreground hover:text-foreground"
      >
        <X className="h-3 w-3 sm:h-3.5 sm:w-3.5" />
      </button>
    </span>
  );
}

/** A singer's photo and the AI's riffs on it, three across; one tapped fills the dialog (tapped again: back). */
function RiffGallery({
  open,
  onOpenChange,
  name,
  photo,
  riffs,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  name: string;
  photo: string;
  riffs: SingerRiff[];
}) {
  const [big, setBig] = useState<string | null>(null);
  const tiles = [{ label: "The photo", url: photo, state: "done" }, ...riffs.filter((r) => r.state !== "failed")];
  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        setBig(null);
        onOpenChange(v);
      }}
    >
      <DialogContent
        className={cn(THEME_CLASS, "w-[calc(100vw-2rem)] max-w-lg gap-3 p-3 sm:p-4")}
        onOpenAutoFocus={(e) => e.preventDefault()}
      >
        <DialogHeader>
          <DialogTitle className="text-base">{name}</DialogTitle>
          <DialogDescription className="sr-only">{name}'s photo and riffs</DialogDescription>
        </DialogHeader>
        {big ? (
          <button type="button" onClick={() => setBig(null)} className="block outline-none">
            <img src={big} alt="" className="max-h-[70vh] w-full rounded-xl object-contain" />
          </button>
        ) : (
          <div className="grid grid-cols-3 gap-2">
            {tiles.map((r, i) => (
              <div key={i} className="overflow-hidden rounded-xl bg-stone-100">
                {r.url ? (
                  <button type="button" onClick={() => setBig(r.url!)} className="block w-full outline-none">
                    <img src={r.url} alt="" className="aspect-square w-full object-cover" />
                  </button>
                ) : (
                  <div className="flex aspect-square w-full items-center justify-center text-muted-foreground">
                    <Loader2 className={cn("h-5 w-5", r.state === "making" && "animate-spin")} />
                  </div>
                )}
                <div className="truncate px-2 py-1 text-xs text-muted-foreground">{r.label}</div>
              </div>
            ))}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

function NamePicker({
  value,
  onChange,
  names,
  alwaysSuggest = false,
  autoFocus = false,
  max = SUGGEST,
  empty = "Name (optional)",
  onShow,
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
  /** A chip's "Their songs" (the Singers card only). */
  onShow?: (name: string) => void;
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
          <NameChip key={n} name={n} keep={keep} onShow={onShow} onRemove={() => onChange(value.filter((x) => x !== n))} />
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
          // type="search", not text: on Android, Chromium puts its autofill bar (passwords, cards, addresses) over the
          // keyboard for a plain text box and never reads autoComplete for it; a search field doesn't get it
          // (owner, 28 Nov: "a pointless list of options like key, credit card"). Its looks stay a plain box's.
          type="search"
          autoComplete="off"
          autoCapitalize="words"
          enterKeyHint="done"
          className="min-w-[5.5rem] flex-1 appearance-none bg-transparent py-0.5 text-xs font-medium outline-none placeholder:font-normal placeholder:text-muted-foreground/70 sm:text-sm [&::-webkit-search-cancel-button]:hidden [&::-webkit-search-decoration]:hidden"
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
  onShow,
}: {
  names: string[];
  onChange: (add: string[], remove: string[]) => void;
  /** A name's songs only in the queue. */
  onShow?: (name: string) => void;
}) {
  return (
    <div className="rounded-xl px-3 py-2 ring-1 ring-stone-200/70">
      <NamePicker
        value={names}
        names={[]}
        empty="Add a name"
        onShow={onShow}
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
  singerFor ? Promise.resolve([singerFor]) : asker ? asker(title) : Promise.resolve(singersNow());

// The queue shows one singer's songs (Their songs): a song added meanwhile is theirs, unasked (owner, 28 Nov: "if I
// add a song while filtered for a person then they should be singer by default ... no need to show the ... modal").
let singerFor: string | null = null;
export const setSingerFor = (n: string | null) => {
  singerFor = n;
};

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
