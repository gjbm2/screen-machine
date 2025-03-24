
import { useEffect } from 'react';
import { fetchOutputFiles } from '@/components/display/utils';

export const useDebugFiles = (
  debugMode: boolean,
  setOutputFiles: (files: string[]) => void
) => {
  // Fetch available output files in debug mode
  useEffect(() => {
    let isMounted = true;
    
    if (debugMode) {
      console.log('[useDebugFiles] Debug mode active, fetching output files');
      fetchOutputFiles()
        .then(files => {
          if (isMounted) {
            console.log('[useDebugFiles] Files fetched:', files);
            setOutputFiles(files);
          }
        })
        .catch(err => {
          console.error('[useDebugFiles] Error fetching files:', err);
        });
    }
    
    return () => {
      isMounted = false;
    };
  }, [debugMode, setOutputFiles]);
};
