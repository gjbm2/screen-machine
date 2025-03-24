
import { useEffect, useRef } from 'react';

export const useIntervalPoller = (
  enabled: boolean,
  intervalTime: number,
  onPoll: () => void,
  dependencies: any[] = []
) => {
  // Use a ref to track the current interval ID
  const intervalIdRef = useRef<number | null>(null);
  // Track component mounted state
  const mountedRef = useRef<boolean>(true);
  
  // Set mounted ref to false on unmount
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      
      // Clear interval on unmount
      if (intervalIdRef.current !== null) {
        console.log('[useIntervalPoller] Clearing interval on unmount');
        window.clearInterval(intervalIdRef.current);
        intervalIdRef.current = null;
      }
    };
  }, []);
  
  // Setup and manage the interval
  useEffect(() => {
    // Skip if polling is disabled, onPoll is not provided, or interval time is invalid
    if (!enabled || typeof onPoll !== 'function' || intervalTime <= 0) {
      // Clear any existing interval if disabled
      if (intervalIdRef.current !== null) {
        console.log('[useIntervalPoller] Clearing interval because polling is disabled');
        window.clearInterval(intervalIdRef.current);
        intervalIdRef.current = null;
      }
      return;
    }
    
    console.log('[useIntervalPoller] Setting up polling interval:', intervalTime);
    
    // Clear any existing interval
    if (intervalIdRef.current !== null) {
      window.clearInterval(intervalIdRef.current);
      intervalIdRef.current = null;
    }
    
    // Create new interval, ensuring it doesn't poll too fast (minimum 1 second)
    const safeIntervalTime = Math.max(1, intervalTime);
    intervalIdRef.current = window.setInterval(() => {
      if (!mountedRef.current) {
        // Component unmounted, clear interval
        if (intervalIdRef.current !== null) {
          console.log('[useIntervalPoller] Component unmounted, clearing interval');
          window.clearInterval(intervalIdRef.current);
          intervalIdRef.current = null;
        }
        return;
      }
      
      // Execute the polling callback
      onPoll();
    }, safeIntervalTime * 1000);
    
    // Clean up on dependency changes or unmount
    return () => {
      if (intervalIdRef.current !== null) {
        console.log('[useIntervalPoller] Clearing interval due to dependency changes');
        window.clearInterval(intervalIdRef.current);
        intervalIdRef.current = null;
      }
    };
  }, [enabled, intervalTime, onPoll, ...dependencies]);
  
  return {
    isPolling: intervalIdRef.current !== null,
    mountedRef
  };
};
