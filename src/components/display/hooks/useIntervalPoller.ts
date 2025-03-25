
import { useState, useEffect, useRef } from 'react';

export const useIntervalPoller = (
  enabled: boolean,
  intervalSeconds: number,
  callback: () => void,
  dependencies: any[] = []
) => {
  const [isPolling, setIsPolling] = useState(false);
  const callbackRef = useRef<() => void>(callback);
  const intervalIdRef = useRef<number | null>(null);
  const enabledRef = useRef(enabled);
  const intervalSecondsRef = useRef(intervalSeconds);
  
  // Update refs when props change
  useEffect(() => {
    callbackRef.current = callback;
    enabledRef.current = enabled;
    intervalSecondsRef.current = Math.max(1, intervalSeconds); // Ensure minimum 1 second
  }, [callback, enabled, intervalSeconds]);
  
  // Set up the interval that will call the latest callback from the ref
  useEffect(() => {
    // Clear any existing interval before setting up a new one
    if (intervalIdRef.current !== null) {
      clearInterval(intervalIdRef.current);
      intervalIdRef.current = null;
    }
    
    // Only set up polling if enabled and we have a positive interval
    if (enabledRef.current && intervalSecondsRef.current > 0) {
      console.log(`[useIntervalPoller] Starting polling with interval: ${intervalSecondsRef.current} seconds`);
      setIsPolling(true);
      
      // Use window.setInterval and store the numeric ID
      intervalIdRef.current = window.setInterval(() => {
        console.log(`[useIntervalPoller] Interval triggered, calling callback`);
        callbackRef.current();
      }, intervalSecondsRef.current * 1000);
    } else {
      console.log(`[useIntervalPoller] Polling disabled or invalid interval: ${intervalSecondsRef.current}`);
      setIsPolling(false);
    }
    
    // Cleanup function
    return () => {
      if (intervalIdRef.current !== null) {
        console.log(`[useIntervalPoller] Cleaning up interval`);
        clearInterval(intervalIdRef.current);
        intervalIdRef.current = null;
        setIsPolling(false);
      }
    };
  }, [...dependencies]); // Only re-run this effect when dependencies change
  
  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (intervalIdRef.current !== null) {
        console.log(`[useIntervalPoller] Component unmounting, cleaning up interval`);
        clearInterval(intervalIdRef.current);
        intervalIdRef.current = null;
      }
    };
  }, []);
  
  return { isPolling };
};
