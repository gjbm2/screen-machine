// Famous artworks to use as reference pictures (owner, 19 Nov): put a photo into a historic picture, or make one in
// its style. Search by anything (artist, title, nickname, subject, movement, century) with type-ahead; narrow by
// period, kind of picture, or pictures with people in them; tap up to three and use them. The pictures (up to
// 3840 px, under 5 MB) come from output/library/ (tools/artworks/fetch.py; it is the Library bucket too) and go
// to the form like an upload.
import { useEffect, useMemo, useRef, useState } from 'react';
import { Check, Loader2, Search, Users, X } from 'lucide-react';
import { toast } from 'sonner';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { cn } from '@/lib/utils';
import { indexLibrary, searchArtworks, suggest, type Artwork, type Library } from './search';

const LIBRARY_URL = '/output/library/library.json';
const MAX_PICKS = 3;

const GENRE_WORDS: Record<string, string> = {
  portrait: 'Portraits', landscape: 'Landscapes', 'genre scene': 'Everyday life', religious: 'Religious',
  history: 'History', mythology: 'Myths', cityscape: 'Cities', seascape: 'The sea', allegory: 'Allegories',
  animals: 'Animals', 'still life': 'Still lifes', interior: 'Interiors', abstract: 'Abstract', fantasy: 'Fantasy',
  nude: 'Nudes', poster: 'Posters',
};

let libraryPromise: Promise<Library> | null = null;
function loadLibrary(): Promise<Library> {
  libraryPromise ??= fetch(LIBRARY_URL).then((r) => {
    if (!r.ok) throw new Error(`library ${r.status}`);
    return r.json() as Promise<Library>;
  });
  libraryPromise.catch(() => { libraryPromise = null; });   // try again next time it opens
  return libraryPromise;
}

const yearText = (y: number) => (y < 0 ? `${-y} BC` : `${y}`);

function Chip({ on, onClick, children }: { on: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'shrink-0 rounded-full border px-3 py-1 text-[13px] transition-colors',
        on ? 'border-teal-700 bg-teal-700 text-white' : 'border-stone-200 bg-white text-stone-700 hover:bg-stone-50',
      )}
    >
      {children}
    </button>
  );
}

