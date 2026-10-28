// "All buckets": every saved picture from every bucket in one place, browsed by tag (owner, 28 Oct: "a virtual
// 'All buckets' bucket on the right hand side of buckets so that user can tab filter across all stored images").
// Read-mostly: open a picture in the loupe (tags and the star work there, per picture's own bucket), star from the
// card. Copy, move, delete and reordering stay in each bucket's own tab.
import { useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';
import { bucketsApi, tagsApi } from '@/shared/api';
import type { TagLexicon } from '@/shared/api';
import ImageCard from '@/components/common/ImageCard';
import { ExpandableContainer } from '@/components/common';
import { useLoopeView } from '@/contexts/LoopeViewContext';
import type { ImageItem } from '@/types/image-types';
import { isVideoFile } from '@/utils/image-utils';
import { cn } from '@/lib/utils';
import { TagFilterBar } from '@/components/tags/TagFilterBar';
import { ITEM_UPDATED_EVENT, type ItemUpdatedDetail } from '@/components/tags/events';
import {
  buildChips,
  facetIndex,
  favouritesThenNewest,
  matchesChips,
  sectionsBySubject,
  type TaggableImage,
} from '@/components/tags/tagIndex';

interface AllImage extends TaggableImage {
  key: string;            // bucket/filename: unique across buckets
  bucketId: string;
  bucketLabel: string;
  url: string;
  thumbnail_url?: string;
  raw_url?: string;
  prompt?: string;
  reference_images?: any[];
}

/** How many cards a section shows before "Show more" (all buckets together are ~2,000 pictures). */
const PAGE = 48;

const CHIP = 'inline-flex min-h-9 shrink-0 items-center gap-1 rounded-full border px-3 text-[15px] transition-colors';
const CHIP_ON = 'border-teal-700 bg-teal-700 font-semibold text-white shadow-sm hover:bg-teal-800';
const CHIP_OFF = 'border-stone-200 bg-white hover:bg-stone-50';

export function AllBucketsView({ buckets }: { buckets: Array<{ id: string; label: string }> }) {
  const [images, setImages] = useState<AllImage[]>([]);
  const [loading, setLoading] = useState(true);
  const [lexicon, setLexicon] = useState<TagLexicon | null>(null);
  const [chosen, setChosen] = useState<string[]>([]);
  const [chosenBuckets, setChosenBuckets] = useState<string[]>([]);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [shown, setShown] = useState<Record<string, number>>({});
  const { open: openLoope } = useLoopeView();
  const bucketKey = buckets.map((b) => b.id).join('|');

  useEffect(() => {
    let live = true;
    setLoading(true);
    Promise.all(
      buckets.map(async (b) => {
        const d = await bucketsApi.getBucketDetails(b.id);
        return (d.items || []).map(
          (item: any): AllImage => ({
            key: `${b.id}/${item.filename}`,
            id: item.filename,
            bucketId: b.id,
            bucketLabel: b.label,
            url: item.url || '',
            thumbnail_url: item.thumbnail_url,
            raw_url: item.raw_url || item.url,
            prompt: item.metadata?.prompt,
            reference_images: item.reference_images || [],
            metadata: { ...item.metadata, favorite: item.favorite },
            created_at: item.created_at || item.metadata?.timestamp,
          }),
        );
      }),
    )
      .then((lists) => live && setImages(lists.flat()))
      .catch(() => live && toast.error('Could not read the buckets'))
      .finally(() => live && setLoading(false));
    tagsApi.getLexicon().then((l) => live && setLexicon(l)).catch(() => {});
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bucketKey]);

  // The loupe changed a picture's tags or star: patch it here too
  useEffect(() => {
    const onItemUpdated = (e: Event) => {
      const d = (e as CustomEvent<ItemUpdatedDetail>).detail;
      if (!d) return;
      setImages((prev) =>
        prev.map((img) =>
          img.bucketId !== d.bucket || img.id !== d.filename
            ? img
            : 'favorite' in d.patch
              ? { ...img, metadata: { ...img.metadata, favorite: d.patch.favorite } }
              : { ...img, metadata: { ...img.metadata, tags: d.patch.tags, tagging: d.patch.tagging } },
        ),
      );
    };
    window.addEventListener(ITEM_UPDATED_EVENT, onItemUpdated);
    return () => window.removeEventListener(ITEM_UPDATED_EVENT, onItemUpdated);
  }, []);

  const facets = useMemo(() => facetIndex(lexicon), [lexicon]);
  const inBuckets = useMemo(
    () => (chosenBuckets.length ? images.filter((i) => chosenBuckets.includes(i.bucketId)) : images),
    [images, chosenBuckets],
  );
  const chips = useMemo(() => buildChips(inBuckets, facets), [inBuckets, facets]);
  const sections = useMemo(() => {
    if (chosen.length) {
      const matching = inBuckets.filter((i) => matchesChips(i, chosen)).sort(favouritesThenNewest);
      return [{ key: '__chosen', label: `${matching.length} pictures`, images: matching }];
    }
    return sectionsBySubject(inBuckets, facets);
  }, [inBuckets, chosen, facets]);

  const bucketCounts = useMemo(() => {
    const c = new Map<string, number>();
    for (const i of images) c.set(i.bucketId, (c.get(i.bucketId) ?? 0) + 1);
    return c;
  }, [images]);

  const toItem = (img: AllImage): ImageItem => ({
    id: img.id,
    urlThumb: img.thumbnail_url || img.url,
    urlFull: img.url,
    promptKey: img.prompt || '',
    seed: 0,
    createdAt: img.created_at ? new Date(img.created_at * 1000).toISOString() : '',
    isFavourite: !!img.metadata?.favorite,
    mediaType: isVideoFile(img.id) ? 'video' : 'image',
    raw_url: img.raw_url,
    metadata: img.metadata,
    bucketId: img.bucketId,
    reference_images: img.reference_images || [],
  });

  const toggleFavourite = async (img: AllImage) => {
    const want = !img.metadata?.favorite;
    const ok = await bucketsApi.toggleFavorite(img.bucketId, img.id, want);
    if (!ok) {
      toast.error('Could not change the favourite');
      return;
    }
    setImages((prev) =>
      prev.map((i) => (i.key === img.key ? { ...i, metadata: { ...i.metadata, favorite: want } } : i)),
    );
  };

  if (loading && !images.length) {
    return <p className="px-1 py-6 text-sm text-muted-foreground">Reading every bucket…</p>;
  }

  return (
    <div className="flex flex-col gap-2 pt-2">
      {/* which buckets: none chosen = all of them */}
      <div className="-mx-1 flex gap-1.5 overflow-x-auto px-1 py-1 sm:flex-wrap sm:overflow-visible [scrollbar-width:none]">
        {buckets
          .filter((b) => bucketCounts.get(b.id))
          .map((b) => {
            const on = chosenBuckets.includes(b.id);
            return (
              <button
                key={b.id}
                type="button"
                aria-pressed={on}
                onClick={() => setChosenBuckets((p) => (p.includes(b.id) ? p.filter((x) => x !== b.id) : [...p, b.id]))}
                className={cn(CHIP, on ? CHIP_ON : CHIP_OFF)}
              >
                {b.label}{' '}
                <span className={cn('text-sm font-normal', on ? 'text-white/80' : 'text-muted-foreground')}>
                  {bucketCounts.get(b.id)}
                </span>
              </button>
            );
          })}
      </div>
      <div className="w-full min-w-0 [contain:inline-size]">
        <TagFilterBar
          chips={chips}
          chosen={chosen}
          onToggle={(k) => setChosen((p) => (p.includes(k) ? p.filter((x) => x !== k) : [...p, k]))}
          onClear={() => setChosen([])}
        />
      </div>
      {sections.length === 0 || (chosen.length > 0 && sections[0].images.length === 0) ? (
        <p className="px-1 py-6 text-sm text-muted-foreground">No picture has all of these tags.</p>
      ) : (
        sections.map((s, si) => {
          const isOpen = open[s.key] ?? si === 0;
          const limit = shown[s.key] ?? PAGE;
          const label = chosen.length ? s.label : `${s.label} (${s.images.length})`;
          return (
            <ExpandableContainer
              key={s.key}
              id={s.key}
              label={label}
              variant="section"
              iconPos="left"
              collapsed={!isOpen}
              onToggle={() => setOpen((p) => ({ ...p, [s.key]: !isOpen }))}
            >
              <div className="grid grid-cols-2 gap-1 sm:grid-cols-3 md:grid-cols-4">
                {s.images.slice(0, limit).map((img, i) => (
                  <div key={img.key} className="relative">
                    <ImageCard
                      image={toItem(img)}
                      index={i}
                      bucketId={img.bucketId}
                      sectionVariant="dated"
                      onToggleFavorite={() => toggleFavourite(img)}
                      onFullscreenClick={() => {
                        const items = s.images.map(toItem);
                        openLoope(items, Math.max(0, s.images.indexOf(img)), `All buckets – ${s.label}`);
                      }}
                    />
                    {/* which bucket it lives in */}
                    <span className="pointer-events-none absolute bottom-2 left-2 rounded bg-black/55 px-1.5 py-0.5 text-[11px] text-white">
                      {img.bucketLabel}
                    </span>
                  </div>
                ))}
              </div>
              {s.images.length > limit && (
                <button
                  type="button"
                  onClick={() => setShown((p) => ({ ...p, [s.key]: limit + PAGE * 2 }))}
                  className={cn(CHIP, CHIP_OFF, 'mx-auto my-3 text-stone-700')}
                >
                  Show more ({s.images.length - limit})
                </button>
              )}
            </ExpandableContainer>
          );
        })
      )}
    </div>
  );
}
