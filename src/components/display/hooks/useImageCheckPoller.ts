
import { useCallback, useRef, useEffect } from 'react';
import { useIntervalPoller } from './useIntervalPoller';

export const useImageCheckPoller = (
  outputUrl: string | null,
  refreshInterval: number,
  isLoading: boolean,
  isTransitioning: boolean,
  checkImageModified: (url: string) => Promise<boolean>,
  loadNewImage: (url: string) => void,
  extractMetadata: (url: string) => Promise<Record<string, string>>,
  enabled: boolean
) => {
  // Create refs to avoid dependency cycles
  const mountedRef = useRef<boolean>(true);
  
  // Set up the mounted ref
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);
  
  // Create the polling callback
  const handlePoll = useCallback(() => {
    if (!outputUrl || isLoading || isTransitioning || !enabled) {
      return;
    }
    
    console.log('[useImageCheckPoller] Checking for image updates...');
    checkImageModified(outputUrl).then(changed => {
      if (!mountedRef.current) return; // Skip if component unmounted
      
      if (changed) {
        console.log('[useImageCheckPoller] Image changed, reloading...');
        loadNewImage(outputUrl);
        // Automatically extract metadata when image changes
        extractMetadata(outputUrl).catch(err => {
          if (mountedRef.current) {
            console.error('[useImageCheckPoller] Error extracting metadata:', err);
          }
        });
      }
    }).catch(err => {
      if (mountedRef.current) {
        console.error('[useImageCheckPoller] Error checking image modifications:', err);
      }
    });
  }, [outputUrl, isLoading, isTransitioning, checkImageModified, loadNewImage, extractMetadata, enabled]);
  
  // Use the interval poller
  const { isPolling } = useIntervalPoller(
    enabled && !!outputUrl,
    refreshInterval,
    handlePoll,
    [outputUrl, isLoading, isTransitioning, refreshInterval, handlePoll]
  );
  
  return {
    mountedRef,
    pollNow: handlePoll,
    isPolling
  };
};
