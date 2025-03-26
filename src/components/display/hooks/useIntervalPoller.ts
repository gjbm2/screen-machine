
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
  const isInitialRunDoneRef = useRef<boolean>(false);
  
  // Update callback ref when callback changes
  useEffect(() => {
    callbackRef.current = callback;
  }, [callback]);
  
  // Convert seconds to milliseconds for setTimeout
  const intervalMs = Math.max(30, intervalSeconds) * 1000; // Minimum 30 seconds
  
  // Function to start polling
  const startPolling = () => {
    // Only log when actually starting polling, not on every render
    if (!isPolling) {
      console.log('[useIntervalPoller] Starting polling with interval:', intervalSeconds, 'seconds');
    }
    
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
        if (now - lastRunRef.current > 60000) { // Log only every 60 seconds
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
    // Only log when actually stopping polling
    if (isPolling) {
      console.log('[useIntervalPoller] Stopping polling');
    }
    
    if (intervalRef.current) {
      clearTimeout(intervalRef.current);
      intervalRef.current = null;
    }
    
    setIsPolling(false);
  };
  
  // Handle the polling lifecycle based on enabled state
  useEffect(() => {
    // Only run this effect when enabled changes or the component mounts
    if (enabled) {
      startPolling();
      
      // Run the callback immediately on start if not done already
      if (!isInitialRunDoneRef.current) {
        console.log('[useIntervalPoller] Running initial poll');
        callbackRef.current();
        lastRunRef.current = Date.now();
        isInitialRunDoneRef.current = true;
      }
    } else if (isPolling) {
      stopPolling();
    }
    
    // Cleanup on unmount
    return () => {
      if (intervalRef.current) {
        clearTimeout(intervalRef.current);
        intervalRef.current = null;
      }
    };
  }, [enabled, intervalMs]);
  
  // Additionally watch dependencies for immediate callback execution,
  // but only if they actually change and have values
  useEffect(() => {
    // Skip if dependencies array is empty or component isn't mounted
    if (!dependencies.length || !enabled) return;
    
    // Check if enough time has passed to run again (avoid spam on rapid dependency changes)
    const now = Date.now();
    const timeSinceLastRun = now - lastRunRef.current;
    
    // Only run if at least 5 seconds have passed since last run
    if (timeSinceLastRun >= 5000) {
      console.log('[useIntervalPoller] Dependencies changed, running callback');
      callbackRef.current();
      lastRunRef.current = now;
      isInitialRunDoneRef.current = true;
    }
  }, dependencies);
  
  return {
    isPolling,
    startPolling,
    stopPolling
  };
};
