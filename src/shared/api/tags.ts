// Art tags: the AI tagger's lexicon and status, and a person's edits to one image's tags (docs/ART_TAGS.md).
// Owner, 27 Oct: every image saved to a bucket is tagged; favourites stay in bucket.json and are shown as a ★ tag,
// so they still go through bucketsApi.toggleFavorite, never through here.
import { jsonInit, requestJson } from '@/shared/http/client';
import type { TagLexicon, TaggingInfo, TagStatus } from './types';

let lexiconPromise: Promise<TagLexicon> | null = null;

/** The lexicon (config/art_tags.yaml), fetched once per page load; a failed fetch is retried next time. */
export function getLexicon(): Promise<TagLexicon> {
  if (!lexiconPromise) {
    lexiconPromise = requestJson<TagLexicon>('/art-tags/lexicon', undefined, 'Failed to load the tag words').catch((e) => {
      lexiconPromise = null;
      throw e;
    });
  }
  return lexiconPromise;
}

export function getStatus(): Promise<TagStatus> {
  return requestJson<TagStatus>('/art-tags/status', undefined, 'Failed to load tagging status');
}

/** Add and/or take out words; the server recomputes the effective list and returns it. */
export function editTags(
  bucketId: string,
  filename: string,
  change: { add?: string[]; remove?: string[] },
): Promise<{ tags: string[]; tagging: TaggingInfo }> {
  return requestJson(
    `/buckets/${bucketId}/tags/${encodeURIComponent(filename)}`,
    jsonInit('POST', { add: change.add ?? [], remove: change.remove ?? [] }),
    (r) => `Failed to change tags: ${r.statusText}`,
  );
}

/** Put the image at the front of the AI tagger's queue; hand-added and taken-out words are kept. */
export function retag(bucketId: string, filename: string): Promise<{ queued: boolean }> {
  return requestJson(
    `/buckets/${bucketId}/tags/${encodeURIComponent(filename)}/retag`,
    { method: 'POST' },
    (r) => `Failed to queue tagging: ${r.statusText}`,
  );
}

/** A tag word as stored: lower case, trimmed, spaces collapsed to single hyphens, at most 32 characters. */
export function normaliseTag(word: string): string {
  return word.trim().toLowerCase().replace(/\s+/g, '-').replace(/-+/g, '-').slice(0, 32).replace(/^-+|-+$/g, '');
}

/** A tag word for display: hyphens shown as spaces ("still-life" -> "still life"). */
export function tagLabel(word: string): string {
  return word.replace(/-/g, ' ');
}
