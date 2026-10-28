// An image's tags as lozenges (owner, 27 Oct: "standard tag-lozenge UI"): the ★ favourite first, then the effective
// tags (docs/ART_TAGS.md §1), each with an × to take it out, and a "+ Tag" lozenge that opens a search over the
// lexicon. Favourites live in bucket.json, so the ★ goes through the existing favourite endpoint. Edits show at once
// and roll back if the server says no; every view holding the image hears about it (bucket:item-updated).
import { useEffect, useMemo, useRef, useState } from 'react';
import { Plus, Star, X } from 'lucide-react';
import { toast } from 'sonner';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from '@/components/ui/command';
import { bucketsApi, tagsApi } from '@/shared/api';
import type { TagLexicon, TaggingInfo } from '@/shared/api/types';
import { cn } from '@/lib/utils';
import { notifyItemUpdated } from './events';

export type TagVariant = 'light' | 'dark';

/** The Room page's TagPicker lozenges (the house pattern), and a dark twin for the loupe's neutral-900. */
export const LOZENGE: Record<TagVariant, { tag: string; fav: string; add: string; x: string; hint: string }> = {
  light: {
    tag: 'border-teal-300 bg-teal-50 text-teal-900',
    fav: 'border-amber-300 bg-amber-50 text-amber-900',
    add: 'border-dashed border-stone-300 bg-white text-stone-700 hover:bg-stone-50',
    x: 'hover:bg-white/70',
    hint: 'text-muted-foreground',
  },
  dark: {
    tag: 'border-teal-300/40 bg-teal-400/15 text-teal-50',
    fav: 'border-amber-300/50 bg-amber-400/20 text-amber-100',
    add: 'border-dashed border-white/30 bg-transparent text-white/80 hover:bg-white/10 hover:text-white',
    x: 'hover:bg-white/15',
    hint: 'text-white/60',
  },
};

const FACET_GROUPS: Array<{ key: 'subject' | 'medium' | 'moods'; heading: string }> = [
  { key: 'subject', heading: 'Subject' },
  { key: 'medium', heading: 'Medium' },
  { key: 'moods', heading: 'Mood' },
];

export function TagLozenges({
  bucket,
  filename,
  tags,
  tagging,
  favorite = false,
  allowFavorite = true,
  isVideo = false,
  variant = 'light',
  onTagsChange,
  onFavoriteChange,
  className,
  compact = false,
}: {
  bucket: string;
  filename: string;
  tags?: string[];
  tagging?: TaggingInfo;
  favorite?: boolean;
  /** False where a star makes no sense (e.g. a published copy). */
  allowFavorite?: boolean;
  /** Videos are not AI-tagged, so no "Tagging…" wait is promised for them. */
  isVideo?: boolean;
  variant?: TagVariant;
  onTagsChange?: (patch: { tags: string[]; tagging: TaggingInfo }) => void;
  onFavoriteChange?: (favorite: boolean) => void;
  className?: string;
  /** Smaller lozenges (the loupe, where a phone has little room under the picture). */
  compact?: boolean;
}) {
  const s = LOZENGE[variant];
  // Optimistic values while a write is in flight; null = show what the parent holds.
  const [tagsOverride, setTagsOverride] = useState<string[] | null>(null);
  const [favOverride, setFavOverride] = useState<boolean | null>(null);
  const [requeued, setRequeued] = useState(false);
  const pending = useRef(0);

  useEffect(() => {
    setTagsOverride(null);
    setFavOverride(null);
    setRequeued(false);
    pending.current = 0;
  }, [bucket, filename]);

  const shownTags = tagsOverride ?? (Array.isArray(tags) ? tags : []);
  const shownFav = favOverride ?? favorite;
  const status = tagging?.status;

  const editTags = async (change: { add?: string[]; remove?: string[] }) => {
    const before = shownTags;
    const next = before.filter((t) => !(change.remove ?? []).includes(t));
    for (const w of change.add ?? []) if (!next.includes(w)) next.push(w);
    setTagsOverride(next);
    pending.current += 1;
    try {
      const res = await tagsApi.editTags(bucket, filename, change);
      const patch = { tags: Array.isArray(res.tags) ? res.tags : next, tagging: res.tagging ?? tagging ?? {} };
      onTagsChange?.(patch);
      notifyItemUpdated({ bucket, filename, patch });
      pending.current -= 1;
      // A newer edit still in flight keeps its own optimistic list.
      if (pending.current === 0) setTagsOverride(null);
    } catch (e) {
      pending.current -= 1;
      setTagsOverride(pending.current === 0 ? null : before);
      toast.error(e instanceof Error ? e.message : 'Failed to change tags');
    }
  };

  const setFavourite = async (want: boolean) => {
    setFavOverride(want);
    try {
      const ok = await bucketsApi.toggleFavorite(bucket, filename, want);
      if (!ok) throw new Error('Failed to change favourite');
      onFavoriteChange?.(want);
      notifyItemUpdated({ bucket, filename, patch: { favorite: want } });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to change favourite');
    } finally {
      setFavOverride(null);
    }
  };

  const tryAgain = async () => {
    try {
      await tagsApi.retag(bucket, filename);
      setRequeued(true);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to queue tagging');
    }
  };

  const chip = compact
    ? 'inline-flex min-h-7 items-center gap-0.5 rounded-full border pl-2.5 pr-0.5 text-[13px] font-medium'
    : 'inline-flex min-h-8 items-center gap-1 rounded-full border pl-3 pr-0.5 text-sm font-semibold';
  const xBtn = compact ? 'h-6 w-6' : 'h-7 w-7';

  return (
    <div className={cn('flex flex-wrap items-center gap-1.5', className)}>
      {allowFavorite && shownFav && (
        <span className={cn(chip, s.fav)}>
          <Star className="h-3.5 w-3.5 fill-current" aria-hidden />
          favourite
          <button
            type="button"
            aria-label="Take out of favourites"
            onClick={() => setFavourite(false)}
            className={cn('grid place-items-center rounded-full', xBtn, s.x)}
          >
            <X className="h-3.5 w-3.5" aria-hidden />
          </button>
        </span>
      )}
      {shownTags.map((t) => (
        <span key={t} className={cn(chip, s.tag)}>
          {tagsApi.tagLabel(t)}
          <button
            type="button"
            aria-label={`Take out ${tagsApi.tagLabel(t)}`}
            onClick={() => editTags({ remove: [t] })}
            className={cn('grid place-items-center rounded-full', xBtn, s.x)}
          >
            <X className="h-3.5 w-3.5" aria-hidden />
          </button>
        </span>
      ))}
      <AddTag
        variant={variant}
        present={shownTags}
        offerFavourite={allowFavorite && !shownFav}
        onAdd={(w) => editTags({ add: [w] })}
        onFavourite={() => setFavourite(true)}
      />
      {!isVideo && (requeued || !status) && <span className={cn('text-xs italic', s.hint)}>Tagging…</span>}
      {!isVideo && !requeued && status === 'failed' && (
        <span className={cn('inline-flex items-center gap-1 text-xs', s.hint)} title={tagging?.error || undefined}>
          Not tagged ·
          <button type="button" onClick={tryAgain} className="min-h-7 px-1 underline underline-offset-2">
            Try again
          </button>
        </span>
      )}
    </div>
  );
}

