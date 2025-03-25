
import React, { useState } from 'react';
import { useIsMobile } from '@/hooks/use-mobile';
import { ViewMode } from '../ImageDisplay';
import { useImageBatchItemActions } from './useImageBatchItemActions';
import { saveAs } from 'file-saver';

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
    title?: string;
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

  // Handle download action
  const handleDownload = (e: React.MouseEvent) => {
    if (!image.url) return;
    e.stopPropagation();
    
    // Use title if available, otherwise generate filename from timestamp
    const titleToUse = image.title || null;
    const filename = titleToUse 
      ? `${titleToUse.replace(/[^a-z0-9]/gi, '_').toLowerCase()}.png`
      : `image_${Date.now()}.png`;
    
    saveAs(image.url, filename);
  };

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
        // Toggle action buttons on mobile
        setShowActionButtons(!showActionButtons);
      }
    }

    // Only call onImageClick for unrolled view in mobile
    if (isMobile && !isRolledUp && viewMode === 'normal' && image.url) {
      onImageClick(image.url);
    }
  };

  // Show action buttons on hover for desktop
  const shouldShowActionButtons = isMobile 
    ? showActionButtons 
    : (isHovered && viewMode === 'normal');

  return {
    isHovered,
    setIsHovered,
    showActionButtons,
    setShowActionButtons,
    handleCreateAgain,
    handleUseAsInput,
    handleFullScreen,
    handleDeleteImage,
    handleDownload,
    handleImageClick,
    shouldShowActionButtons,
    isMobile
  };
};

export default useImageBatchItem;
