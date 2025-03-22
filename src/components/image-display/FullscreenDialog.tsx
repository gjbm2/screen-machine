
import React, { useState, useEffect } from 'react';
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog';
import ReferenceImageDialog from './ReferenceImageDialog';
import ImageInfoDialog from './ImageInfoDialog';
import { useFullscreenDialog } from './fullscreen/useFullscreenDialog';
import FullscreenContent from './fullscreen/FullscreenContent';
import FullscreenHeader from './fullscreen/FullscreenHeader';
import { GeneratedImage } from '@/hooks/image-generation/types';

interface FullscreenDialogProps {
  showFullScreenView: boolean;
  setShowFullScreenView: (show: boolean) => void;
  fullScreenBatchId: string | null;
  batches: Record<string, GeneratedImage[]>;
  fullScreenImageIndex: number;
  setFullScreenImageIndex: (index: number) => void;
  onDeleteImage?: (batchId: string, index: number) => void;
  onCreateAgain?: (batchId: string) => void;
  onUseGeneratedAsInput?: (url: string) => void;
  allImagesFlat: GeneratedImage[];
  currentGlobalIndex: number;
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
  fullscreenRefreshTrigger
}) => {
  const {
    currentImage,
    images,
    prompt,
    isNavigating,
    showReferenceImages,
    showInfoDialog,
    handleNavigateImages,
    handleCloseFullScreen,
    handleReferenceImageClick,
    handleToggleReferenceImages,
    handleToggleInfoDialog,
  } = useFullscreenDialog({
    showFullScreenView,
    setShowFullScreenView,
    fullScreenBatchId,
    batches,
    fullScreenImageIndex,
    setFullScreenImageIndex,
    allImagesFlat,
    currentGlobalIndex,
    handleNavigateGlobal,
    fullscreenRefreshTrigger
  });

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
      batchIndex: currentImage.batchIndex
    } : 'No current image');

  const handleImageClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    // Toggle visibility of the header and footer
  };

  const handleDeleteImage = () => {
    if (currentImage && currentImage.batchId && onDeleteImage) {
      onDeleteImage(currentImage.batchId, currentImage.batchIndex || 0);
      
      // Navigate to the next image if there are any left
      if (images && images.length > 1) {
        const nextIndex = (fullScreenImageIndex + 1) % images.length;
        setFullScreenImageIndex(nextIndex);
      } else {
        // Close the fullscreen view if there are no images left
        setShowFullScreenView(false);
      }
    }
  };

  const handleCreateAgain = () => {
    if (currentImage && currentImage.batchId && onCreateAgain) {
      onCreateAgain(currentImage.batchId);
      setShowFullScreenView(false);
    }
  };

  const handleUseAsInput = () => {
    if (currentImage && onUseGeneratedAsInput) {
      onUseGeneratedAsInput(currentImage.url);
      setShowFullScreenView(false);
    }
  };
  
  // Construct number for display
  const imageNumber = currentImage && currentImage.containerId !== undefined 
    ? currentImage.containerId 
    : 0;
    
  // Use title if available, or construct from components
  const title = currentImage?.title;

  return (
    <Dialog open={showFullScreenView} onOpenChange={setShowFullScreenView}>
      <DialogContent 
        className="w-full max-w-6xl h-[90vh] p-0 flex flex-col"
        onInteractOutside={(e) => e.preventDefault()}
      >
        <FullscreenHeader 
          prompt={prompt || ''}
          hasReferenceImages={Boolean(currentImage?.referenceImageUrl)}
          onReferenceImageClick={handleToggleReferenceImages}
          workflowName={currentImage?.workflow}
          onInfoClick={handleToggleInfoDialog}
          onClose={handleCloseFullScreen}
          imageNumber={imageNumber}
          title={title}
        />
        
        <FullscreenContent 
          currentImage={currentImage}
          imageCount={images?.length || 0}
          currentImageIndex={fullScreenImageIndex}
          onNavigate={handleNavigateImages}
          onImageClick={handleImageClick}
          onDeleteImage={onDeleteImage ? handleDeleteImage : undefined}
          onCreateAgain={onCreateAgain ? handleCreateAgain : undefined}
          onUseAsInput={onUseGeneratedAsInput ? handleUseAsInput : undefined}
          isNavigating={isNavigating}
        />
        
        {/* Show reference images */}
        {currentImage?.referenceImageUrl && (
          <ReferenceImageDialog 
            isOpen={showReferenceImages} 
            onClose={handleToggleReferenceImages}
            referenceImageUrl={currentImage.referenceImageUrl}
          />
        )}
        
        {/* Image info dialog */}
        {currentImage && (
          <ImageInfoDialog
            isOpen={showInfoDialog}
            onClose={handleToggleInfoDialog}
            image={currentImage}
          />
        )}
      </DialogContent>
    </Dialog>
  );
};

export default FullscreenDialog;
