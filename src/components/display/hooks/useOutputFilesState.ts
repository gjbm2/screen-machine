
import { useState } from 'react';
import { fetchOutputFiles } from '../utils';

export const useOutputFilesState = () => {
  const [outputFiles, setOutputFiles] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  const loadOutputFiles = async () => {
    try {
      const files = await fetchOutputFiles();
      setOutputFiles(files);
    } catch (err) {
      console.error('Error loading output files:', err);
      setError('Failed to load output files');
    }
  };

  return {
    outputFiles,
    setOutputFiles,
    error,
    setError,
    loadOutputFiles
  };
};
