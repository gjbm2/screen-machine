
import { useState, useRef, useCallback } from 'react';
import { fetchOutputFiles } from '../utils';

export const useOutputFilesState = () => {
  const [outputFiles, setOutputFiles] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const lastLoadTimeRef = useRef<number>(0);
  const isFetchingRef = useRef<boolean>(false);
  const MIN_LOAD_INTERVAL = 60000; // 60 seconds between loads

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
      // Make sure files is an array even if the API returns something unexpected
      const safeFiles = Array.isArray(files) ? files : [];
      
      // Only update state if files actually changed to prevent unnecessary re-renders
      if (JSON.stringify(safeFiles) !== JSON.stringify(outputFiles)) {
        console.log('[useOutputFilesState] Files have changed, updating state with', safeFiles.length, 'files');
        setOutputFiles(safeFiles);
        setError(null);
      } else {
        console.log('[useOutputFilesState] Files unchanged, skipping state update');
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

  // Define a function to safely set output files from outside
  const setOutputFilesFromOutside = (files: string[]) => {
    // Ensure files is an array
    const safeFiles = Array.isArray(files) ? files : [];
    
    // Only update if different to prevent unnecessary re-renders
    if (JSON.stringify(safeFiles) !== JSON.stringify(outputFiles)) {
      console.log('[useOutputFilesState] Setting files from outside:', safeFiles);
      setOutputFiles(safeFiles);
      setError(null);
    }
  };

  return {
    outputFiles,
    setOutputFiles: setOutputFilesFromOutside,
    error,
    setError,
    loadOutputFiles,
    isLoading
  };
};
