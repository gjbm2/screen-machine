
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
  
  // Set up the mounted ref
  useEffect(() => {
    mountedRef.current = true;
    console.log('[useImageCheckPoller] Component mounted');
    return () => {
      mountedRef.current = false;
      console.log('[useImageCheckPoller] Component unmounted, cleaning up');
    };
  }, []);
  
  // Manual poll function for external triggers
  const pollNow = useCallback(async () => {
    console.log('[useImageCheckPoller] Manual poll triggered');
    
    if (!outputUrl || isLoading || isTransitioning) {
      console.log('[useImageCheckPoller] Skipping poll - conditions not met:', {
        hasUrl: !!outputUrl,
        isLoading,
        isTransitioning
      });
      return false;
    }
    
    console.log('[useImageCheckPoller] Checking for image updates...');
    setIsChecking(true);
    
    // Always update the last check time
    const currentTime = new Date();
    console.log('[useImageCheckPoller] Setting last check time:', currentTime.toISOString());
    setLastCheckTime(currentTime);
    
    // Log the URL we're checking
    console.log('[useImageCheckPoller] Checking URL for modifications:', outputUrl);
    
    try {
      const changed = await checkImageModified(outputUrl);
      
      if (!mountedRef.current) return false; // Skip if component unmounted
      
      setIsChecking(false);
      console.log('[useImageCheckPoller] Check result - image changed:', changed);
      
      if (changed) {
        console.log('[useImageCheckPoller] Image changed, reloading...');
        // Load the new image with the transition effect
        loadNewImage(outputUrl);
        
        // Automatically extract metadata when image changes
        try {
          await extractMetadata(outputUrl);
        } catch (err) {
          if (mountedRef.current) {
            console.error('[useImageCheckPoller] Error extracting metadata:', err);
          }
        }
      }
      
      return changed;
    } catch (err) {
      if (mountedRef.current) {
        setIsChecking(false);
        console.error('[useImageCheckPoller] Error checking image modifications:', err);
      }
      return false;
    }
  }, [outputUrl, isLoading, isTransitioning, checkImageModified, loadNewImage, extractMetadata]);
  
  // We're completely removing polling, so we don't need to use useIntervalPoller
  // This is just a stub to maintain the return interface
  const { isPolling } = { isPolling: false };
  
  return {
    mountedRef,
    pollNow,
    isPolling: false, // Always return false to disable polling
    isChecking,
    lastCheckTime
  };
};