/** "+ Tag": search the lexicon by facet, star it, or add any word typed. */
function AddTag({
  variant,
  present,
  offerFavourite,
  onAdd,
  onFavourite,
}: {
  variant: TagVariant;
  present: string[];
  offerFavourite: boolean;
  onAdd: (word: string) => void;
  onFavourite: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  const [lexicon, setLexicon] = useState<TagLexicon | null>(null);

  useEffect(() => {
    if (!open || lexicon) return;
    let live = true;
    tagsApi
      .getLexicon()
      .then((l) => live && setLexicon(l))
      .catch(() => {
        /* no lexicon: free words still work */
      });
    return () => {
      live = false;
    };
  }, [open, lexicon]);

  const query = q.trim().toLowerCase();
  const typed = tagsApi.normaliseTag(q);
  const groups = useMemo(() => {
    const taken = new Set(present);
    return FACET_GROUPS.map((g) => ({
      ...g,
      words: (lexicon?.facets?.[g.key] ?? []).filter(
        (w) =>
          !taken.has(w.word) &&
          (!query || w.word.includes(query.replace(/\s+/g, '-')) || tagsApi.tagLabel(w.word).includes(query)),
      ),
    })).filter((g) => g.words.length);
  }, [lexicon, present, query]);
  const inLexicon = groups.some((g) => g.words.some((w) => w.word === typed));
  const showFav = offerFavourite && (!query || 'favourite'.includes(query) || 'favorite'.includes(query) || query === '★');

  const pick = (fn: () => void) => {
    fn();
    setOpen(false);
    setQ('');
  };

  return (
    <Popover
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (!o) setQ('');
      }}
    >
      <PopoverTrigger asChild>
        <button
          type="button"
          className={cn('inline-flex min-h-8 items-center gap-1 rounded-full border px-3 text-sm', LOZENGE[variant].add)}
        >
          <Plus className="h-3.5 w-3.5" aria-hidden />
          Tag
        </button>
      </PopoverTrigger>
      <PopoverContent side="top" align="start" className="w-72 max-w-[calc(100vw-2rem)] p-0">
        <Command shouldFilter={false}>
          <CommandInput placeholder="Find or add a tag" value={q} onValueChange={setQ} className="text-base sm:text-sm" />
          <CommandList className="max-h-[40vh]">
            <CommandEmpty>{query ? 'Already tagged.' : 'Type a word to add it.'}</CommandEmpty>
            {showFav && (
              <CommandGroup>
                <CommandItem value="__favourite" onSelect={() => pick(onFavourite)} className="min-h-9">
                  <Star className="mr-2 h-4 w-4 fill-amber-400 text-amber-500" aria-hidden />
                  Favourite
                </CommandItem>
              </CommandGroup>
            )}
            {groups.map((g) => (
              <CommandGroup key={g.key} heading={g.heading}>
                {g.words.map((w) => (
                  <CommandItem key={w.word} value={`${g.key}:${w.word}`} onSelect={() => pick(() => onAdd(w.word))} className="min-h-9">
                    <span className="min-w-0">
                      <span className="block">{tagsApi.tagLabel(w.word)}</span>
                      {query && w.desc && <span className="block truncate text-xs text-muted-foreground">{w.desc}</span>}
                    </span>
                  </CommandItem>
                ))}
              </CommandGroup>
            ))}
            {typed && !present.includes(typed) && !inLexicon && (
              <CommandGroup>
                <CommandItem value={`__add:${typed}`} onSelect={() => pick(() => onAdd(typed))} className="min-h-9">
                  <Plus className="mr-2 h-4 w-4" aria-hidden />
                  Add “{tagsApi.tagLabel(typed)}”
                </CommandItem>
              </CommandGroup>
            )}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
