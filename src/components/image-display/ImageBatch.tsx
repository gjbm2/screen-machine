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
import { ViewMode } from './ImageDisplay'; // Import ViewMode type from ImageDisplay

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
  toggleExpand: (id: string) => void;
  onImageClick: (url: string, prompt: string) => void;
  onCreateAgain: (batchId: string) => void;
  onDeleteImage: (index: number) => void;
  onDeleteContainer: () => void;
  activeImageUrl: string | null;
  viewMode?: ViewMode;
}

const ImageBatch: React.FC<ImageBatchProps> = ({
  batchId,
  images,
  isExpanded,
  toggleExpand,
  onImageClick,
  onCreateAgain,
  onDeleteImage,
  onDeleteContainer,
  activeImageUrl,
  viewMode = 'normal'
}) => {
  if (!images || images.length === 0) return null;
  
  const [activeIndex, setActiveIndex] = React.useState(0);
  const [activeBatchId, setActiveBatchId] = React.useState<string | null>(null);
  const [openFullScreen, setOpenFullScreen] = React.useState(false);
  
  const isSmallView = viewMode === 'small';
  const activeImage = images[activeIndex];
  
  React.useEffect(() => {
    // Reset to the first image when the batch changes
    setActiveIndex(0);
  }, [batchId]);
  
  const handleNavigatePrevImage = () => {
    setActiveIndex(prev => (prev > 0 ? prev - 1 : images.length - 1));
  };
  
  const handleNavigateNextImage = () => {
    setActiveIndex(prev => (prev < images.length - 1 ? prev + 1 : 0));
  };
  
  const handleImageClick = (url: string, prompt: string) => {
    const newIndex = images.findIndex(img => img.url === url);
    if (newIndex >= 0) {
      setActiveIndex(newIndex);
    }
    onImageClick(url, prompt);
  };
  
  const handleOpenFullScreen = () => {
    setOpenFullScreen(true);
  };
  
  const handleCreateAgain = () => {
    onCreateAgain(batchId);
  };
  
  const handleUseAsInput = (url: string) => {
    // This will be forwarded from props if needed
  };
  
  const handleSetActiveImageIndex = (index: number) => {
    setActiveIndex(index);
    if (images[index]) {
      onImageClick(images[index].url, images[index].prompt || '');
    }
  };
  
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
        toggleExpand={toggleExpand}
        viewMode={viewMode}
      >
        {!isExpanded && (
          <Card 
            className="overflow-hidden relative border-0 rounded-none"
            onMouseEnter={() => setActiveBatchId(batchId)}
            onMouseLeave={() => setActiveBatchId(null)}
          >
            {/* Collapsed view (carousel-like navigation) */}
            <ImageBatchItem 
              image={activeImage} 
              batchId={batchId} 
              index={activeIndex}
              total={images.length}
              onCreateAgain={onCreateAgain}
              onUseAsInput={handleUseAsInput}
              onDeleteImage={onDeleteImage}
              onFullScreen={handleOpenFullScreen}
              viewMode={viewMode}
            />
            
            {/* Navigation controls */}
            {images.length > 1 && !isSmallView && (
              <NavigationControls 
                onPrevious={(e) => {
                  e.stopPropagation();
                  handleNavigatePrevImage();
                }}
                onNext={(e) => {
                  e.stopPropagation();
                  handleNavigateNextImage();
                }}
              />
            )}
          </Card>
        )}
        {isExpanded && (
          <Card 
            className="overflow-hidden relative border-0 rounded-none"
            onMouseEnter={() => setActiveBatchId(batchId)}
            onMouseLeave={() => setActiveBatchId(null)}
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
          onSetActiveIndex={handleSetActiveImageIndex}
          onNavigatePrev={(e) => {
            e.stopPropagation();
            handleNavigatePrevImage();
          }}
          onNavigateNext={(e) => {
            e.stopPropagation();
            handleNavigateNextImage();
          }}
          onToggleExpand={toggleExpand}
          onDeleteImage={onDeleteImage}
          onCreateAgain={onCreateAgain}
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
