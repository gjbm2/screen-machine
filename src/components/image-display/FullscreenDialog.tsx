
import React, { useState, useEffect, useCallback } from 'react';
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
  fullscreenRefreshTrigger?: number; // Refresh trigger
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
  const [prompt, setPrompt] = useState('');
  const [currentBatch, setCurrentBatch] = useState<any[] | null>(null);
  const [currentImage, setCurrentImage] = useState<any | null>(null);
  const [showReferenceImagesDialog, setShowReferenceImagesDialog] = useState(false);
  const [showInfoDialog, setShowInfoDialog] = useState(false);
  const [imageDimensions, setImageDimensions] = useState({ width: 0, height: 0 });
  const [lastBatchId, setLastBatchId] = useState<string | null>(null);
  const [pendingBatchGeneration, setPendingBatchGeneration] = useState<string | null>(null);
  
  // Update state based on props - now also listen to the refresh trigger
  useEffect(() => {
    if (fullScreenBatchId && batches[fullScreenBatchId]) {
      const batch = batches[fullScreenBatchId];
      setCurrentBatch(batch);
      setLastBatchId(fullScreenBatchId);
      
      const image = batch[fullScreenImageIndex];
      setCurrentImage(image);
      
      if (image?.prompt) {
        setPrompt(image.prompt);
      } else {
        setPrompt('');
      }
      
      // Debug log for reference images
      console.log('Current image in fullscreen:', image);
      if (image?.referenceImageUrl) {
        console.log('Reference image URL in fullscreen:', image.referenceImageUrl);
      } else {
        console.log('No reference image URL in fullscreen image');
      }
    } else {
      // If we have a pending generation and the refresh trigger was updated,
      // try to find and display the newly generated image
      if (pendingBatchGeneration && fullscreenRefreshTrigger > 0) {
        // First check if we can find the new batch
        const targetPrompt = prompt; // Store the prompt we're looking for
        
        // Look for matching batch with the same prompt (newest first)
        const batchesWithPrompt = Object.entries(batches)
          .filter(([_, images]) => 
            images.some(img => img.prompt === targetPrompt && img.status === 'completed')
          )
          .sort(([_, a], [__, b]) => {
            // Sort by timestamp (newest first)
            const timestampA = a[0]?.timestamp || 0;
            const timestampB = b[0]?.timestamp || 0;
            return timestampB - timestampA;
          });
        
        if (batchesWithPrompt.length > 0) {
          const [newBatchId, newBatch] = batchesWithPrompt[0];
          
          // Find the newest completed image in this batch
          const completedImages = newBatch.filter(img => img.status === 'completed')
            .sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0));
          
          if (completedImages.length > 0) {
            // Find the index of this image in its batch
            const newImageIndex = newBatch.findIndex(img => img === completedImages[0]);
            
            if (newImageIndex !== -1) {
              // Find this image in the flat array to get its global index
              const globalIndex = allImagesFlat.findIndex(
                img => img.batchId === newBatchId && img.batchIndex === completedImages[0].batchIndex
              );
              
              if (globalIndex !== -1) {
                // Update the view to show this new image
                handleNavigateGlobal(globalIndex);
                
                // Clear the pending generation flag
                setPendingBatchGeneration(null);
                
                console.log(`Fullscreen view updated to show newly generated image in batch ${newBatchId}`);
              }
            }
          }
        }
      }
    }
  }, [fullScreenBatchId, batches, fullScreenImageIndex, fullscreenRefreshTrigger, pendingBatchGeneration, prompt, allImagesFlat, handleNavigateGlobal]);

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
    // Log reference image information for debugging
    if (currentImage?.referenceImageUrl) {
      console.log("Reference image URLs for info dialog:", currentImage.referenceImageUrl);
    } else {
      console.log("No reference image URLs available for info dialog");
    }
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
    // Store the prompt before triggering generation
    const currentPrompt = currentImage?.prompt || '';
    console.log("Creating again from batch:", batchId);
    console.log("Batch image:", currentImage);
    
    // Mark that we're expecting a new generation with this prompt
    setPendingBatchGeneration(batchId);
    
    // Store the last batch ID to navigate to the new image later
    setLastBatchId(batchId);
    
    // Call the parent's onCreateAgain
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
              onClose={() => setShowFullScreenView(false)}
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
