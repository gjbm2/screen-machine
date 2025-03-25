import { useEffect, useRef, useState } from 'react';

export const useImageChangeDetector = (
  imageUrl: string | null,
  isLoading: boolean,
  isTransitioning: boolean,
  extractMetadata: (url: string) => Promise<Record<string, string>>
) => {
  // Track component mounted state - always define hooks first and in the same order
  const mountedRef = useRef<boolean>(true);
  
  // Track the last checked URL - keep consistent hook ordering
  const lastCheckedUrl = useRef<string | null>(null);
  
  // Track metadata loading state
  const [isLoadingMetadata, setIsLoadingMetadata] = useState(false);
  
  // Set mounted ref to false on unmount
  useEffect(() => {
    mountedRef.current = true;
    console.log('[useImageChangeDetector] Component mounted');
    
    return () => {
      console.log('[useImageChangeDetector] Component unmounting');
      mountedRef.current = false;
    };
  }, []);
  
  // Extract metadata whenever the image URL changes
  useEffect(() => {
    // Only proceed if we have a valid URL and it's different from last time
    if (mountedRef.current && 
        imageUrl && 
        imageUrl !== lastCheckedUrl.current && 
        !isLoading && 
        !isTransitioning) {
      console.log('[useImageChangeDetector] New image URL detected, extracting metadata:', imageUrl);
      lastCheckedUrl.current = imageUrl;
      
      // Set loading state to true before extraction
      setIsLoadingMetadata(true);
      
      extractMetadata(imageUrl)
        .catch(err => {
          if (mountedRef.current) {
            console.error('[useImageChangeDetector] Error extracting metadata on URL change:', err);
          }
        })
        .finally(() => {
          if (mountedRef.current) {
            setIsLoadingMetadata(false);
          }
        });
    }
  }, [imageUrl, isLoading, isTransitioning, extractMetadata]);
  
  return {
    lastCheckedUrl,
    mountedRef,
    isLoadingMetadata
  };
};