export function ArtworkPicker({ open, onOpenChange, onPick }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** the chosen pictures, as files (like an upload) */
  onPick: (files: File[]) => void;
}) {
  const [library, setLibrary] = useState<Library | null>(null);
  const [failed, setFailed] = useState(false);
  const [query, setQuery] = useState('');
  const [period, setPeriod] = useState<string | null>(null);
  const [genre, setGenre] = useState<string | null>(null);
  const [people, setPeople] = useState(false);
  const [picked, setPicked] = useState<string[]>([]);
  const [typing, setTyping] = useState(false);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open || library) return;
    setFailed(false);
    loadLibrary().then(setLibrary).catch(() => setFailed(true));
  }, [open, library]);

  useEffect(() => { if (!open) { setPicked([]); setTyping(false); } }, [open]);

  const index = useMemo(() => (library ? indexLibrary(library.works) : []), [library]);
  const results = useMemo(() => searchArtworks(index, query, { period, genre, people }), [index, query, period, genre, people]);
  const suggestions = useMemo(() => (library && typing ? suggest(library.works, query) : []), [library, query, typing]);
  const genres = useMemo(() => {
    const count = new Map<string, number>();
    library?.works.forEach((w) => {
      const g = w.genre === 'self-portrait' ? 'portrait' : w.genre;
      count.set(g, (count.get(g) ?? 0) + 1);
    });
    return [...count.entries()].sort((a, b) => b[1] - a[1]).map(([g]) => g);
  }, [library]);
  const byId = useMemo(() => new Map(library?.works.map((w) => [w.id, w]) ?? []), [library]);

  const toggle = (id: string) =>
    setPicked((prev) => {
      if (prev.includes(id)) return prev.filter((p) => p !== id);
      if (prev.length >= MAX_PICKS) {
        toast.info(`Up to ${MAX_PICKS} pictures`);
        return prev;
      }
      return [...prev, id];
    });

  const use = async () => {
    setBusy(true);
    try {
      const files = await Promise.all(picked.map(async (id) => {
        const w = byId.get(id)!;
        const r = await fetch(w.image);
        if (!r.ok) throw new Error(`${w.title}: ${r.status}`);
        const blob = await r.blob();
        return new File([blob], `${w.artist} - ${w.title}.jpg`.replace(/[\\/:*?"<>|]/g, ''), { type: 'image/jpeg' });
      }));
      onPick(files);
      onOpenChange(false);
    } catch (e) {
      toast.error(`Couldn't load the picture${picked.length > 1 ? 's' : ''}`);
      console.error('ArtworkPicker:', e);
    } finally {
      setBusy(false);
    }
  };

  const filtersOn = !!(query || period || genre || people);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex h-[100dvh] max-h-[100dvh] w-full max-w-5xl flex-col gap-3 p-4 sm:h-[90vh] sm:rounded-2xl" data-testid="artwork-picker">
        <DialogHeader>
          <DialogTitle>Famous artworks</DialogTitle>
          <DialogDescription>Put a photo into one of these, or make something in its style.</DialogDescription>
        </DialogHeader>

        {/* search with type-ahead */}
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-stone-400" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => { setQuery(e.target.value); setTyping(true); }}
            onFocus={() => setTyping(true)}
            onBlur={() => setTimeout(() => setTyping(false), 150)}
            onKeyDown={(e) => { if (e.key === 'Escape' && typing && suggestions.length) { e.stopPropagation(); setTyping(false); } }}
            placeholder="Vermeer, Mona Lisa, Japanese, 17th century…"
            className="h-11 w-full rounded-xl border border-stone-200 bg-white pl-9 pr-9 text-[15px] focus:outline-none focus:ring-2 focus:ring-teal-600"
            data-testid="artwork-search"
          />
          {query && (
            <button type="button" aria-label="Clear" onClick={() => { setQuery(''); inputRef.current?.focus(); }}
              className="absolute right-2 top-1/2 -translate-y-1/2 rounded-full p-1 text-stone-400 hover:text-stone-700">
              <X className="h-4 w-4" />
            </button>
          )}
          {suggestions.length > 0 && (
            <ul className="absolute z-50 mt-1 w-full overflow-hidden rounded-xl border border-stone-200 bg-white shadow-lg">
              {suggestions.map((s) => (
                <li key={`${s.kind}:${s.text}`}>
                  <button
                    type="button"
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => { setQuery(s.text); setTyping(false); }}
                    className="flex w-full items-center justify-between px-3 py-2 text-left text-[14px] hover:bg-stone-50"
                  >
                    <span className="truncate">{s.text}</span>
                    <span className="ml-3 shrink-0 text-[12px] text-stone-400">{s.kind}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* period, kind, people */}
        {library && (
          <div className="space-y-2">
            <div className="flex gap-2 overflow-x-auto pb-1">
              <Chip on={!period} onClick={() => setPeriod(null)}>All periods</Chip>
              {library.periods.filter((p) => library.works.some((w) => w.period === p)).map((p) => (
                <Chip key={p} on={period === p} onClick={() => setPeriod(period === p ? null : p)}>{p}</Chip>
              ))}
            </div>
            <div className="flex gap-2 overflow-x-auto pb-1">
              <Chip on={people} onClick={() => setPeople(!people)}>
                <span className="inline-flex items-center gap-1"><Users className="h-3.5 w-3.5" />With people</span>
              </Chip>
              {genres.map((g) => (
                <Chip key={g} on={genre === g} onClick={() => setGenre(genre === g ? null : g)}>{GENRE_WORDS[g] ?? g}</Chip>
              ))}
            </div>
          </div>
        )}

        {/* the pictures */}
        <div className="min-h-0 flex-1 overflow-y-auto">
          {failed ? (
            <p className="py-10 text-center text-stone-500">The artworks library isn't available yet.</p>
          ) : !library ? (
            <div className="flex justify-center py-10"><Loader2 className="h-6 w-6 animate-spin text-stone-400" /></div>
          ) : results.length === 0 ? (
            <div className="py-10 text-center text-stone-500">
              Nothing matches.{' '}
              {filtersOn && (
                <button type="button" className="text-teal-700 underline"
                  onClick={() => { setQuery(''); setPeriod(null); setGenre(null); setPeople(false); }}>
                  Show everything
                </button>
              )}
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5">
              {results.map((w) => {
                const n = picked.indexOf(w.id);
                return (
                  <button
                    key={w.id}
                    type="button"
                    onClick={() => toggle(w.id)}
                    className={cn('group rounded-xl border p-1.5 text-left transition-colors',
                      n >= 0 ? 'border-teal-700 bg-teal-50' : 'border-transparent hover:bg-stone-50')}
                    title={`${w.title}, ${w.artist}, ${yearText(w.year)}`}
                    data-testid="artwork-tile"
                  >
                    <div className="relative flex aspect-square items-center justify-center overflow-hidden rounded-lg bg-stone-100">
                      <img src={w.thumb} alt={w.title} loading="lazy" className="max-h-full max-w-full object-contain" />
                      {n >= 0 && (
                        <span className="absolute right-1.5 top-1.5 flex h-6 w-6 items-center justify-center rounded-full bg-teal-700 text-white">
                          {picked.length > 1 ? <span className="text-[12px] font-semibold">{n + 1}</span> : <Check className="h-4 w-4" />}
                        </span>
                      )}
                    </div>
                    <div className="mt-1.5 truncate text-[13px] font-medium text-stone-900">{w.title}</div>
                    <div className="truncate text-[12px] text-stone-500">{w.artist} · {yearText(w.year)}</div>
                  </button>
                );
              })}
            </div>
          )}
        </div>

        {/* use them */}
        <div className="flex items-center justify-between gap-3 border-t border-stone-100 pt-3">
          <span className="truncate text-[13px] text-stone-500">
            {picked.length
              ? picked.map((id) => byId.get(id)?.title).join(', ')
              : library ? `${results.length} of ${library.works.length} pictures` : ''}
          </span>
          <button
            type="button"
            disabled={!picked.length || busy}
            onClick={use}
            className="inline-flex h-10 shrink-0 items-center gap-2 rounded-xl bg-teal-700 px-5 text-[15px] font-semibold text-white disabled:opacity-40"
            data-testid="artwork-use"
          >
            {busy && <Loader2 className="h-4 w-4 animate-spin" />}
            {picked.length > 1 ? `Use ${picked.length} pictures` : 'Use picture'}
          </button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
