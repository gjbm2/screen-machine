
import React from 'react';
import FullscreenDialog from './FullscreenDialog';
import ViewModeContent from './ViewModeContent';
import ImageDisplayHeader from './ImageDisplayHeader';
import useImageDisplayState from './hooks/useImageDisplayState';

export type ViewMode = 'normal' | 'small' | 'table';
export type SortField = 'index' | 'prompt' | 'batchSize' | 'timestamp';
export type SortDirection = 'asc' | 'desc';

interface ImageDisplayProps {
  imageUrl: string | null;
  prompt: string | null;
  isLoading: boolean;
  uploadedImages: string[];
  generatedImages: any[];
  imageContainerOrder: string[];
  workflow: string | null;
  generationParams?: Record<string, any>;
  onUseGeneratedAsInput: (url: string) => void;
  onCreateAgain: (batchId?: string) => void;
  onReorderContainers: (sourceIndex: number, destinationIndex: number) => void;
  onDeleteImage: (batchId: string, index: number) => void;
  onDeleteContainer: (batchId: string) => void;
  fullscreenRefreshTrigger?: number;
}

const ImageDisplay: React.FC<ImageDisplayProps> = ({
  imageUrl,
  prompt,
  isLoading,
  uploadedImages,
  generatedImages,
  imageContainerOrder,
  workflow,
  generationParams,
  onUseGeneratedAsInput,
  onCreateAgain,
  onReorderContainers,
  onDeleteImage,
  onDeleteContainer,
  fullscreenRefreshTrigger
}) => {
  // Use the new custom hook for state management
  const {
    viewMode,
    setViewMode,
    expandedContainers,
    allImagesFlat,
    showFullScreenView,
    setShowFullScreenView,
    fullScreenBatchId,
    fullScreenImageIndex,
    setFullScreenImageIndex,
    currentGlobalIndex,
    handleNavigateGlobal,
    handleToggleExpand,
    batches,
    hasBatches,
    handleSmallImageClick,
    handleTableRowClick,
    getAllImages,
    sortField,
    sortDirection,
    handleSortClick,
    getSortedContainers
  } = useImageDisplayState(imageContainerOrder, generatedImages, isLoading);
  
  const handleCreateAgain = (batchId?: string) => {
    onCreateAgain(batchId);
    
    if (imageContainerOrder.length > 0) {
      setTimeout(() => {
        handleToggleExpand(imageContainerOrder[0]);
      }, 100);
    }
  };
  
  const handleFullScreenClick = (image: any) => {
    if (image && image.batchId) {
      openFullScreenView(image.batchId, image.batchIndex || 0);
    }
  };

  const openFullScreenView = (batchId: string, imageIndex: number = 0) => {
    const selectedImageBatch = batches[batchId]?.filter(img => img.status === 'completed');
    if (selectedImageBatch && imageIndex < selectedImageBatch.length) {
      const globalIndex = allImagesFlat.findIndex(
        img => img.batchId === batchId && img.batchIndex === (selectedImageBatch[imageIndex]?.batchIndex || 0)
      );
      
      if (globalIndex !== -1) {
        handleNavigateGlobal(globalIndex);
      }
    }
  };
  
  return (
    <div className="mt-4">
      {hasBatches && (
        <div className="mt-2">
          <ImageDisplayHeader
            viewMode={viewMode}
            onViewModeChange={setViewMode}
          />
          
          <div className="pr-2">
            <ViewModeContent
              viewMode={viewMode}
              imageContainerOrder={imageContainerOrder}
              batches={batches}
              expandedContainers={expandedContainers}
              handleToggleExpand={handleToggleExpand}
              onUseGeneratedAsInput={onUseGeneratedAsInput}
              onCreateAgain={handleCreateAgain}
              onDeleteImage={onDeleteImage}
              onDeleteContainer={onDeleteContainer}
              onFullScreenClick={handleFullScreenClick}
              imageUrl={imageUrl}
              getAllImages={getAllImages}
              handleSmallImageClick={handleSmallImageClick}
              sortField={sortField}
              sortDirection={sortDirection}
              handleSortClick={handleSortClick}
              getSortedContainers={getSortedContainers}
              handleTableRowClick={handleTableRowClick}
              isLoading={isLoading}
              onReorderContainers={onReorderContainers}
            />
          </div>
          
          <FullscreenDialog 
            showFullScreenView={showFullScreenView}
            setShowFullScreenView={setShowFullScreenView}
            fullScreenBatchId={fullScreenBatchId}
            batches={batches}
            fullScreenImageIndex={fullScreenImageIndex}
            setFullScreenImageIndex={setFullScreenImageIndex}
            onDeleteImage={onDeleteImage}
            onCreateAgain={onCreateAgain}
            onUseGeneratedAsInput={onUseGeneratedAsInput}
            allImagesFlat={allImagesFlat}
            currentGlobalIndex={currentGlobalIndex}
            handleNavigateGlobal={handleNavigateGlobal}
            fullscreenRefreshTrigger={fullscreenRefreshTrigger}
          />
        </div>
      )}
    </div>
  );
};

export default ImageDisplay;
