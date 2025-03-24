
import { useCallback } from 'react';

interface UseDebugPanelResizeProps {
  positionResizeHandler: (e: React.MouseEvent) => void;
}

export const useDebugPanelResize = ({
  positionResizeHandler
}: UseDebugPanelResizeProps) => {
  
  // We're using the positionResizeHandler as our main resize handler
  const handleResizeStartInternal = useCallback((e: React.MouseEvent) => {
    positionResizeHandler(e);
  }, [positionResizeHandler]);

  return {
    handleResizeStartInternal
  };
};
