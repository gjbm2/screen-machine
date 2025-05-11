import { useEffect } from 'react';
import { useReferenceImages } from '@/contexts/ReferenceImagesContext';

/**
 * This adapter is no longer needed as useUploadedImages now handles the syncing.
 * This is kept as a no-op to avoid breaking existing code that imports it.
 * 
 * @deprecated Use useUploadedImages instead
 */
export const useReferenceImagesAdapter = () => {
  const { referenceUrls } = useReferenceImages();
  
  // No syncing is done here anymore as useUploadedImages handles it
  // This is to prevent multiple hooks trying to sync and causing infinite loops
  
  return { referenceUrls };
}; 