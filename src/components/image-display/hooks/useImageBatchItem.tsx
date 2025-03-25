
import React, { useState } from 'react';
import { useIsMobile } from '@/hooks/use-mobile';
import { ViewMode } from '../ImageDisplay';
import { useImageBatchItemActions } from './useImageBatchItemActions';

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

  // Import action handlers from separate hook
  const { 
    handleCreateAgain,
    handleUseAsInput,
    handleFullScreen,
    handleDeleteImage,
    handleDeleteFromPanel
  } = useImageBatchItemActions({
    batchId,
    batchIndex: index,
    onOpenFullscreenView: onFullScreen || ((batchId: string, index: number) => {}),
    onUseGeneratedAsInput: onUseAsInput || ((url: string) => {}),
    onDeleteImage: onDeleteImage || ((batchId: string, index: number) => {}),
    onCreateAgain: onCreateAgain || ((batchId: string) => {}),
    imageUrl: image.url
  });

  // Updated to accept a React.MouseEvent parameter
  const handleImageClick = (e: React.MouseEvent) => {
    if ((e.target as HTMLElement).closest('.image-action-button') ||
        (e.target as HTMLElement).closest('button')) {
      e.stopPropagation();
      return;
    }
    
    // Always go to fullscreen when clicked, regardless of view mode
    if (image.url && onFullScreen) {
      onFullScreen(batchId, index);
      return;
    }
    
    if (viewMode === 'normal') {
      if (isMobile) {
        // Toggle action panel and buttons on mobile
        setShowActionPanel(!showActionPanel);
        setShowActionButtons(!showActionButtons);
      }
    }

    // Only call onImageClick for unrolled view in mobile
    if (isMobile && !isRolledUp && viewMode === 'normal' && image.url) {
      onImageClick(image.url);
    }
  };

  // Calculate if action menu should be shown
  // Update this logic to ensure buttons always show on hover in desktop mode
  const shouldShowActionsMenu = ((isMobile && showActionPanel) || 
                               (!isMobile && (isHovered || showActionPanel))) && 
                               image.url && 
                               viewMode === 'normal';

  return {
    isHovered,
    setIsHovered,
    showActionPanel,
    setShowActionPanel,
    showActionButtons,
    setShowActionButtons,
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
