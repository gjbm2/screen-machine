
import React from 'react';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Collapsible, CollapsibleContent } from '@/components/ui/collapsible';
import SortableImageContainer from './SortableImageContainer';
import ImageBatchItem from './ImageBatchItem';
import NavigationControls from './NavigationControls';
import ImageDetailView from './ImageDetailView';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Maximize, X } from 'lucide-react';
import ImageActions from '@/components/ImageActions';

type ViewMode = 'normal' | 'small' | 'table';

interface ImageBatchProps {
  batchId: string;
  images: Array<{
    url: string;
    prompt?: string;
    workflow: string;
    timestamp: number;
    params?: Record<string, any>;
    batchId?: string;
    batchIndex?: number;
    status?: 'generating' | 'completed' | 'error';
    referenceImageUrl?: string;
  }>;
  isExpanded: boolean;
  activeIndex: number;
  activeBatchId: string | null;
  onSetActiveBatchId: (id: string | null) => void;
  onSetActiveImageIndex: (batchId: string, index: number) => void;
  onToggleExpandBatch: (id: string) => void;
  onNavigatePrevImage: (batchId: string, imagesCount: number) => void;
  onNavigateNextImage: (batchId: string, imagesCount: number) => void;
  onDeleteImage: (batchId: string, index: number) => void;
  onCreateAgain: (batchId: string) => void;
  onUseAsInput?: ((imageUrl: string) => void) | null;
  extraComponents?: React.ReactNode;
  viewMode?: ViewMode;
  onOpenBatchDialog?: (batchId: string) => void;
}

const ImageBatch: React.FC<ImageBatchProps> = ({
  batchId,
  images,
  isExpanded,
  activeIndex,
  activeBatchId,
  onSetActiveBatchId,
  onSetActiveImageIndex,
  onToggleExpandBatch,
  onNavigatePrevImage,
  onNavigateNextImage,
  onDeleteImage,
  onCreateAgain,
  onUseAsInput,
  extraComponents,
  viewMode = 'normal',
  onOpenBatchDialog
}) => {
  if (!images || images.length === 0) return null;
  
  const activeImage = images[activeIndex];
  const isActive = activeBatchId === batchId;
  const [openFullScreen, setOpenFullScreen] = React.useState(false);
  
  const isSmallView = viewMode === 'small';
  
  const handleOpenFullScreen = () => {
    setOpenFullScreen(true);
  };
  
  const handleCreateAgain = () => {
    onCreateAgain(batchId);
  };
  
  const handleUseAsInput = () => {
    if (onUseAsInput && activeImage.url) {
      onUseAsInput(activeImage.url);
    }
  };
  
  const handleSwipe = React.useCallback((direction: 'left' | 'right') => {
    if (direction === 'left') {
      onNavigateNextImage(batchId, images.length);
    } else {
      onNavigatePrevImage(batchId, images.length);
    }
  }, [batchId, images.length, onNavigateNextImage, onNavigatePrevImage]);
  
  const promptTitle = activeImage?.prompt || '';
  
  return (
    <Collapsible 
      key={batchId} 
      open={isExpanded}
      className={`overflow-hidden rounded-lg bg-card border ${
        isExpanded ? 'col-span-1 sm:col-span-2 md:col-span-3 lg:col-span-4 xl:col-span-5' : 'col-span-1'
      }`}
      id={`batch-${batchId}`}
    >
      <SortableImageContainer 
        batchId={batchId} 
        batch={{ images }}
        isExpanded={isExpanded}
        toggleExpand={onToggleExpandBatch}
        viewMode={viewMode}
      >
        {!isExpanded && (
          <Card 
            className="overflow-hidden relative border-0 rounded-none"
            onMouseEnter={() => onSetActiveBatchId(batchId)}
            onMouseLeave={() => onSetActiveBatchId(null)}
          >
            {/* Collapsed view (carousel-like navigation) */}
            <ImageBatchItem 
              image={activeImage} 
              batchId={batchId} 
              index={activeIndex}
              total={images.length}
              onCreateAgain={onCreateAgain}
              onUseAsInput={onUseAsInput}
              onDeleteImage={onDeleteImage}
              onFullScreen={handleOpenFullScreen}
              viewMode={viewMode}
            />
            
            {/* Navigation controls */}
            {images.length > 1 && !isSmallView && (
              <NavigationControls 
                onPrevious={(e) => {
                  e.stopPropagation();
                  onNavigatePrevImage(batchId, images.length);
                }}
                onNext={(e) => {
                  e.stopPropagation();
                  onNavigateNextImage(batchId, images.length);
                }}
              />
            )}
          </Card>
        )}
        {isExpanded && (
          <Card 
            className="overflow-hidden relative border-0 rounded-none"
            onMouseEnter={() => onSetActiveBatchId(batchId)}
            onMouseLeave={() => onSetActiveBatchId(null)}
          >
            {/* Empty content for expanded view */}
          </Card>
        )}
      </SortableImageContainer>
      
      {/* Expanded content */}
      <CollapsibleContent>
        <ImageDetailView 
          batchId={batchId}
          images={images}
          activeIndex={activeIndex}
          onSetActiveIndex={(index) => onSetActiveImageIndex(batchId, index)}
          onNavigatePrev={(e) => {
            e.stopPropagation();
            onNavigatePrevImage(batchId, images.length);
          }}
          onNavigateNext={(e) => {
            e.stopPropagation();
            onNavigateNextImage(batchId, images.length);
          }}
          onToggleExpand={onToggleExpandBatch}
          onDeleteImage={onDeleteImage}
          onCreateAgain={onCreateAgain}
          onUseAsInput={onUseAsInput}
        />
      </CollapsibleContent>
      
      {/* Full screen dialog */}
      <Dialog open={openFullScreen} onOpenChange={setOpenFullScreen}>
        <DialogContent className="max-w-screen-lg p-0 overflow-hidden">
          <DialogHeader className="p-4 pb-0">
            <DialogTitle>Image Details</DialogTitle>
          </DialogHeader>
          <div className="p-4 pt-0">
            <div className="flex flex-col">
              {/* Image with click-to-close */}
              <div 
                className="w-full overflow-hidden rounded-md cursor-pointer" 
                onClick={() => setOpenFullScreen(false)}
              >
                <img 
                  src={activeImage?.url} 
                  alt={activeImage?.prompt || "Generated image"}
                  className="w-full h-auto object-contain max-h-[70vh]"
                />
              </div>
              
              {/* Image info */}
              <div className="mt-4 text-sm text-muted-foreground">
                <p className="mb-2">{activeImage?.prompt || "No prompt information"}</p>
                {activeImage?.workflow && (
                  <p>Workflow: {activeImage.workflow}</p>
                )}
              </div>
              
              {/* Action buttons */}
              <div className="mt-4 flex justify-center space-x-2">
                <ImageActions
                  imageUrl={activeImage.url}
                  onCreateAgain={handleCreateAgain}
                  onUseAsInput={onUseAsInput ? handleUseAsInput : undefined}
                  generationInfo={{
                    prompt: activeImage.prompt || '',
                    workflow: activeImage.workflow || '',
                    params: activeImage.params
                  }}
                  isFullScreen={true}
                />
              </div>
              
              {/* Close button */}
              <div className="mt-4 flex justify-center">
                <Button 
                  onClick={() => setOpenFullScreen(false)}
                  variant="outline"
                >
                  Close
                </Button>
              </div>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </Collapsible>
  );
};

export default ImageBatch;
