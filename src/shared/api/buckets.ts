// Bucket endpoints: listing, ordering, favourites, copy/move, delete, maintenance.
// Path segments are interpolated raw unless encodeURIComponent is shown; that matches the old Api class.

import { apiUrl, jsonInit, JSON_HEADERS, request, requestJson, send, sendUrl } from '@/shared/http/client';
import type { BucketDetails, RawBucketFile, ReferenceImageInfo } from './types';

export async function uploadToBucket(bucketId: string, file: File): Promise<{ filename: string }> {
  const formData = new FormData();
  formData.append('file', file);
  return requestJson(
    `/buckets/${bucketId}/upload`,
    { method: 'POST', body: formData },
    (r) => `Upload failed: ${r.statusText}`,
  );
}

// Known backend gap: /upload only reads request.files['file'], so a JSON {url} body is always rejected.
export async function uploadUrlToBucket(bucketId: string, url: string): Promise<void> {
  await request(`/buckets/${bucketId}/upload`, jsonInit('POST', { url }), 'Upload failed');
}

export async function deleteFromBucket(bucketId: string, filename: string): Promise<{ status: string }> {
  return requestJson(
    `/buckets/${bucketId}/${filename}`,
    { method: 'DELETE' },
    (r) => `Failed to delete from bucket: ${r.statusText}`,
  );
}

export async function purgeBucket(bucketId: string, days?: number): Promise<{ status: string; removed: string[]; error?: string }> {
  // new URL() without a base: throws if API_URL is relative, as before.
  const url = new URL(apiUrl(`/buckets/${bucketId}/purge`));
  if (days !== undefined) {
    url.searchParams.append('days', days.toString());
  }

  const response = await sendUrl(url.toString(), { method: 'DELETE' });
  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`Failed to purge bucket: ${errorText}`);
  }

  const result = await response.json();
  if (result.status !== 'purged') {
    throw new Error(result.error || 'Failed to purge bucket');
  }
  return result;
}

export async function moveToPosition(
  bucketId: string,
  filename: string,
  targetFilename: string | null = null,
): Promise<{ status: string; index: number }> {
  // A null target is sent explicitly ("insert_after": null) and means move to the front.
  return requestJson(
    `/buckets/${bucketId}/move-to/${filename}`,
    jsonInit('POST', { insert_after: targetFilename }),
    (r) => `Failed to move image to position: ${r.statusText}`,
  );
}

function emptyBucketDetails(bucketId: string, error: string | null): BucketDetails {
  return {
    name: bucketId,
    items: [],
    published: null,
    publishedAt: null,
    raw_url: null,
    thumbnail_url: null,
    favorites: [],
    sequence: [],
    error,
  };
}

/** Never rejects: failures come back in `error` (a 404 is an empty bucket with error null). */
export async function getBucketDetails(bucketId: string): Promise<BucketDetails> {
  try {
    const response = await send(`/buckets/${bucketId}/complete`);

    if (response.status === 404) {
      return emptyBucketDetails(bucketId, null);
    }
    if (!response.ok) {
      throw new Error(`Failed to get bucket details: ${response.statusText}`);
    }

    const data = await response.json();

    const items = Array.isArray(data.files) ? data.files.map((file: RawBucketFile) => {
      const baseMeta = file.metadata || {};
      const createdTs = file.created_at || baseMeta.timestamp || file.modified;
      return {
        filename: file.filename,
        url: file.raw_url || file.url || '',
        thumbnail_url: file.thumbnail_url,
        thumbnail_embedded: file.thumbnail_embedded,
        favorite: file.favorite || false,
        metadata: {
          ...baseMeta,
          timestamp: createdTs,
        },
        created_at: createdTs,
        reference_images: Array.isArray(file.reference_images) ? file.reference_images : [],
      };
    }) : [];

    // `published` is the backend's published OBJECT, despite BucketDetails typing it as a string.
    const published = data.published || null;
    const publishedAt = published?.published_at || null;
    const raw_url = published?.raw_url || null;
    const thumbnail_url = published?.thumbnail_url || null;

    return {
      name: bucketId,
      items,
      published,
      publishedAt,
      raw_url,
      thumbnail_url,
      favorites: data.favorites || [],
      sequence: data.sequence || [],
      error: null,
    };
  } catch (error) {
    console.error('Error getting bucket details:', error);
    return emptyBucketDetails(bucketId, error instanceof Error ? error.message : 'Unknown error');
  }
}

export async function getImageComplete(bucketId: string, filename: string): Promise<{
  bucket_id: string;
  filename: string;
  size: number;
  modified: number;
  created_at: number;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  metadata: Record<string, any>;
  favorite: boolean;
  thumbnail_url: string;
  raw_url: string;
  reference_images: ReferenceImageInfo[];
}> {
  return requestJson(
    `/buckets/${bucketId}/image/${encodeURIComponent(filename)}/complete`,
    undefined,
    (r) => `Failed to get image details: ${r.statusText}`,
  );
}

/** isFavorite is the DESIRED state: true favourites (POST), false unfavourites (DELETE). */
export async function toggleFavorite(bucketId: string, filename: string, isFavorite: boolean): Promise<boolean> {
  const method = isFavorite ? 'POST' : 'DELETE';
  const data = await requestJson(
    `/buckets/${bucketId}/favorite/${encodeURIComponent(filename)}`,
    { method, headers: JSON_HEADERS },
    (r) => `Failed to toggle favorite: ${r.statusText}`,
  );
  return data.status === 'favorited' || data.status === 'unfavorited';
}

export async function deleteImage(bucketId: string, filename: string): Promise<boolean> {
  await request(`/buckets/${bucketId}/${filename}`, { method: 'DELETE' }, (r) => `Failed to delete image: ${r.statusText}`);
  return true;
}

/** copy=false (the default) MOVES the image; the key is always sent, overriding the backend's copy=True default. */
export async function copyImageToBucket(
  sourceBucketId: string,
  targetBucketId: string,
  filename: string,
  copy: boolean = false,
): Promise<{ status: string; filename: string }> {
  return requestJson(
    '/buckets/add_image_to_new_bucket',
    jsonInit('POST', {
      source_publish_destination: sourceBucketId,
      target_publish_destination: targetBucketId,
      filename,
      copy,
    }),
    (r) => `Failed to copy image: ${r.statusText}`,
  );
}

// 'purge' POSTs to a DELETE-only route (405); kept as it was.
export async function performBucketMaintenance(bucketId: string, action: 'purge' | 'reindex' | 'extract'): Promise<boolean> {
  let path: string;
  if (action === 'reindex') {
    path = `/buckets/${bucketId}/reindex`;
  } else if (action === 'purge') {
    path = `/buckets/${bucketId}/purge`;
  } else if (action === 'extract') {
    path = `/buckets/${bucketId}/extractjson`;
  } else {
    throw new Error(`Unknown maintenance action: ${action}`);
  }

  await request(path, { method: 'POST' }, (r) => `Failed to perform maintenance: ${r.statusText}`);
  return true;
}
