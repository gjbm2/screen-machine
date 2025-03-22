
import { GeneratedImage } from '../types';
import { ImageGenerationStatus } from '@/types/workflows';

/**
 * Updates a placeholder image with actual generation results
 * while preserving important metadata like reference images
 */
export const updateImageWithResult = (
  placeholder: GeneratedImage,
  imageUrl: string
): GeneratedImage => {
  // Ensure we preserve the referenceImageUrl when updating the image
  // Log what's being preserved for debugging
  if (placeholder.referenceImageUrl) {
    console.log('[result-handler] Preserving reference image URL during update:', placeholder.referenceImageUrl);
  } else {
    console.log('[result-handler] No reference image URL to preserve during update');
  }
  
  return {
    ...placeholder,
    url: imageUrl,
    status: 'completed' as ImageGenerationStatus,
    timestamp: Date.now(),
    // Reference image URL is automatically preserved via spread operator
  };
};

/**
 * Updates a placeholder image to show an error state
 */
export const updateImageWithError = (
  placeholder: GeneratedImage
): GeneratedImage => {
  // Log reference image information for debugging
  if (placeholder.referenceImageUrl) {
    console.log('[result-handler] Preserving reference image URL during error update:', placeholder.referenceImageUrl);
  }
  
  // Ensure we preserve the referenceImageUrl for error images too
  return {
    ...placeholder,
    status: 'error' as ImageGenerationStatus,
    timestamp: Date.now()
    // Reference image URL is automatically preserved via spread operator
  };
};

/**
 * Updates the generated images state with API results
 */
export const processGenerationResults = (
  response: any,
  currentBatchId: string,
  prevImages: GeneratedImage[]
): GeneratedImage[] => {
  if (!response || !response.images) {
    throw new Error('No images were returned');
  }
  
  const newImages = [...prevImages];
  const images = response.images;
  
  images.forEach((img: any, index: number) => {
    // Find the placeholder for this image
    const placeholderIndex = newImages.findIndex(
      pi => pi.batchId === currentBatchId && pi.batchIndex === index && pi.status === 'generating'
    );
    
    if (placeholderIndex >= 0) {
      // Update the placeholder with actual data
      newImages[placeholderIndex] = updateImageWithResult(
        newImages[placeholderIndex],
        img.url
      );
      
      // Log the updated image for debugging
      if (newImages[placeholderIndex].referenceImageUrl) {
        console.log("[result-handler] Updated image with reference image URL:", newImages[placeholderIndex].referenceImageUrl);
      }
    } else {
      // No placeholder found, this is unexpected
      console.warn(`[result-handler] No placeholder found for batch ${currentBatchId}, index ${index}`);
    }
  });
  
  return newImages;
};

/**
 * Updates all images in a batch to error state
 */
export const markBatchAsError = (
  batchId: string,
  prevImages: GeneratedImage[]
): GeneratedImage[] => {
  return prevImages.map(img => {
    if (img.batchId === batchId && img.status === 'generating') {
      return updateImageWithError(img);
    }
    return img;
  });
};
