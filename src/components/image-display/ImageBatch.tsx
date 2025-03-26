import React, { useState, useEffect } from 'react';
import SortableImageContainer from './SortableImageContainer';
import { ViewMode } from './ImageDisplay';
import { ImageGenerationStatus } from '@/types/workflows';
import ExpandedBatchView from './batch-components/ExpandedBatchView';
import RolledUpBatchView from './batch-components/RolledUpBatchView';
import TableBatchView from './batch-components/TableBatchView';
import DeleteBatchDialog from './batch-components/DeleteBatchDialog';

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
  activeImageUrl,
  viewMode,
  onFullScreenClick
}) => {
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const [activeImageIndex, setActiveImageIndex] = useState(0);
  
  useEffect(() => {
    if (images.length > 0 && activeImageIndex >= images.length) {
      setActiveImageIndex(0);
    }
  }, [images, activeImageIndex]);
  
  if (!images || images.length === 0) {
    return null;
  }
  
  const anyGenerating = images.some(img => img.status === 'generating');
  const completedImages = images.filter(img => img.status === 'completed');
  const failedImages = images.filter(img => img.status === 'failed' || img.status === 'error');
  
  const allGeneratingWithoutUrl = images.every(img => img.status === 'generating' && !img.url);

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
    if (onFullScreenClick) {
      onFullScreenClick({
        ...completedImages[activeImageIndex],
        batchId,
        batchIndex: completedImages[activeImageIndex].batchIndex || activeImageIndex
      });
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
          failedImages={failedImages}
          activeImageIndex={activeImageIndex}
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
          failedImages={failedImages}
          activeImageIndex={activeImageIndex}
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
