
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
        window.clearInterval(intervalIdRef.current);
        intervalIdRef.current = null;
      }
    };
  }, []);
  
  // Setup and manage the interval
  useEffect(() => {
    // Skip if polling is disabled or onPoll is not provided
    if (!enabled || typeof onPoll !== 'function') {
      return;
    }
    
    console.log('[useIntervalPoller] Setting up polling interval:', intervalTime);
    
    // Clear any existing interval
    if (intervalIdRef.current !== null) {
      window.clearInterval(intervalIdRef.current);
      intervalIdRef.current = null;
    }
    
    // Create new interval
    intervalIdRef.current = window.setInterval(() => {
      if (!mountedRef.current) {
        // Component unmounted, clear interval
        if (intervalIdRef.current !== null) {
          window.clearInterval(intervalIdRef.current);
          intervalIdRef.current = null;
        }
        return;
      }
      
      // Execute the polling callback
      onPoll();
    }, intervalTime * 1000);
    
    // Clean up on dependency changes or unmount
    return () => {
      if (intervalIdRef.current !== null) {
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
