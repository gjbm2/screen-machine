import { JSON_HEADERS, jsonInit, readErrorBody, requestJson, send } from '@/shared/http/client';
import type { PublishDestination } from './types';

interface RawPublishDestination {
  id: string;
  name?: string;
  description?: string;
  icon?: string;
  has_bucket?: boolean;
  headless?: boolean;
  hidden?: boolean;
  groups?: string[];
}

// Never rejects: every failure (validation, non-2xx, timeout, network, unparseable 2xx body) resolves { success: false, error }.
export async function publishImageUnified(data: {
  dest_bucket_id: string;
  src_bucket_id?: string;
  filename?: string;
  source_url?: string;
  metadata?: unknown;
  skip_bucket?: boolean;
}): Promise<{ success: boolean; error?: string }> {
  const isRouteBucket = !!(data.src_bucket_id && data.filename);
  const isRouteExternal = !!data.source_url;

  if (!isRouteBucket && !isRouteExternal) {
    console.error('[publishImageUnified] Missing required parameters');
    return { success: false, error: 'Missing required parameters: either (src_bucket_id + filename) or source_url' };
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

    // The server payload is discarded, but a non-JSON 2xx body still lands in the catch below.
    await response.json();
    return { success: true };
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
      groups: dest.groups || []
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
export async function publishBucketFile(destBucketId: string, srcBucketId: string, filename: string) {
  return requestJson(
    '/publish',
    jsonInit('POST', { dest_bucket_id: destBucketId, src_bucket_id: srcBucketId, filename }),
    (response) => `Publish failed: ${response.statusText}`,
  );
}

export async function publishUrl(destBucketId: string, sourceUrl: string) {
  return requestJson(
    '/publish',
    jsonInit('POST', { dest_bucket_id: destBucketId, source_url: sourceUrl }),
    (response) => `Publish failed: ${response.statusText}`,
  );
}
