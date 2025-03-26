
import { useEffect, useRef, useState } from 'react';

export const useIntervalPoller = (
  enabled: boolean,
  intervalSeconds: number,
  callback: () => void,
  dependencies: any[] = [] // Provide empty array as default
) => {
  const [isPolling, setIsPolling] = useState(false);
  const intervalRef = useRef<NodeJS.Timeout | null>(null);
  
  // Convert seconds to milliseconds for setTimeout
  const intervalMs = Math.max(1, intervalSeconds) * 1000;
  
  // Function to start polling
  const startPolling = () => {
    console.log('[useIntervalPoller] Starting polling with interval:', intervalSeconds, 'seconds');
    
    // Clear any existing interval first
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
    
    // Set new interval
    setIsPolling(true);
    intervalRef.current = setInterval(() => {
      console.log('[useIntervalPoller] Polling interval triggered');
      callback();
    }, intervalMs);
  };
  
  // Function to stop polling
  const stopPolling = () => {
    console.log('[useIntervalPoller] Stopping polling');
    
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
    
    setIsPolling(false);
  };
  
  // Handle the polling lifecycle based on enabled state
  useEffect(() => {
    console.log('[useIntervalPoller] Effect running, enabled:', enabled);
    
    if (enabled) {
      startPolling();
      
      // Run the callback immediately on start
      callback();
    } else {
      stopPolling();
    }
    
    // Cleanup on unmount
    return () => {
      console.log('[useIntervalPoller] Cleaning up interval');
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };
    // Ensure dependencies is always an array, even if undefined is passed
    // This fixes the "Cannot read property 'length' of undefined" error
  }, [enabled, intervalMs, callback, ...(Array.isArray(dependencies) ? dependencies : [])]);
  
  return {
    isPolling,
    startPolling,
    stopPolling
  };
};
