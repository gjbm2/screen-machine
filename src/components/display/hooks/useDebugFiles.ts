
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
  const isFetchingRef = useRef<boolean>(false);
  const MIN_FETCH_INTERVAL = 60000; // 60 seconds between fetches
  
  // Create a stable fetch function that's memoized
  const fetchFiles = useCallback(async () => {
    if (!isMountedRef.current || !debugMode || isFetchingRef.current) return;
    
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
    isFetchingRef.current = true;
    
    try {
      const files = await fetchOutputFiles();
      if (isMountedRef.current) {
        // Make sure files is an array even if the API returns something unexpected
        const safeFiles = Array.isArray(files) ? files : [];
        console.log('[useDebugFiles] Fetched output files:', safeFiles);
        
        // Pass the files array to setOutputFiles
        setOutputFiles(safeFiles);
      }
    } catch (err) {
      console.error('[useDebugFiles] Error fetching files:', err);
    } finally {
      isFetchingRef.current = false;
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
