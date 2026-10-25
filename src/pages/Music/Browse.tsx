// Browse: the whole library. The search box up front (as you type, a quarter of a second after the last key; it says
// how many pieces there are), with Composer (a sheet: A–Z with counts and its own search) and the order beside it, then
// Pinned and the genres with how many pieces each has, on one line that scrolls sideways. Results 50 at a time, one line
// each (title · composer · performer, the duration), more as the list scrolls (and a More button). Tap a row to play
// it now; ⋯ for Play next, Add to the queue, Add to a playlist…, Pin. Counts come from the library and follow the
// other filters.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, Pin, UserRound, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { Spinner } from "@/pages/Room/bits";
import { ActionSheet } from "@/pages/Room/overlays";
import { TAP_BUTTON_CLASS } from "@/pages/Room/theme";
import { clock, musicWords, plural, type Facets, type MusicItem, type SortOrder } from "./api";
import { CHIP, CHIP_OFF, CHIP_ON, ListState, PIECE_LIST, PieceRow, PieceSheet, SearchBox, SECONDARY, SHEET_ROW, type PieceRef } from "./bits";
import { useMusic } from "./context";

const PAGE = 50;
const DEBOUNCE_MS = 250;

const SORTS: [SortOrder, string][] = [
  ["rank", "Best known first"],
  ["title", "Title A–Z"],
  ["composer", "Composer A–Z"],
  ["recent", "Newest first"],
];

/** Lower case, no accents: what the composer search compares. */
const fold = (s: string) =>
  s
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase();

/** Composers sort by surname ("Frédéric Chopin" under C); duos, bands and one-word names as written. */
function surnameKey(name: string): string {
  const n = name.trim();
  if (/[&,/+]|\band\b/i.test(n) || !/\s/.test(n)) return fold(n.replace(/^the\s+/i, ""));
  const parts = n.split(/\s+/).filter((p) => !/^(jr\.?|sr\.?|ii|iii|iv)$/i.test(p));
  return fold(`${parts[parts.length - 1]} ${parts.slice(0, -1).join(" ")}`);
}

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

function ComposerSheet({
  open,
  onOpenChange,
  composers,
  chosen,
  onPick,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  composers: Facets["composers"];
  chosen: string | null;
  onPick: (name: string | null) => void;
}) {
  const [q, setQ] = useState("");
  useEffect(() => {
    if (!open) setQ("");
  }, [open]);
  const groups = useMemo(() => {
    const f = fold(q.trim());
    const rows = composers
      .filter((c) => c.name && (!f || fold(c.name).includes(f)))
      .map((c) => ({ ...c, key: surnameKey(c.name) }))
      .sort((a, b) => a.key.localeCompare(b.key));
    const out: { letter: string; rows: typeof rows }[] = [];
    for (const r of rows) {
      const ch = r.key.charAt(0).toUpperCase();
      const letter = /[A-Z]/.test(ch) ? ch : "#";
      if (!out.length || out[out.length - 1].letter !== letter) out.push({ letter, rows: [] });
      out[out.length - 1].rows.push(r);
    }
    return out;
  }, [composers, q]);
  return (
    <ActionSheet open={open} onOpenChange={onOpenChange} title="Composer" description="Sorted by surname, with how many pieces each has." testId="composer-sheet">
      <div className="space-y-2 pb-1">
        <SearchBox value={q} onChange={setQ} placeholder="Find a composer" label="Find a composer" />
        <button type="button" className={cn(SHEET_ROW, !chosen && "bg-teal-50 text-teal-900")} onClick={() => onPick(null)}>
          Any composer
        </button>
        {groups.length === 0 && <p className="px-3 py-2 text-base text-muted-foreground">No composer matches “{q.trim()}”.</p>}
        {groups.map((g) => (
          <div key={g.letter}>
            <p className="sticky top-[var(--app-top)] z-10 bg-background px-3 pb-1 pt-2 text-sm font-semibold text-muted-foreground">{g.letter}</p>
            {g.rows.map((c) => (
              <button
                key={c.name}
                type="button"
                className={cn(SHEET_ROW, c.name === chosen && "bg-teal-50 text-teal-900")}
                onClick={() => onPick(c.name)}
              >
                <span className="min-w-0 flex-1 truncate">{c.name}</span>
                <span className="shrink-0 tabular-nums text-sm text-muted-foreground">{c.count}</span>
              </button>
            ))}
          </div>
        ))}
      </div>
    </ActionSheet>
  );
}

