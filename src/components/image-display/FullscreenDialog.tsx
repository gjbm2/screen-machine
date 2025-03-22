
import React, { useState, useEffect } from 'react';
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog';
import { X } from 'lucide-react';
import ImageDetailView from './ImageDetailView';
import ImagePrompt from './detail-view/ImagePrompt';
import ReferenceImageDialog from './ReferenceImageDialog';

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
  handleNavigateGlobal
}) => {
  const [prompt, setPrompt] = useState('');
  const [currentBatch, setCurrentBatch] = useState<any[] | null>(null);
  const [currentImage, setCurrentImage] = useState<any | null>(null);
  const [showReferenceImagesDialog, setShowReferenceImagesDialog] = useState(false);
  
  // Update state based on props
  useEffect(() => {
    if (fullScreenBatchId && batches[fullScreenBatchId]) {
      const batch = batches[fullScreenBatchId];
      setCurrentBatch(batch);
      
      const image = batch[fullScreenImageIndex];
      setCurrentImage(image);
      
      if (image?.prompt) {
        setPrompt(image.prompt);
      } else {
        setPrompt('');
      }
    } else {
      setCurrentBatch(null);
      setCurrentImage(null);
      setPrompt('');
    }
  }, [fullScreenBatchId, batches, fullScreenImageIndex]);

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
    // This function should trigger the DetailViewInfoPanel to show info
    // For now we'll just toggle the reference images dialog as a placeholder
    if (currentImage?.referenceImageUrl) {
      setShowReferenceImagesDialog(true);
    }
  };

  const hasReferenceImages = Boolean(currentImage?.referenceImageUrl);
  
  const handleShowReferenceImages = () => {
    setShowReferenceImagesDialog(true);
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
              onDeleteImage={onDeleteImage}
              onCreateAgain={onCreateAgain}
              onUseAsInput={(url) => {
                onUseGeneratedAsInput(url);
                setShowFullScreenView(false);
              }}
              allImages={allImagesFlat}
              isNavigatingAllImages={true}
              onNavigateGlobal={handleNavigateGlobal}
              currentGlobalIndex={currentGlobalIndex !== null ? currentGlobalIndex : undefined}
              onImageClick={handleImageClick}
              hidePrompt={true} // Hide the prompt since we now show it in the header
            />
          )}
        </div>
        
        {/* Reference images dialog */}
        {currentImage?.referenceImageUrl && (
          <ReferenceImageDialog
            isOpen={showReferenceImagesDialog}
            onOpenChange={setShowReferenceImagesDialog}
            imageUrl={currentImage.referenceImageUrl}
          />
        )}
      </DialogContent>
    </Dialog>
  );
};

export default FullscreenDialog;
