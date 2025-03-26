
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
    // Don't trigger image click when clicking on action buttons or navigation buttons
    if ((e.target as HTMLElement).closest('.image-action-button') ||
        (e.target as HTMLElement).closest('button')) {
      e.stopPropagation();
      return; 
    }
    
    // Always go to fullscreen when clicked in normal view (for both desktop and mobile)
    if (image.url && onFullScreen && viewMode === 'normal') {
      onFullScreen(batchId, index);
      return;
    }
    
    // For small view, just call the general onImageClick
    if (image.url) {
      onImageClick(image.url);
    }
  };

  // Show action buttons on hover for desktop, or on tap for mobile
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
