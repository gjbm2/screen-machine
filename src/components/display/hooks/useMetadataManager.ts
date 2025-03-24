
import { useState, useRef } from 'react';
import { toast } from 'sonner';
import { DisplayParams } from '../types';

export const useMetadataManager = (params: DisplayParams, imageUrl: string | null, extractMetadataFromImage: (url: string) => Promise<Record<string, string>>) => {
  const [previousImageUrl, setPreviousImageUrl] = useState<string | null>(null);
  const [metadataExtractionAttempted, setMetadataExtractionAttempted] = useState(false);
  const mountedRef = useRef(true);

  // Reset metadata extraction flag when image URL changes
  const resetMetadataExtractionFlag = () => {
    if (!mountedRef.current) return;
    
    if (imageUrl !== previousImageUrl) {
      setPreviousImageUrl(imageUrl);
      setMetadataExtractionAttempted(false);
    }
  };

  // Extract metadata when image changes or loads
  const attemptMetadataExtraction = async (
    imageUrl: string | null, 
    metadata: Record<string, string>,
    isLoading: boolean, 
    isTransitioning: boolean
  ) => {
    if (!mountedRef.current) return;
    
    // Check if the image URL has changed
    const imageUrlChanged = imageUrl !== previousImageUrl;
    
    // Only extract metadata if the image URL has changed or if we haven't attempted it yet for this URL
    if (imageUrl && 
        (imageUrlChanged || Object.keys(metadata).length === 0) && 
        !isLoading && 
        !isTransitioning && 
        !metadataExtractionAttempted &&
        mountedRef.current) {
      
      console.log('[useMetadataManager] Image URL changed or no metadata found, retrieving metadata');
      setPreviousImageUrl(imageUrl);
      setMetadataExtractionAttempted(true);
      
      // Always try to extract metadata when image changes or loads
      if (mountedRef.current) {
        try {
          await extractMetadataFromImage(imageUrl);
        } catch (err) {
          console.error('[useMetadataManager] Error extracting metadata:', err);
        }
      }
    }
  };

  // Set mounted ref to false on unmount
  const cleanupMountedRef = () => {
    return () => {
      mountedRef.current = false;
    };
  };

  return {
    previousImageUrl,
    metadataExtractionAttempted,
    mountedRef,
    resetMetadataExtractionFlag,
    attemptMetadataExtraction,
    cleanupMountedRef
  };
};
