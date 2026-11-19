import { JSON_HEADERS, jsonInit, readErrorBody, requestJson, send } from '@/shared/http/client';
import type { PublishDestination } from './types';
import { askFit, type Fit } from '@/shared/fitChoice';

interface RawPublishDestination {
  id: string;
  name?: string;
  description?: string;
  icon?: string;
  has_bucket?: boolean;
  headless?: boolean;
  hidden?: boolean;
  groups?: string[];
  access?: 'guest' | 'approved' | 'admin';
}

/**
 * How to fit a picture of another shape than the screen (routes/reframe.py): asks the person (FitChoiceDialog) when
 * POST /api/publish/fit-check says the picture is the wrong shape. Returns the choice, undefined to use the screen's own
 * setting (the right shape, no dialog, or the check failed: never in the way of a publish), or null when they cancel.
 */
export async function chooseFit(where: {
  dest_bucket_id: string;
  src_bucket_id?: string;
  filename?: string;
  source_url?: string;
}): Promise<Fit | null | undefined> {
  try {
    const response = await send('/publish/fit-check', jsonInit('POST', where));
    if (!response.ok) return undefined;
    const check = await response.json();
    if (!check?.ask) return undefined;
    return askFit({ screen: where.dest_bucket_id, name: check.name || where.dest_bucket_id, picture: check.picture, size: check.screen });
  } catch {
    return undefined;
  }
}

/**
 * What to say after a publish: `published`, unless the picture is still being fitted to the screen (routes/publisher.py
 * makes it in the background, as a job in the Jobs dock, and the screen changes when it is ready).
 */
export function publishedWords(result: { fitting?: string } | undefined, published: string): string {
  return result?.fitting ? `Fitting to ${result.fitting}…` : published;
}

// Never rejects: every failure (validation, non-2xx, timeout, network, unparseable 2xx body) resolves { success: false, error }.
// A picture of another shape than the screen is fitted as the person chooses (chooseFit), unless `fit` is given;
// { success: false, cancelled: true } when they cancel.
export async function publishImageUnified(data: {
  dest_bucket_id: string;
  src_bucket_id?: string;
  filename?: string;
  source_url?: string;
  metadata?: unknown;
  skip_bucket?: boolean;
  fit?: Fit;
}): Promise<{ success: boolean; error?: string; cancelled?: boolean; fitting?: string }> {
  const isRouteBucket = !!(data.src_bucket_id && data.filename);
  const isRouteExternal = !!data.source_url;

  if (!isRouteBucket && !isRouteExternal) {
    console.error('[publishImageUnified] Missing required parameters');
    return { success: false, error: 'Missing required parameters: either (src_bucket_id + filename) or source_url' };
  }

  if (!data.fit) {
    const fit = await chooseFit(data);
    if (fit === null) return { success: false, cancelled: true, error: 'Not published' };
    if (fit) data = { ...data, fit };
  }

  try {
    const controller = new AbortController();
    // The timeout covers only the fetch itself, not the body read, and is not cleared if fetch rejects.
    const timeoutId = setTimeout(() => controller.abort(), 30000);

    const response = await send('/publish', {
      method: 'POST',
      headers: JSON_HEADERS,
      body: JSON.stringify(data),
      signal: controller.signal
    });

    clearTimeout(timeoutId);

    if (!response.ok) {
      const errorObj = await readErrorBody(response, `Server error: ${response.status}`);
      console.error(`[publishImageUnified] Error response (${response.status}):`, errorObj);
      return {
        success: false,
        error: errorObj.error || `Failed with status ${response.status}`
      };
    }

    // A non-JSON 2xx body lands in the catch below. `fitting` names the screen while the picture is still being
    // fitted to it in the background (it goes up, with its overlay, when it is made).
    const body = await response.json();
    return { success: true, fitting: body?.meta?.fitting || undefined };
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      console.error('[publishImageUnified] Request timed out after 30 seconds');
      return { success: false, error: 'Request timed out' };
    }

    console.error('[publishImageUnified] Request failed:', error);
    return {
      success: false,
      error: error instanceof Error ? `${error.name}: ${error.message}` : String(error)
    };
  }
}

