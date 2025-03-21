
import { useState } from 'react';
import { ViewMode } from '../ImageDisplay';
import { useIsMobile } from '@/hooks/use-mobile';

interface UseImageHoverStateProps {
  viewMode: ViewMode;
}

export const useImageHoverState = ({ viewMode }: UseImageHoverStateProps) => {
  const [isHovered, setIsHovered] = useState(false);
  const [showActionPanel, setShowActionPanel] = useState(false);
  const [showActionButtons, setShowActionButtons] = useState(false);
  const isMobile = useIsMobile();

  // Calculate if action menu should be shown based on device and hover state
  const shouldShowActionsMenu = (hasImageUrl: boolean) => {
    return ((isMobile && showActionPanel) || (!isMobile && (isHovered || showActionPanel))) && 
           hasImageUrl && 
           viewMode === 'normal';
  };

  return {
    isHovered,
    setIsHovered,
    showActionPanel,
    setShowActionPanel,
    showActionButtons,
    setShowActionButtons,
    isMobile,
    shouldShowActionsMenu
  };
};
