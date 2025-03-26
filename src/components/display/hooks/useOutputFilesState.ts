
import { useState } from 'react';
import { fetchOutputFiles } from '../utils';

export const useOutputFilesState = () => {
  const [outputFiles, setOutputFiles] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const loadOutputFiles = async () => {
    // Skip if already loading
    if (isLoading) return;
    
    setIsLoading(true);
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
  };

  return {
    outputFiles,
    setOutputFiles,
    error,
    setError,
    loadOutputFiles,
    isLoading
  };
};
