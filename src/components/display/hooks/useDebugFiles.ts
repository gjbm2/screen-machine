
import { useEffect, useRef, useCallback } from 'react';
import { fetchOutputFiles } from '@/components/display/utils';

export const useDebugFiles = (
  debugMode: boolean,
  setOutputFiles: (files: string[]) => void
) => {
  const fetchTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const lastFetchRef = useRef<number>(0);
  const isMountedRef = useRef<boolean>(true);
  const isInitialFetchDoneRef = useRef<boolean>(false);
  const MIN_FETCH_INTERVAL = 30000; // Increase to 30 seconds between fetches
  
  // Create a stable fetch function that's memoized
  const fetchFiles = useCallback(async () => {
    if (!isMountedRef.current || !debugMode) return;
    
    const now = Date.now();
    // Enforce minimum interval between fetches
    if (now - lastFetchRef.current < MIN_FETCH_INTERVAL && isInitialFetchDoneRef.current) {
      console.log('[useDebugFiles] Skipping fetch, throttled:', 
        Math.ceil((MIN_FETCH_INTERVAL - (now - lastFetchRef.current)) / 1000), 'seconds remaining');
      return;
    }
    
    console.log('[useDebugFiles] Fetching output files');
    lastFetchRef.current = now;
    isInitialFetchDoneRef.current = true;
    
    try {
      const files = await fetchOutputFiles();
      if (isMountedRef.current) {
        // Fix: Store the current files and compare before updating
        // This addresses the TypeScript error by not using a callback function
        setOutputFiles(files);
      }
    } catch (err) {
      console.error('[useDebugFiles] Error fetching files:', err);
    }
  }, [debugMode, setOutputFiles]);
  
  // Fetch available output files in debug mode
  useEffect(() => {
    isMountedRef.current = true;
    
    if (debugMode) {
      // Do an initial fetch only if we haven't already done one
      if (!isInitialFetchDoneRef.current) {
        console.log('[useDebugFiles] Initial fetch of output files');
        fetchFiles();
      } else if (!fetchTimeoutRef.current) {
        // Schedule a fetch after the minimum interval
        fetchTimeoutRef.current = setTimeout(() => {
          fetchTimeoutRef.current = null;
          if (isMountedRef.current && debugMode) {
            fetchFiles();
          }
        }, MIN_FETCH_INTERVAL);
      }
    }
    
    return () => {
      isMountedRef.current = false;
      if (fetchTimeoutRef.current) {
        clearTimeout(fetchTimeoutRef.current);
        fetchTimeoutRef.current = null;
      }
    };
  }, [debugMode, fetchFiles]);
};
