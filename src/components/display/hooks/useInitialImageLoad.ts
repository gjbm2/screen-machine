
import { useEffect, useRef } from 'react';

export const useInitialImageLoad = (
  outputUrl: string | null,
  isTransitioning: boolean,
  isLoading: boolean,
  loadImage: (url: string) => void,
  extractMetadata: (url: string) => Promise<void>,
  debugMode: boolean
) => {
  // Track whether we've loaded the initial image
  const initialLoadCompleted = useRef<boolean>(false);
  // Track component mounted state
  const mountedRef = useRef<boolean>(true);
  
  // Set mounted ref to false on unmount
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);
  
  // Handle initial image loading
  useEffect(() => {
    if (!outputUrl && !debugMode) {
      return;
    }

    // Logging for debugging
    console.log('[useInitialImageLoad] URL:', outputUrl);
    console.log('[useInitialImageLoad] Is transitioning:', isTransitioning);
    console.log('[useInitialImageLoad] Is loading:', isLoading);
    console.log('[useInitialImageLoad] Initial load completed:', initialLoadCompleted.current);
    
    // Initial load
    if (outputUrl && !isTransitioning && !initialLoadCompleted.current) {
      console.log('[useInitialImageLoad] Initial image load for:', outputUrl);
      loadImage(outputUrl);
      initialLoadCompleted.current = true;
      
      // Extract metadata on initial load
      if (!isLoading && mountedRef.current) {
        extractMetadata(outputUrl).catch(err => {
          if (mountedRef.current) {
            console.error('[useInitialImageLoad] Error extracting metadata on initial load:', err);
          }
        });
      }
    }
    
    // Reset flag if there's no URL
    if (!outputUrl) {
      initialLoadCompleted.current = false;
    }
  }, [outputUrl, isTransitioning, isLoading, loadImage, extractMetadata, debugMode]);
  
  return {
    initialLoadCompleted,
    mountedRef
  };
};
