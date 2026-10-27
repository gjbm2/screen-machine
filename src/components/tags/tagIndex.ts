// The generator's Tags view, as plain functions over a bucket's images: the chips (with counts) and the sections.
// Owner, 27 Oct: a screen's images can be browsed by tag as well as by date; favourites show as a ★ tag.
import type { TagLexicon } from '@/shared/api/types';
import { tagLabel } from '@/shared/api/tags';

export type ShownFacet = 'subject' | 'medium' | 'moods';

/** Chip keys that are not words (a tag word never starts with "__"). */
export const FAVOURITE_CHIP = '__favourite';
export const UNTAGGED_CHIP = '__untagged';

export interface TaggableImage {
  id: string;
  created_at?: number;
  metadata?: Record<string, any>;
}

export interface TagChip {
  key: string;
  label: string;
  count: number;
  kind: 'favourite' | ShownFacet | 'free' | 'untagged';
}

export interface TagSection<T> {
  key: string;
  label: string;
  images: T[];
}

/** word -> its facet; a word in two facets ("cosmic") counts as the earlier one (subject before moods). */
export function facetIndex(lexicon: TagLexicon | null): Map<string, ShownFacet> {
  const m = new Map<string, ShownFacet>();
  if (!lexicon?.facets) return m;
  (['subject', 'medium', 'moods'] as ShownFacet[]).forEach((f) => {
    (lexicon.facets[f] ?? []).forEach((w) => {
      if (!m.has(w.word)) m.set(w.word, f);
    });
  });
  return m;
}

export function imageTags(img: TaggableImage): string[] {
  const t = img.metadata?.tags;
  return Array.isArray(t) ? t : [];
}

/** Waiting for the AI: no tagging status yet (a hand-added word doesn't count). Videos are never tagged. */
export function awaitingTagger(img: TaggableImage, isVideo: (id: string) => boolean): boolean {
  return !img.metadata?.tagging?.status && !isVideo(img.id);
}

/** The section an image sits in when no chip is chosen: its first subject word, else its first tag. */
export function primaryTag(img: TaggableImage, facets: Map<string, ShownFacet>): string | null {
  const tags = imageTags(img);
  return tags.find((t) => facets.get(t) === 'subject') ?? tags[0] ?? null;
}

const FACET_ORDER: Record<string, number> = { subject: 0, medium: 1, moods: 2, free: 3 };

/** ★ Favourites, then words by facet (subject, medium, moods, then free words), most used first; Untagged last. */
export function buildChips(images: TaggableImage[], facets: Map<string, ShownFacet>): TagChip[] {
  const counts = new Map<string, number>();
  let favs = 0;
  let untagged = 0;
  for (const img of images) {
    if (img.metadata?.favorite) favs += 1;
    const tags = imageTags(img);
    if (!tags.length) untagged += 1;
    for (const t of new Set(tags)) counts.set(t, (counts.get(t) ?? 0) + 1);
  }
  const words: TagChip[] = [...counts.entries()]
    .map(([w, n]) => ({ key: w, label: tagLabel(w), count: n, kind: facets.get(w) ?? ('free' as const) }))
    .sort((a, b) => FACET_ORDER[a.kind] - FACET_ORDER[b.kind] || b.count - a.count || a.key.localeCompare(b.key));
  const chips: TagChip[] = [];
  if (favs) chips.push({ key: FAVOURITE_CHIP, label: 'Favourites', count: favs, kind: 'favourite' });
  chips.push(...words);
  if (untagged) chips.push({ key: UNTAGGED_CHIP, label: 'Untagged', count: untagged, kind: 'untagged' });
  return chips;
}

/** Every chosen chip must hold (AND). */
export function matchesChips(img: TaggableImage, chosen: string[]): boolean {
  const tags = imageTags(img);
  return chosen.every((c) =>
    c === FAVOURITE_CHIP ? !!img.metadata?.favorite : c === UNTAGGED_CHIP ? tags.length === 0 : tags.includes(c),
  );
}

/** Favourites first, then newest. */
export function favouritesThenNewest<T extends TaggableImage>(a: T, b: T): number {
  const fa = a.metadata?.favorite ? 1 : 0;
  const fb = b.metadata?.favorite ? 1 : 0;
  return fb - fa || (b.created_at || 0) - (a.created_at || 0);
}

function capitalise(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** One section per primary tag, biggest first; each image in exactly one section (dnd ids stay unique). */
export function sectionsBySubject<T extends TaggableImage>(images: T[], facets: Map<string, ShownFacet>): TagSection<T>[] {
  const byKey = new Map<string, T[]>();
  for (const img of images) {
    const k = primaryTag(img, facets) ?? UNTAGGED_CHIP;
    if (!byKey.has(k)) byKey.set(k, []);
    byKey.get(k)!.push(img);
  }
  return [...byKey.entries()]
    .map(([k, imgs]) => ({
      key: k,
      label: k === UNTAGGED_CHIP ? 'Untagged' : capitalise(tagLabel(k)),
      images: imgs.sort(favouritesThenNewest),
    }))
    .sort(
      (a, b) =>
        Number(a.key === UNTAGGED_CHIP) - Number(b.key === UNTAGGED_CHIP) ||
        b.images.length - a.images.length ||
        a.key.localeCompare(b.key),
    );
}
