
import React, { useState, useEffect } from 'react';
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog';
import ReferenceImageDialog from './ReferenceImageDialog';
import ImageInfoDialog from './ImageInfoDialog';
import FullscreenHeader from './fullscreen/FullscreenHeader';
import FullscreenContent from './fullscreen/FullscreenContent';
import useFullscreenDialog from './fullscreen/useFullscreenDialog';

interface FullscreenDialogProps {
  showFullScreenView: boolean;
  setShowFullScreenView: (show: boolean) => void;
  fullScreenBatchId: string | null;
  batches: Record<string, any[]>;
  fullScreenImageIndex: number;
  setFullScreenImageIndex: (index: number) => void;
  onDeleteImage: (batchId: string, index: number) => void;
  onCreateAgain: (batchId: string) => void;
  onUseGeneratedAsInput: (url: string) => void;
  allImagesFlat: any[];
  currentGlobalIndex: number | null;
  handleNavigateGlobal: (index: number) => void;
  fullscreenRefreshTrigger?: number;
}

const FullscreenDialog: React.FC<FullscreenDialogProps> = ({
  showFullScreenView,
  setShowFullScreenView,
  fullScreenBatchId,
  batches,
  fullScreenImageIndex,
  setFullScreenImageIndex,
  onDeleteImage,
  onCreateAgain,
  onUseGeneratedAsInput,
  allImagesFlat,
  currentGlobalIndex,
  handleNavigateGlobal,
  fullscreenRefreshTrigger = 0
}) => {
  const [lastBatchId, setLastBatchId] = useState<string | null>(null);

  const {
    prompt,
    currentBatch,
    currentImage,
    showReferenceImagesDialog,
    setShowReferenceImagesDialog,
    showInfoDialog,
    setShowInfoDialog,
    imageDimensions,
    handleImageLoad,
    handleShowInfoPanel,
    handleShowReferenceImages,
    hasReferenceImages
  } = useFullscreenDialog({
    fullScreenBatchId,
    batches,
    fullScreenImageIndex,
    fullscreenRefreshTrigger,
    lastBatchId,
    setLastBatchId
  });

  // Only render dialog if we need to show it
  if (!showFullScreenView) {
    return null;
  }

  // Additional logging to track what prompt is being displayed
  console.log(`FullscreenDialog - Current prompt: "${prompt}", Current image:`, 
    currentImage ? {
      url: currentImage.url,
      prompt: currentImage.prompt,
      workflow: currentImage.workflow,
      batchId: currentImage.batchId,
      batchIndex: currentImage.batchIndex,
      title: currentImage.title // Add title to logged properties
    } : 'No current image');

  const handleImageClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    setShowFullScreenView(false);
  };

  const handleClose = (e: React.MouseEvent) => {
    e.stopPropagation();
    setShowFullScreenView(false);
  };

  const handleCreateAgain = (batchId: string) => {
    // Store the current batch ID to navigate to the new image later
    setLastBatchId(batchId);
    onCreateAgain(batchId);
  };

  const handleDeleteImage = (batchId: string, index: number) => {
    onDeleteImage(batchId, index);
    // Close the fullscreen view after deleting
    setShowFullScreenView(false);
  };

  const handleUseAsInput = (url: string) => {
    onUseGeneratedAsInput(url);
    // Close the fullscreen view after applying input
    setShowFullScreenView(false);
  };
  
  return (
    <Dialog 
      open={showFullScreenView} 
      onOpenChange={(open) => setShowFullScreenView(open)}
    >
      <DialogContent 
        className="max-w-[95vw] w-auto min-w-0 md:w-auto max-h-[95vh] h-auto p-0 overflow-hidden flex flex-col select-none" 
        noPadding
        hideCloseButton
        style={{ width: 'fit-content', minWidth: '50vw' }}
      >
        <DialogTitle className="sr-only">Image Detail View</DialogTitle>
        
        {/* Header component - Now passing title */}
        <FullscreenHeader
          prompt={prompt}
          hasReferenceImages={hasReferenceImages}
          onReferenceImageClick={handleShowReferenceImages}
          workflowName={currentImage?.workflow}
          onInfoClick={handleShowInfoPanel}
          onClose={handleClose}
          imageNumber={fullScreenImageIndex + 1}
          title={currentImage?.title} // Pass the title to the header
        />

        {/* Content component */}
        {currentBatch && (
          <FullscreenContent
            batchId={fullScreenBatchId as string}
            currentBatch={currentBatch}
            fullScreenImageIndex={fullScreenImageIndex}
            setFullScreenImageIndex={setFullScreenImageIndex}
            onNavigatePrev={(e) => {
              e.stopPropagation();
              if (currentGlobalIndex !== null && currentGlobalIndex > 0) {
                handleNavigateGlobal(currentGlobalIndex - 1);
              }
            }}
            onNavigateNext={(e) => {
              e.stopPropagation();
              if (currentGlobalIndex !== null && currentGlobalIndex < allImagesFlat.length - 1) {
                handleNavigateGlobal(currentGlobalIndex + 1);
              }
            }}
            onDeleteImage={handleDeleteImage}
            onCreateAgain={handleCreateAgain}
            onUseAsInput={handleUseAsInput}
            allImagesFlat={allImagesFlat}
            currentGlobalIndex={currentGlobalIndex}
            handleNavigateGlobal={handleNavigateGlobal}
            onImageClick={handleImageClick}
            onClose={() => setShowFullScreenView(false)}
          />
        )}
        
        {/* Reference images dialog */}
        {currentImage?.referenceImageUrl && (
          <ReferenceImageDialog
            isOpen={showReferenceImagesDialog}
            onOpenChange={setShowReferenceImagesDialog}
            imageUrls={typeof currentImage.referenceImageUrl === 'string' 
              ? [currentImage.referenceImageUrl] 
              : currentImage.referenceImageUrl}
          />
        )}

        {/* Image info dialog */}
        {currentImage && (
          <ImageInfoDialog
            open={showInfoDialog}
            onOpenChange={setShowInfoDialog}
            image={currentImage}
            onDownload={() => window.open(currentImage.url, '_blank')}
          />
        )}

        {/* Hidden image element to load the image and get dimensions */}
        {currentImage?.url && (
          <img 
            src={currentImage.url} 
            onLoad={handleImageLoad} 
            alt="Preload for dimensions" 
            className="hidden" 
          />
        )}
      </DialogContent>
    </Dialog>
  );
};

export default FullscreenDialog;
