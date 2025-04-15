import React, { useState, useEffect } from 'react';
import SortableImageContainer from './SortableImageContainer';
import { ViewMode } from './ImageDisplay';
import ExpandedBatchView from './batch-components/ExpandedBatchView';
import RolledUpBatchView from './batch-components/RolledUpBatchView';
import TableBatchView from './batch-components/TableBatchView';
import DeleteBatchDialog from './batch-components/DeleteBatchDialog';
import { useIsMobile } from '@/hooks/use-mobile';

interface Image {
  url: string;
  prompt: string;
  workflow: string;
  batchIndex: number;
  status: 'generating' | 'completed' | 'error' | 'failed' | 'to_update';
  referenceImageUrl?: string;
}

interface ImageBatchProps {
  batchId: string;
  images: Image[];
  isExpanded: boolean;
  toggleExpand: (id: string) => void;
  onImageClick: (url: string, prompt: string) => void;
  onCreateAgain: () => void;
  onDeleteImage: (batchId: string, index: number) => void;
  onDeleteContainer: () => void;
  activeImageUrl: string | null;
  viewMode: ViewMode;
  onFullScreenClick?: (image: any) => void;
  hasGeneratingImages?: boolean;
}

const ImageBatch: React.FC<ImageBatchProps> = ({
  batchId,
  images,
  isExpanded,
  toggleExpand,
  onImageClick,
  onCreateAgain,
  onDeleteImage,
  onDeleteContainer,
  onFullScreenClick,
  activeImageUrl,
  viewMode,
  hasGeneratingImages = false
}) => {
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const [activeImageIndex, setActiveImageIndex] = useState(0);
  const isMobile = useIsMobile();
  
  useEffect(() => {
    if (images.length > 0 && activeImageIndex >= images.length) {
      console.log(`ImageBatch: Resetting activeImageIndex from ${activeImageIndex} to 0 because images.length=${images.length}`);
      setActiveImageIndex(0);
    }
  }, [images, activeImageIndex]);
  
  useEffect(() => {
    if (isMobile && hasGeneratingImages) {
      console.log(`Auto-scrolling to batch ${batchId} with generating images on mobile`);
      
      setTimeout(() => {
        const element = document.getElementById(batchId);
        if (element) {
          element.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
      }, 100);
    }
  }, [batchId, hasGeneratingImages, isMobile]);
  
  if (!images || images.length === 0) {
    return null;
  }
  
  const anyGenerating = images.some(img => img.status === 'generating') || hasGeneratingImages;
  const completedImages = images.filter(img => img.status === 'completed');
  const failedImages = images.filter(img => img.status === 'failed' || img.status === 'error');
  const generatingImages = images.filter(img => img.status === 'generating');
  
  const allNonCompletedImages = [...failedImages, ...generatingImages];

  if (viewMode === 'small' && completedImages.length === 0 && !anyGenerating && failedImages.length === 0) {
    return null;
  }

  const handleCreateAgain = () => {
    onCreateAgain();
  };

  const handleRetry = () => {
    onCreateAgain();
  };

  const handleFullScreenClick = (image: any) => {
    if (onFullScreenClick && image && image.url) {
      console.log("Opening fullscreen view with image:", image.url);
      onFullScreenClick({
        ...image,
        batchId,
        batchIndex: image.batchIndex || activeImageIndex
      });
    } else {
      console.error("Cannot open fullscreen: Invalid image or missing URL");
    }
  };

  const handleRemoveFailedImage = () => {
    if (failedImages.length > 0) {
      const firstFailedImage = failedImages[0];
      onDeleteImage(batchId, firstFailedImage.batchIndex);
    }
  };

  const handleDeleteDialogClose = () => {
    setShowDeleteDialog(false);
  };

  const handleConfirmDelete = () => {
    onDeleteContainer();
    setShowDeleteDialog(false);
  };

  const safeActiveIndex = completedImages.length > 0 
    ? Math.min(Math.max(0, activeImageIndex), completedImages.length - 1) 
    : 0;

  const handleImageClick = (url: string, prompt?: string) => {
    if (!url) {
      console.error("Cannot handle image click: URL is empty or undefined");
      return;
    }
    
    if (isMobile && viewMode === 'normal' && !isExpanded) {
      console.log(`Mobile: Expanding container ${batchId} on click`);
      toggleExpand(batchId);
      return;
    }
    
    if (viewMode === 'normal') {
      onImageClick(url, prompt || '');
    } else {
      const image = images.find(img => img.url === url);
      if (image) {
        onFullScreenClick?.(image);
      } else {
        onImageClick(url, prompt || '');
      }
    }
  };

  return (
    <SortableImageContainer 
      batchId={batchId}
      batch={{ images }}
      isExpanded={isExpanded}
      toggleExpand={toggleExpand}
      viewMode={viewMode}
    >
      {viewMode === 'table' ? (
        <TableBatchView 
          batchId={batchId}
          completedImages={completedImages}
          onImageClick={onImageClick}
          onDeleteImage={onDeleteImage}
        />
      ) : isExpanded ? (
        <ExpandedBatchView 
          batchId={batchId}
          completedImages={completedImages}
          anyGenerating={anyGenerating}
          failedImages={allNonCompletedImages}
          activeImageIndex={safeActiveIndex}
          setActiveImageIndex={setActiveImageIndex}
          handleCreateAgain={handleCreateAgain}
          handleFullScreenClick={handleFullScreenClick}
          handleRemoveFailedImage={handleRemoveFailedImage}
          handleRetry={handleRetry}
          onImageClick={onImageClick}
          onDeleteImage={onDeleteImage}
          toggleExpand={toggleExpand}
        />
      ) : (
        <RolledUpBatchView 
          batchId={batchId}
          completedImages={completedImages}
          anyGenerating={anyGenerating}
          failedImages={allNonCompletedImages}
          activeImageIndex={safeActiveIndex}
          setActiveImageIndex={setActiveImageIndex}
          handleCreateAgain={handleCreateAgain}
          handleFullScreenClick={handleFullScreenClick}
          handleRemoveFailedImage={handleRemoveFailedImage}
          handleRetry={handleRetry}
          onImageClick={onImageClick}
          onDeleteImage={onDeleteImage}
          viewMode={viewMode}
        />
      )}
      
      <DeleteBatchDialog 
        isOpen={showDeleteDialog}
        onClose={handleDeleteDialogClose}
        onConfirm={handleConfirmDelete}
      />
    </SortableImageContainer>
  );
};

export default ImageBatch;
