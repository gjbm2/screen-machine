// Image generation. Jobs and their progress are the job tracker's (./jobs.ts, docs/JOBS.md).
import { send } from '@/shared/http/client';
import type { GenerateImageParams } from './types';

// Relative /output/ and /api/ reference URLs are made absolute against the page origin; data: URIs pass through.
function normalizeReferenceUrl(url: string): string {
  if (url.startsWith('data:')) {
    return url;
  }
  if (url.startsWith('/output/') || url.startsWith('/api/')) {
    return `${window.location.origin}${url}`;
  }
  return url;
}

export async function generateImage(params: GenerateImageParams) {
  const {
    prompt,
    workflow,
    params: workflowParams,
    global_params,
    imageFiles,
    batch_id,
    placeholders,
    referenceUrls,
    refiner,
    refiner_params,
  } = params;

  const jsonData: Record<string, unknown> = {
    prompt,
    // "auto" becomes null so the backend applies its default resolution
    workflow: workflow === 'auto' ? null : workflow,
    params: workflowParams || {},
    global_params: global_params || { batch_size: 1 },
    batch_id,
    has_reference_image: (imageFiles && imageFiles.length > 0) || false,
  };

  if (referenceUrls && referenceUrls.length > 0) {
    jsonData.referenceUrls = referenceUrls.map(normalizeReferenceUrl);
  }

  if (placeholders && placeholders.length > 0) {
    jsonData.placeholders = placeholders;
  }

  // "auto" (or unset) omits refiner so the backend decides; "none" is sent explicitly
  if (refiner && refiner !== 'auto') {
    jsonData.refiner = refiner;
    if (refiner_params) {
      jsonData.refiner_params = refiner_params;
    }
  }

  const formData = new FormData();
  formData.append('data', JSON.stringify(jsonData));

  if (imageFiles && imageFiles.length > 0) {
    imageFiles.forEach(file => {
      if (file instanceof File) {
        formData.append('image', file);
      }
    });
  }

  const response = await send('/generate-image', { method: 'POST', body: formData });

  if (!response.ok) {
    // A non-JSON error body rejects with the SyntaxError from response.json()
    const errorData = await response.json();
    throw new Error(errorData.error || 'Failed to generate image');
  }

  const result = await response.json();

  // The Recent tab relies on this metadata-carrying event; a missing result.images throws TypeError here (as before)
  if (result.recent_files && result.recent_files.length > 0) {
    const imagesWithMetadata = result.images.map((img: Record<string, unknown>, index: number) => ({
      fileName: result.recent_files[index],
      metadata: {
        ...img,
      },
    }));

    const eventDetail = {
      batchId: result.batch_id,
      files: result.recent_files,
      imagesWithMetadata: imagesWithMetadata,
      // the placeholders sent, in image order, so the Recent view replaces each one with its own image
      placeholderIds: (placeholders || []).map(p => p.placeholder_id),
    };

    window.dispatchEvent(new CustomEvent('recent:add', { detail: eventDetail }));
  }

  return result;
}

// Job control (cancel one, cancel all) is the job tracker's: ./jobs.ts. Re-exported for the old Api facade.
export { cancelAllJobs } from './jobs';
