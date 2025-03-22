
import React, { useState } from 'react';
import { Dialog, DialogContent } from '@/components/ui/dialog';
import ImageDetailView from './ImageDetailView';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { ChevronDown, ChevronRight, X } from 'lucide-react';

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
  
  const currentBatch = batches[fullScreenBatchId];
  const currentImage = currentBatch?.filter(img => img.status === 'completed')[fullScreenImageIndex];
  const imagePrompt = currentImage?.prompt || '';
  
  // State for prompt expansion
  const [isPromptExpanded, setIsPromptExpanded] = useState(false);
  
  // Function to check if a string contains multiple lines or is very long
  const hasMultipleLines = (text: string) => {
    return text.includes('\n') || text.length > 120;
  };
  
  const showCollapsibleTrigger = hasMultipleLines(imagePrompt);
  
  return (
    <Dialog 
      open={showFullScreenView} 
      onOpenChange={(open) => setShowFullScreenView(open)}
    >
      <DialogContent 
        className="max-w-[100vw] w-[95vw] md:w-[90vw] max-h-[95vh] h-auto p-0 overflow-hidden flex flex-col select-none" 
        noPadding
        description="Detailed view of generated image"
      >
        {/* Completely rebuilt header section for proper alignment */}
        <div className="flex items-center border-b border-border/30 p-3 pb-2 flex-shrink-0">
          {/* Left: Expand trigger (only shown for multi-line prompts) */}
          <div className="flex items-center flex-grow">
            {showCollapsibleTrigger ? (
              <Collapsible 
                open={isPromptExpanded}
                onOpenChange={setIsPromptExpanded}
                className="w-full"
              >
                <div className="flex items-center">
                  <CollapsibleTrigger className="flex items-center justify-center mr-1.5">
                    {isPromptExpanded ? (
                      <ChevronDown className="h-4 w-4 text-foreground" />
                    ) : (
                      <ChevronRight className="h-4 w-4 text-foreground" />
                    )}
                  </CollapsibleTrigger>
                  
                  <div className="text-sm font-medium text-foreground truncate">
                    {imagePrompt.split('\n')[0] || "No prompt available"}
                  </div>
                </div>
                
                <CollapsibleContent className="mt-1">
                  <div className="text-sm font-medium text-foreground pl-5 pr-2 py-1">
                    {imagePrompt.split('\n').slice(1).join('\n')}
                  </div>
                </CollapsibleContent>
              </Collapsible>
            ) : (
              <div className="text-sm font-medium text-foreground pl-6">
                {imagePrompt || "No prompt available"}
              </div>
            )}
          </div>
          
          {/* Right: Close button */}
          <button 
            onClick={() => setShowFullScreenView(false)}
            className="ml-3 p-1 rounded-full hover:bg-muted transition-colors flex-shrink-0"
            aria-label="Close dialog"
            onMouseDown={(e) => e.preventDefault()} // Prevent text selection
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
              onImageClick={(e) => e.stopPropagation()}
              hidePrompt={true} // Hide the prompt in the detail view since we're showing it in the dialog header
            />
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
};

export default FullscreenDialog;
