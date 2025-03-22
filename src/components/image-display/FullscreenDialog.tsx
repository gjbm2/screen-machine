
import React, { useState, useEffect } from 'react';
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog';
import { ChevronDown, ChevronUp, X } from 'lucide-react';
import ImageDetailView from './ImageDetailView';

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
  // Always declare hooks at the top level
  const [isPromptExpanded, setIsPromptExpanded] = useState(false);
  const [isMultiline, setIsMultiline] = useState(false);
  const [prompt, setPrompt] = useState('');
  const [currentBatch, setCurrentBatch] = useState<any[] | null>(null);
  const [currentImage, setCurrentImage] = useState<any | null>(null);
  
  // Update state based on props
  useEffect(() => {
    if (fullScreenBatchId && batches[fullScreenBatchId]) {
      const batch = batches[fullScreenBatchId];
      setCurrentBatch(batch);
      
      const image = batch[fullScreenImageIndex];
      setCurrentImage(image);
      
      if (image?.prompt) {
        setPrompt(image.prompt);
        setIsMultiline(image.prompt.length > 100);
      } else {
        setPrompt('');
        setIsMultiline(false);
      }
    } else {
      setCurrentBatch(null);
      setCurrentImage(null);
      setPrompt('');
      setIsMultiline(false);
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

  const togglePromptExpand = (e: React.MouseEvent) => {
    e.stopPropagation();
    setIsPromptExpanded(!isPromptExpanded);
  };

  const handleClose = (e: React.MouseEvent) => {
    e.stopPropagation();
    setShowFullScreenView(false);
  };
  
  return (
    <Dialog 
      open={showFullScreenView} 
      onOpenChange={(open) => setShowFullScreenView(open)}
    >
      <DialogContent 
        className="max-w-[95vw] w-auto min-w-[50vw] max-h-[95vh] h-auto p-0 overflow-hidden flex flex-col select-none" 
        noPadding
        hideCloseButton
      >
        <DialogTitle className="sr-only">Image Detail View</DialogTitle>
        
        {/* Header with expandable prompt - fixed width with text truncation */}
        {prompt && (
          <div className="px-4 py-2 border-b min-h-[40px] flex-shrink-0 w-full">
            <div 
              className="overflow-hidden flex items-start justify-between w-full"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="flex items-start flex-grow overflow-hidden min-w-0">
                {isMultiline && (
                  <button 
                    onClick={togglePromptExpand}
                    className="inline-flex items-center justify-center p-1 mr-2 hover:bg-gray-100 rounded-md flex-shrink-0 mt-0.5"
                  >
                    {isPromptExpanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                  </button>
                )}
                <div className={`text-base text-muted-foreground overflow-hidden ${isPromptExpanded ? '' : 'max-h-6'} min-w-0`}>
                  <p className={isPromptExpanded ? 'whitespace-normal break-words' : 'truncate'}>
                    {prompt}
                  </p>
                </div>
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
        )}

        <div className="flex-grow overflow-hidden flex flex-col min-h-0 w-full relative">
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
      </DialogContent>
    </Dialog>
  );
};

export default FullscreenDialog;
