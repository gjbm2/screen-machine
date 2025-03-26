
import { useState, useRef, useCallback } from 'react';
import { fetchOutputFiles } from '../utils';

export const useOutputFilesState = () => {
  const [outputFiles, setOutputFiles] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const lastLoadTimeRef = useRef<number>(0);
  const MIN_LOAD_INTERVAL = 5000; // Minimum 5 seconds between loads

  const loadOutputFiles = useCallback(async () => {
    // Skip if already loading
    if (isLoading) return;
    
    // Add throttling to prevent excessive calls
    const now = Date.now();
    if (now - lastLoadTimeRef.current < MIN_LOAD_INTERVAL) {
      console.log('[useOutputFilesState] Skipping load, throttled:', 
        (MIN_LOAD_INTERVAL - (now - lastLoadTimeRef.current)) / 1000, 'seconds remaining');
      return;
    }
    
    setIsLoading(true);
    lastLoadTimeRef.current = now;
    
    try {
      const files = await fetchOutputFiles();
      setOutputFiles(files);
      setError(null);
    } catch (err) {
      console.error('Error loading output files:', err);
      setError('Failed to load output files');
    } finally {
      setIsLoading(false);
    }
  }, [isLoading]);

  return {
    outputFiles,
    setOutputFiles,
    error,
    setError,
    loadOutputFiles,
    isLoading
  };
};
