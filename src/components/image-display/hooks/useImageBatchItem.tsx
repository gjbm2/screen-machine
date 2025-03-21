
import React, { useState } from 'react';
import { useIsMobile } from '@/hooks/use-mobile';
import { ViewMode } from '../ImageDisplay';

interface UseImageBatchItemProps {
  image: {
    url: string;
    prompt?: string;
    workflow?: string;
    timestamp?: number;
    params?: Record<string, any>;
    batchId?: string;
    batchIndex?: number;
    status?: string;
    refiner?: string;
    referenceImageUrl?: string;
  };
  batchId: string;
  index: number;
  onCreateAgain?: (batchId: string) => void;
  onUseAsInput?: ((imageUrl: string) => void) | null;
  onDeleteImage?: (batchId: string, index: number) => void;
  onFullScreen?: (batchId: string, index: number) => void;
  onImageClick: (url: string) => void;
  viewMode: ViewMode;
  isRolledUp: boolean;
}

export const useImageBatchItem = ({
  image,
  batchId,
  index,
  onCreateAgain,
  onUseAsInput,
  onDeleteImage,
  onFullScreen,
  onImageClick,
  viewMode,
  isRolledUp
}: UseImageBatchItemProps) => {
  const [isHovered, setIsHovered] = useState(false);
  const [showActionPanel, setShowActionPanel] = useState(false);
  const [showActionButtons, setShowActionButtons] = useState(false);
  const isMobile = useIsMobile();

  const handleCreateAgain = () => {
    if (onCreateAgain) {
      onCreateAgain(batchId);
    }
  };

  const handleUseAsInput = () => {
    if (onUseAsInput && image.url) {
      onUseAsInput(image.url);
    }
  };

  const handleFullScreen = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (onFullScreen) {
      onFullScreen(batchId, index);
    }
  };
  
  const handleDeleteImage = (e: React.MouseEvent) => {
    e.stopPropagation();
    e.preventDefault();
    if (onDeleteImage) {
      onDeleteImage(batchId, index);
    }
  };
  
  const handleDeleteFromPanel = () => {
    if (onDeleteImage) {
      onDeleteImage(batchId, index);
    }
  };

  const handleImageClick = (e: React.MouseEvent) => {
    if ((e.target as HTMLElement).closest('.image-action-button') ||
        (e.target as HTMLElement).closest('button')) {
      e.stopPropagation();
      return;
    }
    
    if (viewMode === 'normal') {
      // On mobile:
      // - When rolled up: toggleExpand (handled by parent)
      // - When unrolled: toggle action panel and buttons
      // On desktop:
      // - Always go to fullscreen view (both rolled up and unrolled)
      if (isMobile) {
        // Toggle both action panel and buttons on mobile
        setShowActionPanel(!showActionPanel);
        setShowActionButtons(!showActionButtons);
      } else {
        // On desktop, go to fullscreen
        if (image.url && onFullScreen) {
          onFullScreen(batchId, index);
        }
      }
    } else if (image.url && onFullScreen) {
      // For small and table view, always go to fullscreen
      onFullScreen(batchId, index);
    }

    // Only call onImageClick for unrolled view in mobile
    if (isMobile && !isRolledUp && viewMode === 'normal' && image.url) {
      onImageClick(image.url);
    }
  };

  // Calculate if action menu should be shown
  const shouldShowActionsMenu = ((isMobile && showActionPanel) || (!isMobile && (isHovered || showActionPanel))) && 
                                image.url && 
                                viewMode === 'normal';

  return {
    isHovered,
    setIsHovered,
    showActionPanel,
    showActionButtons,
    handleCreateAgain,
    handleUseAsInput,
    handleFullScreen,
    handleDeleteImage,
    handleDeleteFromPanel,
    handleImageClick,
    shouldShowActionsMenu,
    isMobile
  };
};

export default useImageBatchItem;
