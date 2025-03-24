
import { useEffect } from 'react';
import { fetchOutputFiles } from '@/components/display/utils';

export const useDebugFiles = (
  debugMode: boolean,
  setOutputFiles: (files: string[]) => void
) => {
  // Fetch available output files in debug mode
  useEffect(() => {
    if (debugMode) {
      fetchOutputFiles().then(files => setOutputFiles(files));
    }
  }, [debugMode, setOutputFiles]);
};
