
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
  expandedContainers?: Record<string, boolean>;
  setExpandedContainers?: React.Dispatch<React.SetStateAction<Record<string, boolean>>>;
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
  expandedContainers: externalExpandedContainers,
  setExpandedContainers: externalSetExpandedContainers,
  workflow,
  generationParams,
  onUseGeneratedAsInput,
  onCreateAgain,
  onReorderContainers,
  onDeleteImage,
  onDeleteContainer,
  fullscreenRefreshTrigger
}) => {
  // Debug logging for image URL
  React.useEffect(() => {
    console.log('[ImageDisplay] Component rendered with imageUrl:', imageUrl);
    if (imageUrl) {
      // Test if image loads
      const testImg = new Image();
      testImg.onload = () => console.log('[ImageDisplay] Test image verified to load:', imageUrl);
      testImg.onerror = (e) => console.error('[ImageDisplay] Test image FAILED to load:', imageUrl, e);
      testImg.src = imageUrl;
    }
  }, [imageUrl]);

  const {
    viewMode,
    setViewMode,
    expandedContainers: internalExpandedContainers,
    setExpandedContainers: internalSetExpandedContainers,
    allImagesFlat,
    showFullScreenView,
    setShowFullScreenView,
    fullScreenBatchId,
    fullScreenImageIndex,
    setFullScreenImageIndex,
    currentGlobalIndex,
    openFullScreenView,
    handleNavigateGlobal,
    handleNavigateWithBatchAwareness,
    handleToggleExpand: internalHandleToggleExpand,
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
  
  // Use external state if provided, otherwise fall back to internal state
  const expandedContainers = externalExpandedContainers || internalExpandedContainers;
  
  // Handle toggling container expansion
  const handleToggleExpand = (batchId: string) => {
    if (externalSetExpandedContainers) {
      externalSetExpandedContainers(prev => ({
        ...prev,
        [batchId]: !prev[batchId]
      }));
    } else {
      internalHandleToggleExpand(batchId);
    }
  };
  
  const handleFullScreenClick = (image: any) => {
    if (image && image.batchId) {
      openFullScreenView(image.batchId, image.batchIndex || 0);
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
              onCreateAgain={onCreateAgain}
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
            handleNavigateWithBatchAwareness={handleNavigateWithBatchAwareness}
            fullscreenRefreshTrigger={fullscreenRefreshTrigger}
          />
        </div>
      )}
    </div>
  );
};

export default ImageDisplay;
