import { useCallback, useRef, useEffect, useState } from 'react';
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
  const [isChecking, setIsChecking] = useState(false);
  const [lastCheckTime, setLastCheckTime] = useState<Date | null>(null);
  const outputUrlRef = useRef(outputUrl);
  const isLoadingRef = useRef(isLoading);
  const isTransitioningRef = useRef(isTransitioning);
  const enabledRef = useRef(enabled);
  
  // Keep refs updated with latest values
  useEffect(() => {
    outputUrlRef.current = outputUrl;
    isLoadingRef.current = isLoading;
    isTransitioningRef.current = isTransitioning;
    enabledRef.current = enabled;
  }, [outputUrl, isLoading, isTransitioning, enabled]);
  
  // Set up the mounted ref
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);
  
  // Create the polling callback
  const handlePoll = useCallback(() => {
    const currentOutputUrl = outputUrlRef.current;
    const currentIsLoading = isLoadingRef.current;
    const currentIsTransitioning = isTransitioningRef.current;
    const currentEnabled = enabledRef.current;
    
    if (!currentOutputUrl || currentIsLoading || currentIsTransitioning) {
      return;
    }
    
    console.log('[useImageCheckPoller] Checking for image updates...');
    setIsChecking(true);
    
    // Always update the last check time, even if not enabled
    const currentTime = new Date();
    setLastCheckTime(currentTime);
    
    if (!currentEnabled) {
      setIsChecking(false);
      return;
    }
    
    checkImageModified(currentOutputUrl).then(changed => {
      if (!mountedRef.current) return; // Skip if component unmounted
      
      setIsChecking(false);
      
      if (changed) {
        console.log('[useImageCheckPoller] Image changed, reloading...');
        // Load the new image with the transition effect
        loadNewImage(currentOutputUrl);
        
        // Automatically extract metadata when image changes
        extractMetadata(currentOutputUrl).catch(err => {
          if (mountedRef.current) {
            console.error('[useImageCheckPoller] Error extracting metadata:', err);
          }
        });
      }
    }).catch(err => {
      if (mountedRef.current) {
        setIsChecking(false);
        console.error('[useImageCheckPoller] Error checking image modifications:', err);
      }
    });
  }, [checkImageModified, loadNewImage, extractMetadata]); // Minimal dependencies that rarely change
  
  // Use the interval poller with the specified refresh interval
  const { isPolling } = useIntervalPoller(
    !!outputUrl, // Run the poller if we have a URL, regardless of enabled state
    refreshInterval || 5, // Default to 5 seconds if not specified
    handlePoll,
    [outputUrl, isLoading, isTransitioning, refreshInterval]
  );
  
  return {
    mountedRef,
    pollNow: handlePoll,
    isPolling,
    isChecking,
    lastCheckTime
  };
};
