
import React, { useState, useEffect } from 'react';
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog';
import { X } from 'lucide-react';
import ImageDetailView from './ImageDetailView';
import ImagePrompt from './detail-view/ImagePrompt';
import ReferenceImageDialog from './ReferenceImageDialog';
import ImageInfoDialog from './ImageInfoDialog';

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
  fullscreenRefreshTrigger?: number; // Add optional refresh trigger
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
  fullscreenRefreshTrigger = 0 // Default to 0
}) => {
  const [prompt, setPrompt] = useState('');
  const [currentBatch, setCurrentBatch] = useState<any[] | null>(null);
  const [currentImage, setCurrentImage] = useState<any | null>(null);
  const [showReferenceImagesDialog, setShowReferenceImagesDialog] = useState(false);
  const [showInfoDialog, setShowInfoDialog] = useState(false);
  const [imageDimensions, setImageDimensions] = useState({ width: 0, height: 0 });
  const [lastBatchId, setLastBatchId] = useState<string | null>(null);
  
  // Update state based on props - now also listen to the refresh trigger
  useEffect(() => {
    if (showFullScreenView && fullScreenBatchId && batches[fullScreenBatchId]) {
      const batch = batches[fullScreenBatchId];
      setCurrentBatch(batch);
      setLastBatchId(fullScreenBatchId);
      
      // Find completed images in the batch
      const completedImages = batch.filter(img => img.status === 'completed');
      
      // If we have completed images and a valid index, use it; otherwise use the first completed image
      if (completedImages.length > 0) {
        const validIndex = fullScreenImageIndex < completedImages.length 
          ? fullScreenImageIndex 
          : 0;
        
        const image = completedImages[validIndex];
        setCurrentImage(image);
        
        if (image?.prompt) {
          setPrompt(image.prompt);
        } else {
          setPrompt('');
        }
      }
    } else {
      setCurrentBatch(null);
      setCurrentImage(null);
      setPrompt('');
    }
  }, [fullScreenBatchId, batches, fullScreenImageIndex, fullscreenRefreshTrigger, showFullScreenView]);

  // Log refresh trigger changes for debugging - Moving this useEffect up to maintain consistent hook order
  useEffect(() => {
    console.log("FullscreenDialog - refresh trigger changed:", fullscreenRefreshTrigger);
    
    // If dialog is open and there's a recent generation, update to display it
    if (showFullScreenView && lastBatchId && batches[lastBatchId]) {
      const completedImages = batches[lastBatchId].filter(img => img.status === 'completed');
      if (completedImages.length > 0) {
        // If there are completed images in the batch, force update to that batch
        if (lastBatchId !== fullScreenBatchId || fullScreenImageIndex >= completedImages.length) {
          console.log("FullscreenDialog - updating to newly completed image at index:", 0);
          
          // Find the global index of the first completed image in this batch
          const globalIndex = allImagesFlat.findIndex(
            img => img.batchId === lastBatchId && img.batchIndex === 0
          );
          
          if (globalIndex !== -1) {
            handleNavigateGlobal(globalIndex);
          }
        }
      }
    }
  }, [fullscreenRefreshTrigger, showFullScreenView, lastBatchId, batches, fullScreenBatchId, fullScreenImageIndex, allImagesFlat, handleNavigateGlobal]);

  // Only render dialog if we need to show it
  if (!showFullScreenView) {
    return null;
  }

  const handleImageClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    setShowFullScreenView(false);
  };

  const handleClose = (e: React.MouseEvent) => {
    e.stopPropagation();
    setShowFullScreenView(false);
  };

  const handleShowInfoPanel = () => {
    console.log("Showing info dialog in fullscreen mode");
    setShowInfoDialog(true);
  };

  // Determine if there are reference images based on the currentImage
  const hasReferenceImages = Boolean(currentImage?.referenceImageUrl);
  
  const handleShowReferenceImages = () => {
    if (currentImage?.referenceImageUrl) {
      console.log("Opening reference image dialog with:", currentImage.referenceImageUrl);
    } else {
      console.log("Attempted to show reference images but none available");
    }
    setShowReferenceImagesDialog(true);
  };

  const handleImageLoad = (e: React.SyntheticEvent<HTMLImageElement>) => {
    const img = e.currentTarget as HTMLImageElement;
    setImageDimensions({
      width: img.naturalWidth,
      height: img.naturalHeight
    });
  };

  const handleCreateAgain = (batchId: string) => {
    // Store the current batch ID to navigate to the new image later
    setLastBatchId(batchId);
    onCreateAgain(batchId);
    
    // We don't close the fullscreen view - we'll wait for the new image to be generated
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
        
        {/* Header with prompt - fixed height */}
        <div className="px-4 py-2 border-b h-10 flex-shrink-0 flex items-center">
          <div className="flex items-center justify-between w-full min-w-0 overflow-hidden">
            <div className="flex-grow min-w-0 overflow-hidden">
              <ImagePrompt 
                prompt={prompt}
                hasReferenceImages={hasReferenceImages}
                onReferenceImageClick={handleShowReferenceImages}
                imageNumber={fullScreenImageIndex + 1}
                workflowName={currentImage?.workflow}
                onInfoClick={handleShowInfoPanel}
              />
            </div>
            
            {/* Close button */}
            <button 
              onClick={handleClose}
              className="inline-flex items-center justify-center p-1 hover:bg-gray-100 rounded-md flex-shrink-0 ml-2"
              aria-label="Close dialog"
            >
              <X size={16} />
            </button>
          </div>
        </div>

        <div className="flex-grow overflow-hidden flex flex-col min-h-0 min-w-0 w-auto">
          {currentBatch && (
            <ImageDetailView
              batchId={fullScreenBatchId as string}
              images={currentBatch.filter(img => img.status === 'completed')}
              activeIndex={fullScreenImageIndex}
              onSetActiveIndex={setFullScreenImageIndex}
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
              onToggleExpand={() => {}}
              onDeleteImage={handleDeleteImage}
              onCreateAgain={handleCreateAgain}
              onUseAsInput={(url) => {
                handleUseAsInput(url);
              }}
              allImages={allImagesFlat}
              isNavigatingAllImages={true}
              onNavigateGlobal={handleNavigateGlobal}
              currentGlobalIndex={currentGlobalIndex !== null ? currentGlobalIndex : undefined}
              onImageClick={handleImageClick}
              hidePrompt={true} // Hide the prompt since we now show it in the header
              onClose={() => setShowFullScreenView(false)} // Add handler to close
            />
          )}
        </div>
        
        {/* Reference images dialog - for all reference images */}
        {currentImage?.referenceImageUrl && (
          <ReferenceImageDialog
            isOpen={showReferenceImagesDialog}
            onOpenChange={setShowReferenceImagesDialog}
            imageUrl={currentImage.referenceImageUrl} // Pass the entire string to allow multiple images
          />
        )}

        {/* Image info dialog - includes all reference images */}
        {currentImage && (
          <ImageInfoDialog
            isOpen={showInfoDialog}
            onOpenChange={setShowInfoDialog}
            image={currentImage}
            dimensions={imageDimensions}
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
