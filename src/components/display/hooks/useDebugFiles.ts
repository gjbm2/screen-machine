
import { useEffect, useRef } from 'react';
import { fetchOutputFiles } from '@/components/display/utils';

export const useDebugFiles = (
  debugMode: boolean,
  setOutputFiles: (files: string[]) => void
) => {
  const fetchTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const lastFetchRef = useRef<number>(0);
  
  // Fetch available output files in debug mode
  useEffect(() => {
    let isMounted = true;
    const MIN_FETCH_INTERVAL = 5000; // Minimum time between fetches (5 seconds)
    
    if (debugMode) {
      const now = Date.now();
      const timeSinceLastFetch = now - lastFetchRef.current;
      
      // Only fetch if sufficient time has passed
      if (timeSinceLastFetch > MIN_FETCH_INTERVAL) {
        console.log('[useDebugFiles] Debug mode active, fetching output files');
        
        lastFetchRef.current = now;
        
        fetchOutputFiles()
          .then(files => {
            if (isMounted) {
              setOutputFiles(files);
            }
          })
          .catch(err => {
            console.error('[useDebugFiles] Error fetching files:', err);
          });
      } else if (!fetchTimeoutRef.current) {
        // Schedule a fetch after the minimum interval
        fetchTimeoutRef.current = setTimeout(() => {
          if (isMounted && debugMode) {
            console.log('[useDebugFiles] Scheduled fetch of output files');
            lastFetchRef.current = Date.now();
            
            fetchOutputFiles()
              .then(files => {
                if (isMounted) {
                  setOutputFiles(files);
                }
              })
              .catch(err => {
                console.error('[useDebugFiles] Error fetching files:', err);
              });
          }
          fetchTimeoutRef.current = null;
        }, MIN_FETCH_INTERVAL - timeSinceLastFetch);
      }
    }
    
    return () => {
      isMounted = false;
      if (fetchTimeoutRef.current) {
        clearTimeout(fetchTimeoutRef.current);
        fetchTimeoutRef.current = null;
      }
    };
  }, [debugMode, setOutputFiles]);
};
