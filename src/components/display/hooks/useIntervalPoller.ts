
import { useEffect, useRef, useState } from 'react';

export const useIntervalPoller = (
  enabled: boolean,
  intervalSeconds: number,
  callback: () => void,
  dependencies: any[] = [] // Provide empty array as default
) => {
  const [isPolling, setIsPolling] = useState(false);
  const intervalRef = useRef<NodeJS.Timeout | null>(null);
  const callbackRef = useRef(callback); // Store callback in ref to avoid dependency issues
  const lastRunRef = useRef<number>(0);
  
  // Update callback ref when callback changes
  useEffect(() => {
    callbackRef.current = callback;
  }, [callback]);
  
  // Convert seconds to milliseconds for setTimeout
  const intervalMs = Math.max(5, intervalSeconds) * 1000; // Minimum 5 seconds
  
  // Function to start polling
  const startPolling = () => {
    console.log('[useIntervalPoller] Starting polling with interval:', intervalSeconds, 'seconds');
    
    // Clear any existing interval first
    if (intervalRef.current) {
      clearTimeout(intervalRef.current);
      intervalRef.current = null;
    }
    
    // Set flag
    setIsPolling(true);
    
    // Use recursive setTimeout instead of setInterval for more control
    const scheduleNextRun = () => {
      // Use timeouts instead of intervals to prevent overlapping executions
      intervalRef.current = setTimeout(() => {
        if (!enabled) {
          setIsPolling(false);
          return;
        }
        
        const now = Date.now();
        // Don't log this every time to reduce console spam
        if (now - lastRunRef.current > 30000) { // Log only every 30 seconds
          console.log('[useIntervalPoller] Polling interval triggered');
        }
        
        lastRunRef.current = now;
        
        // Call the callback
        callbackRef.current();
        
        // Schedule next run
        scheduleNextRun();
      }, intervalMs);
    };
    
    // Start the polling
    scheduleNextRun();
  };
  
  // Function to stop polling
  const stopPolling = () => {
    console.log('[useIntervalPoller] Stopping polling');
    
    if (intervalRef.current) {
      clearTimeout(intervalRef.current);
      intervalRef.current = null;
    }
    
    setIsPolling(false);
  };
  
  // Handle the polling lifecycle based on enabled state
  useEffect(() => {
    // Only log when enabled state changes
    if (enabled) {
      console.log('[useIntervalPoller] Polling enabled with interval:', intervalSeconds);
      startPolling();
      
      // Run the callback immediately on start
      callbackRef.current();
      lastRunRef.current = Date.now();
    } else if (isPolling) {
      console.log('[useIntervalPoller] Polling disabled');
      stopPolling();
    }
    
    // Cleanup on unmount
    return () => {
      if (intervalRef.current) {
        clearTimeout(intervalRef.current);
        intervalRef.current = null;
      }
    };
  }, [enabled, intervalMs, isPolling, intervalSeconds]);
  
  // Additionally watch dependencies for immediate callback execution
  useEffect(() => {
    if (enabled && dependencies.length > 0) {
      // Run callback when dependencies change but don't restart the polling
      callbackRef.current();
    }
  }, [...(Array.isArray(dependencies) ? dependencies : [])]);
  
  return {
    isPolling,
    startPolling,
    stopPolling
  };
};
