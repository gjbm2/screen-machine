
import { useCallback } from 'react';

interface UseDebugPanelFileSelectorProps {
  selectFile: (file: string) => () => void;
  isCurrentFile: (file: string, imageUrl: string | null) => boolean;
  imageUrl: string | null;
}

export const useDebugPanelFileSelector = ({
  selectFile,
  isCurrentFile,
  imageUrl
}: UseDebugPanelFileSelectorProps) => {
  
  const selectFileHandler = useCallback((file: string) => {
    const handler = selectFile(file);
    handler();
  }, [selectFile]);

  const isCurrentFileHandler = useCallback((file: string) => {
    return isCurrentFile(file, imageUrl);
  }, [isCurrentFile, imageUrl]);

  return {
    selectFileHandler,
    isCurrentFileHandler
  };
};
