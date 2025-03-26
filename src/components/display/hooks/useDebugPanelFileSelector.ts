
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
  
  // Create a handler that can be used in onClick directly (for list items)
  const selectFileHandler = useCallback((file: string) => {
    console.log('[useDebugPanelFileSelector] Creating handler for file:', file);
    // Return the navigation function that was provided
    return selectFile(file);
  }, [selectFile]);
  
  // Create a direct handler for use with the custom URL input
  const selectFileDirectly = useCallback((file: string) => {
    console.log('[useDebugPanelFileSelector] Directly selecting file:', file);
    // Return the navigation function
    return selectFile(file);
  }, [selectFile]);
  
  // Create a callback to check if a file is currently selected
  const isCurrentFileHandler = useCallback((file: string) => {
    // Debug logging
    console.log('[useDebugPanelFileSelector] Checking if file is current:', { 
      file, 
      imageUrl, 
      isMatch: isCurrentFile(file, imageUrl)
    });
    
    return isCurrentFile(file, imageUrl);
  }, [isCurrentFile, imageUrl]);
  
  return {
    selectFileHandler,
    selectFileDirectly,
    isCurrentFileHandler
  };
};
