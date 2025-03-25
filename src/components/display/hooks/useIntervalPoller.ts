
import { useState, useEffect, useRef } from 'react';

export const useIntervalPoller = (
  enabled: boolean,
  intervalSeconds: number,
  callback: () => void,
  dependencies: any[] = []
) => {
  const [isPolling, setIsPolling] = useState(false);
  const callbackRef = useRef<() => void>(callback);
  
  // Update the callback ref when callback changes
  useEffect(() => {
    callbackRef.current = callback;
  }, [callback]);
  
  // Set up the interval that will call the latest callback from the ref
  useEffect(() => {
    // Only set up polling if enabled and we have a positive interval
    if (enabled && intervalSeconds > 0) {
      console.log(`[useIntervalPoller] Starting polling with interval: ${intervalSeconds} seconds`);
      setIsPolling(true);
      
      const intervalId = setInterval(() => {
        console.log(`[useIntervalPoller] Interval triggered, calling callback`);
        callbackRef.current();
      }, intervalSeconds * 1000);
      
      return () => {
        console.log(`[useIntervalPoller] Cleaning up interval`);
        clearInterval(intervalId);
        setIsPolling(false);
      };
    } else {
      setIsPolling(false);
    }
  }, [enabled, intervalSeconds]); // Removed callback from dependencies to prevent resetting
  
  return { isPolling };
};
