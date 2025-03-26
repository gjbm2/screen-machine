
import { useState, useRef, useCallback } from 'react';
import { fetchOutputFiles } from '../utils';

export const useOutputFilesState = () => {
  const [outputFiles, setOutputFiles] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const lastLoadTimeRef = useRef<number>(0);
  const isFetchingRef = useRef<boolean>(false);
  const MIN_LOAD_INTERVAL = 10000; // Increase to 10 seconds between loads

  const loadOutputFiles = useCallback(async () => {
    // Skip if already loading or if another fetch is in progress
    if (isLoading || isFetchingRef.current) {
      console.log('[useOutputFilesState] Skipping load, already fetching');
      return;
    }
    
    // Add stronger throttling to prevent excessive calls
    const now = Date.now();
    if (now - lastLoadTimeRef.current < MIN_LOAD_INTERVAL) {
      console.log('[useOutputFilesState] Skipping load, throttled:', 
        Math.ceil((MIN_LOAD_INTERVAL - (now - lastLoadTimeRef.current)) / 1000), 'seconds remaining');
      return;
    }
    
    setIsLoading(true);
    isFetchingRef.current = true;
    lastLoadTimeRef.current = now;
    
    try {
      const files = await fetchOutputFiles();
      // Only update state if files actually changed to prevent unnecessary re-renders
      if (JSON.stringify(files) !== JSON.stringify(outputFiles)) {
        setOutputFiles(files);
        setError(null);
      }
    } catch (err) {
      console.error('Error loading output files:', err);
      setError('Failed to load output files');
    } finally {
      setIsLoading(false);
      // Add a slight delay before allowing another fetch to prevent rapid sequential calls
      setTimeout(() => {
        isFetchingRef.current = false;
      }, 500);
    }
  }, [outputFiles, isLoading]);

  return {
    outputFiles,
    setOutputFiles,
    error,
    setError,
    loadOutputFiles,
    isLoading
  };
};
