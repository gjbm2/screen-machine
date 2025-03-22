
import React, { useState } from 'react';
import { Dialog, DialogContent } from '@/components/ui/dialog';
import ImageDetailView from './ImageDetailView';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { ChevronRight, X } from 'lucide-react';

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
  if (!fullScreenBatchId) return null;
  
  const handleImageClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    setShowFullScreenView(false);
  };
  
  const currentBatch = batches[fullScreenBatchId];
  const currentImage = currentBatch?.filter(img => img.status === 'completed')[fullScreenImageIndex];
  const imagePrompt = currentImage?.prompt || '';
  
  // Determine if the prompt is long and might need collapsing
  const isLongPrompt = imagePrompt.length > 80;
  const [isLongPromptExpanded, setIsLongPromptExpanded] = useState(false);
  
  // Function to check if a string contains multiple lines
  const hasMultipleLines = (text: string) => {
    return text.includes('\n') || text.length > 120;
  };
  
  // Only show the collapsible trigger if the prompt has multiple lines
  const showCollapsibleTrigger = hasMultipleLines(imagePrompt);
  
  return (
    <Dialog 
      open={showFullScreenView} 
      onOpenChange={(open) => setShowFullScreenView(open)}
    >
      <DialogContent 
        className="max-w-[100vw] w-[95vw] md:w-[90vw] max-h-[95vh] h-auto p-0 overflow-hidden flex flex-col" 
        noPadding
        description="Detailed view of generated image"
      >
        <div className="flex justify-between items-start p-3 pb-0 flex-shrink-0">
          {imagePrompt ? (
            <Collapsible 
              defaultOpen={!showCollapsibleTrigger} 
              className="text-left flex-grow"
              open={isLongPromptExpanded}
              onOpenChange={setIsLongPromptExpanded}
            >
              <div className="flex items-start">
                {showCollapsibleTrigger && (
                  <CollapsibleTrigger className="h-6 w-6 flex items-center justify-center mr-1">
                    <ChevronRight className="h-4 w-4 transform transition-transform duration-200 data-[state=open]:rotate-90" />
                  </CollapsibleTrigger>
                )}
                <div className="text-sm font-normal text-muted-foreground truncate">
                  {imagePrompt.substring(0, showCollapsibleTrigger ? 80 : undefined)}
                  {showCollapsibleTrigger && isLongPromptExpanded === false && '...'}
                </div>
              </div>
              <CollapsibleContent>
                <div className="text-sm font-normal text-foreground pl-7 pr-2 pt-1">
                  {imagePrompt}
                </div>
              </CollapsibleContent>
            </Collapsible>
          ) : (
            <div className="text-sm font-normal text-muted-foreground">No prompt available</div>
          )}
          
          <button 
            onClick={() => setShowFullScreenView(false)}
            className="p-2 rounded-full hover:bg-muted transition-colors ml-2"
            aria-label="Close dialog"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
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
              hidePrompt={true}
            />
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
};

export default FullscreenDialog;
