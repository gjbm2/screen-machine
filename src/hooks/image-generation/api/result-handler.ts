
import { GeneratedImage } from '../types';

/**
 * Process the results of an image generation API call and update the generated images state
 */
export const processGenerationResults = (
  response: any,
  batchId: string,
  prevImages: GeneratedImage[]
): GeneratedImage[] => {
  // If no images were returned, return the previous state
  if (!response.images || response.images.length === 0) {
    console.warn('[result-handler] No images returned from API');
    return prevImages;
  }

  // Find all placeholders for this batch
  const placeholderIndices = prevImages
    .map((img, index) => img.batchId === batchId ? index : -1)
    .filter(index => index !== -1);

  // Create a copy of the previous images array
  const newImages = [...prevImages];

  // Replace placeholders with actual images
  response.images.forEach((image: any, index: number) => {
    const placeholderIndex = placeholderIndices[index];
    
    if (placeholderIndex !== undefined) {
      // Extract existing placeholder to preserve metadata
      const placeholder = prevImages[placeholderIndex];
      
      // Create updated image with API response data
      newImages[placeholderIndex] = {
        ...placeholder,
        url: image.url,
        loading: false,
        error: false,
        timestamp: Date.now(),
        // Make sure to preserve ALL parameters from the placeholder
        workflow: placeholder.workflow,
        prompt: placeholder.prompt,
        params: placeholder.params, // Ensure workflow params are preserved
        refiner: placeholder.refiner, // Ensure refiner is preserved
        refinerParams: placeholder.refinerParams, // Ensure refiner params are preserved
        globalParams: placeholder.globalParams, // Ensure global params are preserved
        referenceImageUrl: placeholder.referenceImageUrl,
        title: placeholder.title
      };
    }
  });

  return newImages;
};

/**
 * Mark all images in a batch as having an error
 */
export const markBatchAsError = (
  batchId: string,
  prevImages: GeneratedImage[]
): GeneratedImage[] => {
  return prevImages.map(image => {
    if (image.batchId === batchId) {
      return {
        ...image,
        loading: false,
        error: true
      };
    }
    return image;
  });
};
