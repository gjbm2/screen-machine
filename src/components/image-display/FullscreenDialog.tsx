
import React, { useState } from 'react';
import { Dialog, DialogContent } from '@/components/ui/dialog';
import { X, ChevronDown, ChevronUp } from 'lucide-react';
import { Button } from '@/components/ui/button';
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
  const [isPromptExpanded, setIsPromptExpanded] = useState(false);
  
  if (!fullScreenBatchId) return null;
  
  const currentBatch = batches[fullScreenBatchId];
  const currentImage = currentBatch?.[fullScreenImageIndex];
  const prompt = currentImage?.prompt || '';
  
  const handleImageClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    setShowFullScreenView(false);
  };

  const togglePromptExpand = (e: React.MouseEvent) => {
    e.stopPropagation();
    setIsPromptExpanded(!isPromptExpanded);
  };
  
  return (
    <Dialog 
      open={showFullScreenView} 
      onOpenChange={(open) => setShowFullScreenView(open)}
    >
      <DialogContent 
        className="max-w-[100vw] w-[95vw] md:w-[90vw] max-h-[95vh] h-auto p-0 overflow-hidden flex flex-col select-none" 
        noPadding
      >
        {/* Custom header with expandable prompt and close button */}
        {currentBatch && (
          <div className="flex justify-between items-start px-4 py-3 border-b">
            <div 
              className={`flex-1 pr-4 overflow-hidden`}
              onClick={(e) => e.stopPropagation()}
            >
              <div className="flex items-center">
                <button 
                  onClick={togglePromptExpand}
                  className="inline-flex items-center justify-center p-1 mr-2 hover:bg-gray-100 rounded-md flex-shrink-0"
                >
                  {isPromptExpanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                </button>
                <div className={`text-sm text-muted-foreground ${isPromptExpanded ? 'max-h-none' : 'max-h-6 overflow-hidden'}`}>
                  <p className={isPromptExpanded ? 'whitespace-normal' : 'truncate'}>
                    {prompt}
                  </p>
                </div>
              </div>
            </div>
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7 p-0 flex-shrink-0 -mt-0.5"
              onClick={() => setShowFullScreenView(false)}
            >
              <X className="h-4 w-4" />
            </Button>
          </div>
        )}

        <div className="flex-grow overflow-hidden flex flex-col">
          {batches[fullScreenBatchId] && (
            <ImageDetailView
              batchId={fullScreenBatchId}
              images={batches[fullScreenBatchId].filter(img => img.status === 'completed')}
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