export function Browse() {
  const { api, libraryRev, pinnedOf } = useMusic();
  const [q, setQ] = useState("");
  const dq = useDebounced(q, DEBOUNCE_MS);
  const [genre, setGenre] = useState<string | null>(null);
  const [composer, setComposer] = useState<string | null>(null);
  const [pinned, setPinned] = useState(false);
  const [sort, setSort] = useState<SortOrder>("rank");
  const [items, setItems] = useState<MusicItem[] | null>(null);
  const [total, setTotal] = useState(0);
  const [facets, setFacets] = useState<Facets | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sheet, setSheet] = useState<PieceRef | null>(null);
  const [composerOpen, setComposerOpen] = useState(false);
  const seq = useRef(0);
  const filters = useMemo(() => ({ q: dq, genre, composer, pinned, sort }), [dq, genre, composer, pinned, sort]);

  // A new search: from the top.
  useEffect(() => {
    const n = ++seq.current;
    const ac = new AbortController();
    setLoading(true);
    api
      .search({ ...filters, limit: PAGE, offset: 0 }, ac.signal)
      .then((r) => {
        if (n !== seq.current) return;
        setItems(Array.isArray(r?.items) ? r.items : []);
        setTotal(typeof r?.total === "number" ? r.total : (r?.items?.length ?? 0));
        if (r?.facets) setFacets(r.facets);
        setError(null);
      })
      .catch((e) => {
        if (n !== seq.current || (e instanceof DOMException && e.name === "AbortError")) return;
        setError(musicWords(e));
      })
      .finally(() => {
        if (n === seq.current) setLoading(false);
      });
    return () => ac.abort();
  }, [api, filters, libraryRev]);

  const more = useCallback(() => {
    if (loading || !items || items.length >= total) return;
    const n = ++seq.current;
    setLoading(true);
    api
      .search({ ...filters, limit: PAGE, offset: items.length })
      .then((r) => {
        if (n !== seq.current) return;
        const seen = new Set(items.map((x) => x.id));
        const fresh = (r?.items ?? []).filter((x) => !seen.has(x.id));
        setItems([...items, ...fresh]);
        // a page with nothing new is the end, whatever the total said (so "more" never loops)
        setTotal(fresh.length ? (typeof r?.total === "number" ? r.total : total) : items.length);
        setError(null);
      })
      .catch((e) => {
        if (n === seq.current) setError(musicWords(e));
      })
      .finally(() => {
        if (n === seq.current) setLoading(false);
      });
  }, [api, filters, items, loading, total]);

  // More as the end of the list comes into view.
  const sentinel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = sentinel.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver((es) => es.some((e) => e.isIntersecting) && more(), { rootMargin: "400px 0px" });
    io.observe(el);
    return () => io.disconnect();
  }, [more]);

  const genres = (facets?.genres ?? []).filter((g) => g.count > 0 || g.id === genre);
  const shown = (items ?? []).filter((it) => !pinned || pinnedOf(it.id, it.pinned));
  const anyFilter = !!(dq.trim() || genre || composer || pinned);
  const clearAll = () => {
    setQ("");
    setGenre(null);
    setComposer(null);
    setPinned(false);
  };

  return (
    <div className="space-y-2" data-testid="music-browse">
      {/* the search with Composer and the order: one row from 640 px, two on a phone */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex min-w-0 basis-full sm:basis-0 sm:flex-1">
          <SearchBox
            value={q}
            onChange={setQ}
            placeholder={total > 0 && !anyFilter ? `Search ${plural(total, "piece")}` : "Search by title, composer or style"}
            label="Search the library"
          />
        </div>
        {composer ? (
          <span className={cn(CHIP, CHIP_ON, "max-w-[14rem] pr-1")}>
            <button type="button" onClick={() => setComposerOpen(true)} className="inline-flex min-w-0 items-center gap-1.5">
              <UserRound className="h-4 w-4 shrink-0" aria-hidden /> <span className="truncate">{composer}</span>
            </button>
            <button type="button" aria-label="Any composer" onClick={() => setComposer(null)} className="grid h-8 w-8 shrink-0 place-items-center rounded-full hover:bg-teal-100">
              <X className="h-4 w-4" aria-hidden />
            </button>
          </span>
        ) : (
          <button type="button" onClick={() => setComposerOpen(true)} className={cn(CHIP, CHIP_OFF)}>
            <UserRound className="h-4 w-4" aria-hidden /> Composer <ChevronDown className="h-4 w-4" aria-hidden />
          </button>
        )}
        <label className="ml-auto inline-flex items-center sm:ml-0">
          <span className="sr-only">Order</span>
          <select
            value={sort}
            onChange={(e) => setSort(e.target.value as SortOrder)}
            className="min-h-10 rounded-full border border-stone-200 bg-white px-3 text-[15px] font-medium text-stone-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {SORTS.map(([id, w]) => (
              <option key={id} value={id}>
                {w}
              </option>
            ))}
          </select>
        </label>
      </div>

      {/* Pinned and the genres: one line that scrolls sideways */}
      <div
        className="-mx-4 flex gap-2 overflow-x-auto px-4 [scrollbar-width:none] sm:-mx-6 sm:px-6 lg:mx-0 lg:px-0 [&::-webkit-scrollbar]:hidden"
        role="group"
        aria-label="Genre"
      >
        <button type="button" aria-pressed={pinned} onClick={() => setPinned(!pinned)} className={cn(CHIP, "shrink-0", pinned ? CHIP_ON : CHIP_OFF)}>
          <Pin className={cn("h-4 w-4", pinned && "fill-teal-700")} aria-hidden /> Pinned
        </button>
        <span aria-hidden className="my-2 w-px shrink-0 bg-stone-200" />
        <button type="button" aria-pressed={!genre} onClick={() => setGenre(null)} className={cn(CHIP, "shrink-0", !genre ? CHIP_ON : CHIP_OFF)}>
          All
        </button>
        {genres.map((g) => (
          <button
            key={g.id}
            type="button"
            aria-pressed={genre === g.id}
            onClick={() => setGenre(genre === g.id ? null : g.id)}
            className={cn(CHIP, "shrink-0 whitespace-nowrap", genre === g.id ? CHIP_ON : CHIP_OFF)}
          >
            {g.label}
            <span className="tabular-nums text-sm font-normal text-muted-foreground">{g.count}</span>
          </button>
        ))}
      </div>

      {/* how many, only once a filter narrows it (unfiltered, the search box says it) */}
      {items && !error && (anyFilter || total === 0 || loading) && (
        <p className="flex min-h-6 items-center gap-2 text-[15px] text-muted-foreground" aria-live="polite">
          {loading && <Spinner />}
          {total > 0 ? plural(total, "piece") : "Nothing matches."}
          {anyFilter && (
            <button type="button" onClick={clearAll} className="font-medium text-teal-800 underline underline-offset-2">
              Clear
            </button>
          )}
        </p>
      )}

      <ListState
        loading={loading}
        error={error}
        empty={!items}
        emptyWords={null}
      />
      {pinned && items && shown.length === 0 && !loading && (
        <p className="text-base text-muted-foreground">Nothing pinned{genre || composer || dq.trim() ? " that matches" : " yet"}. Pin a piece with ⋯ to find it here quickly.</p>
      )}

      {shown.length > 0 && (
        <ul className={PIECE_LIST} aria-label="Pieces">
          {shown.map((it) => (
            <PieceRow
              key={it.id}
              joined
              piece={it}
              line={[it.composer, it.performer, genre ? null : it.genre_label].filter(Boolean).join(" · ")}
              meta={clock(it.duration_s)}
              onMore={() => setSheet(it)}
              testId="piece-row"
            />
          ))}
        </ul>
      )}
      <div ref={sentinel} aria-hidden />
      {items && items.length < total && (
        <button type="button" onClick={more} className={cn(TAP_BUTTON_CLASS, SECONDARY, "inline-flex w-full items-center justify-center gap-2 font-medium")}>
          {loading && <Spinner />} More ({total - items.length} to go)
        </button>
      )}

      <PieceSheet piece={sheet} onClose={() => setSheet(null)} />
      <ComposerSheet
        open={composerOpen}
        onOpenChange={setComposerOpen}
        composers={facets?.composers ?? []}
        chosen={composer}
        onPick={(name) => {
          setComposer(name);
          setComposerOpen(false);
        }}
      />
    </div>
  );
}
