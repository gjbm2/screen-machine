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
  const refreshIntervalRef = useRef(refreshInterval);
  const lastRunTimeRef = useRef<number>(0);
  const MIN_CHECK_INTERVAL = 10000; // Minimum 10 seconds between checks
  
  // Keep refs updated with latest values
  useEffect(() => {
    outputUrlRef.current = outputUrl;
    isLoadingRef.current = isLoading;
    isTransitioningRef.current = isTransitioning;
    enabledRef.current = enabled;
    refreshIntervalRef.current = refreshInterval;
    
    // Log refresh interval changes for debugging - but limit frequency
    const now = Date.now();
    if (now - lastRunTimeRef.current > 30000) { // Log at most every 30 seconds
      console.log('[useImageCheckPoller] Refresh interval updated:', refreshInterval, 'seconds');
      lastRunTimeRef.current = now;
    }
  }, [outputUrl, isLoading, isTransitioning, enabled, refreshInterval]);
  
  // Set up the mounted ref
  useEffect(() => {
    mountedRef.current = true;
    console.log('[useImageCheckPoller] Component mounted, polling enabled:', enabled);
    return () => {
      mountedRef.current = false;
      console.log('[useImageCheckPoller] Component unmounted, cleaning up');
    };
  }, [enabled]);
  
  // Create the polling callback
  const handlePoll = useCallback(() => {
    const currentOutputUrl = outputUrlRef.current;
    const currentIsLoading = isLoadingRef.current;
    const currentIsTransitioning = isTransitioningRef.current;
    const currentEnabled = enabledRef.current;
    
    if (!currentOutputUrl || currentIsLoading || currentIsTransitioning) {
      const now = Date.now();
      if (now - lastRunTimeRef.current > 30000) { // Log at most every 30 seconds
        console.log('[useImageCheckPoller] Skipping poll - conditions not met:', {
          hasUrl: !!currentOutputUrl,
          isLoading: currentIsLoading,
          isTransitioning: currentIsTransitioning
        });
        lastRunTimeRef.current = now;
      }
      return;
    }
    
    // Enforce minimum time between checks
    const now = Date.now();
    if (now - lastRunTimeRef.current < MIN_CHECK_INTERVAL) {
      return; // Silently skip if called too frequently
    }
    lastRunTimeRef.current = now;
    
    console.log('[useImageCheckPoller] Checking for image updates...');
    setIsChecking(true);
    
    // Always update the last check time, even if not enabled
    const currentTime = new Date();
    setLastCheckTime(currentTime);
    
    if (!currentEnabled) {
      setIsChecking(false);
      console.log('[useImageCheckPoller] Polling disabled, skipping actual check');
      return;
    }
    
    // Log the URL we're checking
    console.log('[useImageCheckPoller] Checking URL for modifications:', currentOutputUrl);
    
    checkImageModified(currentOutputUrl).then(changed => {
      if (!mountedRef.current) return; // Skip if component unmounted
      
      setIsChecking(false);
      console.log('[useImageCheckPoller] Check result - image changed:', changed);
      
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
  }, []); // Empty dependencies since we use refs
  
  // Use the interval poller with the specified refresh interval
  const { isPolling } = useIntervalPoller(
    !!outputUrl && enabled, // Only enable if we have a URL and polling is enabled
    Math.max(refreshInterval || 30, 30), // Minimum 30 seconds
    handlePoll,
    outputUrl ? [outputUrl] : [] // Ensure we always pass a valid array even when outputUrl is null
  );
  
  // Manual poll function for external triggers
  const pollNow = useCallback(() => {
    console.log('[useImageCheckPoller] Manual poll triggered');
    handlePoll();
  }, [handlePoll]);
  
  // Log polling status on changes
  useEffect(() => {
    const now = Date.now();
    if (now - lastRunTimeRef.current > 30000) { // Log at most every 30 seconds
      console.log('[useImageCheckPoller] Polling status changed:', { 
        isPolling,
        enabled,
        refreshInterval: refreshIntervalRef.current
      });
      lastRunTimeRef.current = now;
    }
  }, [isPolling, enabled]);
  
  return {
    mountedRef,
    pollNow,
    isPolling,
    isChecking,
    lastCheckTime
  };
};