// Fields other than these (e.g. type, file) are deliberately dropped; useBaseFileName depends on that.
export async function getPublishDestinations(includeRecent: boolean = false): Promise<PublishDestination[]> {
  const destinations = await requestJson<RawPublishDestination[]>('/publish-destinations', undefined, 'Failed to fetch publish destinations');
  return destinations
    .filter((dest) => (!dest.hidden) || (includeRecent && dest.id === '_recent'))
    .map((dest) => ({
      id: dest.id,
      name: dest.name || dest.id,
      description: dest.description,
      icon: dest.icon,
      has_bucket: dest.has_bucket || false,
      headless: dest.headless || false,
      hidden: dest.hidden || false,
      groups: dest.groups || [],
      access: dest.access,
    }));
}

// Never rejects; maps the backend's `filename` (not `published`) field.
export async function getPublishedContent(destinationId: string): Promise<{
  published: string | null;
  publishedAt: string | null;
  raw_url: string | null;
  thumbnail_url: string | null;
}> {
  try {
    const response = await send(`/published/${destinationId}`);

    if (response.status === 404) {
      return {
        published: null,
        publishedAt: null,
        raw_url: null,
        thumbnail_url: null
      };
    }

    if (!response.ok) {
      throw new Error(`Failed to get published content: ${response.statusText}`);
    }

    const data = await response.json();
    return {
      published: data.filename || null,
      publishedAt: data.published_at || null,
      raw_url: data.raw_url || null,
      thumbnail_url: data.thumbnail_url || null
    };
  } catch (error) {
    console.error(`Error fetching published content for ${destinationId}:`, error);
    return {
      published: null,
      publishedAt: null,
      raw_url: null,
      thumbnail_url: null
    };
  }
}

// Never rejects; maps the backend's `published` field (unlike getPublishedContent).
export async function getPublishedContentForDestination(destinationId: string): Promise<{
  published: string | null;
  publishedAt: string | null;
  raw_url: string | null;
  thumbnail_url: string | null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  meta?: any;
}> {
  try {
    const data = await requestJson(
      `/published/${destinationId}`,
      undefined,
      (response) => `Failed to get published content: ${response.statusText}`
    );
    return {
      published: data.published || null,
      publishedAt: data.published_at || null,
      raw_url: data.raw_url || null,
      thumbnail_url: data.thumbnail_url || null,
      meta: data.meta || {}
    };
  } catch (error) {
    console.error(`Error fetching published content for ${destinationId}:`, error);
    return {
      published: null,
      publishedAt: null,
      raw_url: null,
      thumbnail_url: null,
      meta: {}
    };
  }
}

// Throwing variants of POST /publish (publishImageUnified returns {success:false} and has a 30 s timeout instead).
// Both ask how to fit a picture of another shape (chooseFit); a cancel throws "Not published".
export async function publishBucketFile(destBucketId: string, srcBucketId: string, filename: string) {
  const fit = await chooseFit({ dest_bucket_id: destBucketId, src_bucket_id: srcBucketId, filename });
  if (fit === null) throw new Error('Not published');
  return requestJson(
    '/publish',
    jsonInit('POST', { dest_bucket_id: destBucketId, src_bucket_id: srcBucketId, filename, ...(fit ? { fit } : {}) }),
    (response) => `Publish failed: ${response.statusText}`,
  );
}

export async function publishUrl(destBucketId: string, sourceUrl: string) {
  const fit = await chooseFit({ dest_bucket_id: destBucketId, source_url: sourceUrl });
  if (fit === null) throw new Error('Not published');
  return requestJson(
    '/publish',
    jsonInit('POST', { dest_bucket_id: destBucketId, source_url: sourceUrl, ...(fit ? { fit } : {}) }),
    (response) => `Publish failed: ${response.statusText}`,
  );
}

/** What a screen does with a picture of another shape than its own (routes/reframe.py). */
export type Reframe = Fit;

/** PUT /api/publish-destinations/<id>/reframe: a screen's choice for a picture of another shape (Settings › Screens). */
export function setScreenReframe(id: string, reframe: Reframe): Promise<{ id: string; reframe: Reframe }> {
  return requestJson(`/publish-destinations/${encodeURIComponent(id)}/reframe`, jsonInit('PUT', { reframe }),
    "The screen's choice wasn't saved");
}
