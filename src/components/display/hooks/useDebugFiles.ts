
import { useEffect, useRef, useCallback } from 'react';
import { fetchOutputFiles } from '@/components/display/utils';

export const useDebugFiles = (
  debugMode: boolean,
  setOutputFiles: (files: string[]) => void
) => {
  const fetchTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const lastFetchRef = useRef<number>(0);
  const isMountedRef = useRef<boolean>(true);
  
  // Create a stable fetch function that's memoized
  const fetchFiles = useCallback(async () => {
    if (!isMountedRef.current || !debugMode) return;
    
    console.log('[useDebugFiles] Fetching output files');
    lastFetchRef.current = Date.now();
    
    try {
      const files = await fetchOutputFiles();
      if (isMountedRef.current) {
        setOutputFiles(files);
      }
    } catch (err) {
      console.error('[useDebugFiles] Error fetching files:', err);
    }
  }, [debugMode, setOutputFiles]);
  
  // Fetch available output files in debug mode
  useEffect(() => {
    isMountedRef.current = true;
    const MIN_FETCH_INTERVAL = 10000; // Increase to 10 seconds minimum between fetches
    
    if (debugMode) {
      const now = Date.now();
      const timeSinceLastFetch = now - lastFetchRef.current;
      
      // Only fetch if sufficient time has passed
      if (timeSinceLastFetch > MIN_FETCH_INTERVAL) {
        console.log('[useDebugFiles] Initial fetch of output files');
        fetchFiles();
      } else if (!fetchTimeoutRef.current) {
        // Schedule a fetch after the minimum interval
        console.log('[useDebugFiles] Scheduling delayed fetch in', 
          (MIN_FETCH_INTERVAL - timeSinceLastFetch) / 1000, 'seconds');
          
        fetchTimeoutRef.current = setTimeout(() => {
          fetchTimeoutRef.current = null;
          if (isMountedRef.current && debugMode) {
            fetchFiles();
          }
        }, MIN_FETCH_INTERVAL - timeSinceLastFetch);
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
