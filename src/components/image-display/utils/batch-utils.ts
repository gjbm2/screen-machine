
import { ViewMode } from "../ImageDisplay";

/**
 * Helper function to determine if the image batch should be visible based on viewMode and content
 */
export const shouldShowImageBatch = (
  viewMode: ViewMode, 
  hasCompletedImages: boolean, 
  hasGeneratingImages: boolean, 
  hasFailedImages: boolean
): boolean => {
  // In small view, hide batches with no completed images and no generating/failed images
  if (viewMode === 'small') {
    return hasCompletedImages || hasGeneratingImages || hasFailedImages;
  }
  
  // In other views, always show the batch
  return true;
};

/**
 * Safely get a valid image index that's within bounds
 */
export const getSafeImageIndex = (
  currentIndex: number, 
  totalImages: number
): number => {
  if (totalImages === 0) return 0;
  return Math.min(Math.max(0, currentIndex), totalImages - 1);
};

/**
 * Find image by URL within an image array
 */
export const findImageByUrl = (
  images: Array<{ url: string }>, 
  targetUrl: string
): { url: string } | undefined => {
  return images.find(img => img.url === targetUrl);
};
