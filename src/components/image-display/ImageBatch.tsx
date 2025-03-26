
import React from 'react';
import SortableImageContainer from './SortableImageContainer';
import { ViewMode } from './ImageDisplay';
import ExpandedBatchView from './batch-components/ExpandedBatchView';
import RolledUpBatchView from './batch-components/RolledUpBatchView';
import TableBatchView from './batch-components/TableBatchView';
import ImageBatchActions from './batch-components/ImageBatchActions';
import { useImageBatch } from './hooks/useImageBatch';
import { ImageGenerationStatus } from '@/types/workflows';

interface Image {
  url: string;
  prompt: string;
  workflow: string;
  batchIndex: number;
  status: ImageGenerationStatus;
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
  const {
    showDeleteDialog,
    setShowDeleteDialog,
    activeImageIndex,
    setActiveImageIndex,
    isMobile,
    getFilteredImages,
    getSafeActiveIndex
  } = useImageBatch({ batchId, images, hasGeneratingImages });
  
  if (!images || images.length === 0) {
    return null;
  }
  
  const { anyGenerating, completedImages, failedImages, allNonCompletedImages } = getFilteredImages();
  
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
    // Extra validation to ensure we have a valid image before showing fullscreen
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

  // Ensure we always have a valid activeImageIndex
  const safeActiveIndex = getSafeActiveIndex(completedImages);

  const handleImageClick = (url: string, prompt?: string) => {
    if (!url) {
      console.error("Cannot handle image click: URL is empty or undefined");
      return;
    }
    
    // In mobile view, clicking a rolled up container should expand it
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
      
      <ImageBatchActions
        showDeleteDialog={showDeleteDialog}
        onDeleteDialogClose={handleDeleteDialogClose}
        onConfirmDelete={handleConfirmDelete}
      />
    </SortableImageContainer>
  );
};

export default ImageBatch;
