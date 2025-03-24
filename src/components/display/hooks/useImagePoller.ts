
import { useEffect, useRef } from 'react';
import { DisplayParams } from '@/components/display/types';
import { processOutputParam, extractImageMetadata } from '@/components/display/utils';
import { toast } from 'sonner';

export const useImagePoller = (
  params: DisplayParams,
  imageUrl: string | null,
  isLoading: boolean,
  isTransitioning: boolean,
  loadNewImage: (url: string) => void,
  checkImageModified: (url: string) => Promise<boolean>,
  extractMetadataFromImage: (url: string) => Promise<Record<string, string>>
) => {
  // Use a ref to track the current interval ID
  const intervalIdRef = useRef<number | null>(null);
  const lastCheckedUrl = useRef<string | null>(null);
  // Track whether we've loaded the initial image
  const initialLoadCompleted = useRef<boolean>(false);
  // Track component mounted state
  const mountedRef = useRef<boolean>(true);
  
  // Set mounted ref to false on unmount
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      
      // Clear interval on unmount
      if (intervalIdRef.current !== null) {
        window.clearInterval(intervalIdRef.current);
        intervalIdRef.current = null;
      }
    };
  }, []);
  
  // Handle initial image loading and periodic checking
  useEffect(() => {
    if (!params.output && !params.debugMode) {
      return;
    }

    const processedUrl = processOutputParam(params.output);
    
    // Logging for debugging
    console.log('[useImagePoller] Processed URL:', processedUrl);
    console.log('[useImagePoller] Refresh interval:', params.refreshInterval);
    console.log('[useImagePoller] Is transitioning:', isTransitioning);
    console.log('[useImagePoller] Is loading:', isLoading);
    
    // Initial load
    if (processedUrl) {
      if (!isTransitioning && !initialLoadCompleted.current) {
        console.log('[useImagePoller] Initial image load for:', processedUrl);
        loadNewImage(processedUrl);
        initialLoadCompleted.current = true;
        
        // Extract metadata on initial load
        if (!isLoading && mountedRef.current) {
          extractMetadataFromImage(processedUrl).catch(err => {
            if (mountedRef.current) {
              console.error('[useImagePoller] Error extracting metadata on initial load:', err);
            }
          });
        }
      }
      
      // Clear any existing interval
      if (intervalIdRef.current !== null) {
        window.clearInterval(intervalIdRef.current);
        intervalIdRef.current = null;
      }
      
      // Set up new polling interval
      intervalIdRef.current = window.setInterval(() => {
        if (!mountedRef.current) {
          // Component unmounted, clear interval
          if (intervalIdRef.current !== null) {
            window.clearInterval(intervalIdRef.current);
            intervalIdRef.current = null;
          }
          return;
        }
        
        if (processedUrl && !isLoading && !isTransitioning) {
          console.log('[useImagePoller] Checking for image updates...');
          checkImageModified(processedUrl).then(changed => {
            if (!mountedRef.current) return; // Skip if component unmounted
            
            if (changed) {
              console.log('[useImagePoller] Image changed, reloading...');
              loadNewImage(processedUrl);
              // Automatically extract metadata when image changes
              extractMetadataFromImage(processedUrl).catch(err => {
                if (mountedRef.current) {
                  console.error('[useImagePoller] Error extracting metadata:', err);
                }
              });
            }
          }).catch(err => {
            if (mountedRef.current) {
              console.error('[useImagePoller] Error checking image modifications:', err);
            }
          });
        }
      }, params.refreshInterval * 1000);

      return () => {
        if (intervalIdRef.current !== null) {
          window.clearInterval(intervalIdRef.current);
          intervalIdRef.current = null;
        }
      };
    }
    
    // Reset flag if there's no URL
    initialLoadCompleted.current = false;
  }, [params.output, params.refreshInterval, params.debugMode, isLoading, isTransitioning, loadNewImage, checkImageModified, extractMetadataFromImage]);

  // Extract metadata whenever the image URL changes
  useEffect(() => {
    if (imageUrl && imageUrl !== lastCheckedUrl.current && !isLoading && !isTransitioning && mountedRef.current) {
      console.log('[useImagePoller] New image URL detected, extracting metadata:', imageUrl);
      lastCheckedUrl.current = imageUrl;
      
      extractMetadataFromImage(imageUrl).catch(err => {
        if (mountedRef.current) {
          console.error('[useImagePoller] Error extracting metadata on URL change:', err);
        }
      });
    }
  }, [imageUrl, isLoading, isTransitioning, extractMetadataFromImage]);

  // Enhanced manual check that handles metadata refresh
  const handleManualCheck = async (
    imageUrl: string | null,
    originalHandleManualCheck: () => Promise<boolean>,
    params: DisplayParams
  ): Promise<boolean> => {
    if (!mountedRef.current) return false; // Skip if component unmounted
    
    console.log('[useImagePoller] Manual check initiated');
    
    if (imageUrl) {
      // Call the base image check function without arguments
      const result = await originalHandleManualCheck();
      
      if (!mountedRef.current) return false; // Skip if component unmounted during async operation
      
      // Force metadata refresh regardless of whether the image changed
      try {
        console.log('[useImagePoller] Forcing metadata refresh on manual check');
        const extractedMetadata = await extractImageMetadata(imageUrl);
        
        if (!mountedRef.current) return false; // Skip if component unmounted during async operation
        
        console.log('[useImagePoller] Manually extracted metadata:', extractedMetadata);
        
        if (Object.keys(extractedMetadata).length === 0) {
          toast.warning("No metadata found in this image");
        }
      } catch (err) {
        if (mountedRef.current) {
          console.error('[useImagePoller] Error during manual metadata extraction:', err);
          toast.error("Failed to extract metadata");
        }
      }
      
      return result;
    }
    
    return false;
  };

  return {
    handleManualCheck
  };
};
