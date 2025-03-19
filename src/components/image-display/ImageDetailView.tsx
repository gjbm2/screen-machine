
import React from 'react';
import { Button } from '@/components/ui/button';
import { ChevronUp, Image, Maximize, X } from 'lucide-react';
import { TooltipProvider } from '@/components/ui/tooltip';
import NavigationControls from './NavigationControls';
import ThumbnailGallery from './ThumbnailGallery';
import ImageBatchItem from './ImageBatchItem';
import ImageActions from '@/components/ImageActions';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';

interface ImageDetailViewProps {
  batchId: string;
  images: Array<{
    url: string;
    prompt?: string;
    workflow: string;
    status?: 'generating' | 'completed' | 'error';
    params?: Record<string, any>;
    referenceImageUrl?: string;
  }>;
  activeIndex: number;
  onSetActiveIndex: (index: number) => void;
  onNavigatePrev: (e: React.MouseEvent) => void;
  onNavigateNext: (e: React.MouseEvent) => void;
  onToggleExpand: (batchId: string) => void;
  onDeleteImage: (batchId: string, index: number) => void;
  onCreateAgain: (batchId: string) => void;
  onUseAsInput?: ((imageUrl: string) => void) | null;
}

const ImageDetailView: React.FC<ImageDetailViewProps> = ({
  batchId,
  images,
  activeIndex,
  onSetActiveIndex,
  onNavigatePrev,
  onNavigateNext,
  onToggleExpand,
  onDeleteImage,
  onCreateAgain,
  onUseAsInput
}) => {
  const activeImage = images[activeIndex];
  const [showReferenceImage, setShowReferenceImage] = React.useState(false);
  const [showFullScreen, setShowFullScreen] = React.useState(false);
  const referenceImageUrl = activeImage?.referenceImageUrl;
  
  const handleCreateAgain = () => {
    onCreateAgain(batchId);
  };
  
  const handleUseAsInput = () => {
    if (onUseAsInput && activeImage.url) {
      onUseAsInput(activeImage.url);
    }
  };
  
  return (
    <div className="p-4 space-y-4">
      {/* Reference image indicator */}
      {referenceImageUrl && (
        <div className="flex justify-start mb-2">
          <Button 
            variant="outline" 
            size="sm" 
            className="text-xs gap-1"
            onClick={() => setShowReferenceImage(true)}
          >
            <Image className="h-3.5 w-3.5" />
            Reference Image
          </Button>
        </div>
      )}
      
      {/* Selected image view */}
      <div className="aspect-square relative bg-secondary/10 rounded-md overflow-hidden max-w-lg mx-auto group">
        <div onClick={() => setShowFullScreen(true)} className="cursor-pointer">
          <ImageBatchItem 
            image={activeImage}
            batchId={batchId}
            index={activeIndex}
            total={images.length}
            onCreateAgain={onCreateAgain}
            onUseAsInput={onUseAsInput}
            onDeleteImage={onDeleteImage}
            onFullScreen={() => setShowFullScreen(true)}
          />
        </div>
        
        {/* Navigation controls in expanded view */}
        {images.length > 1 && (
          <NavigationControls 
            onPrevious={onNavigatePrev}
            onNext={onNavigateNext}
          />
        )}
      </div>
      
      {/* Image Actions Bar */}
      {activeImage?.url && (
        <div className="flex justify-center space-x-2 py-2">
          <ImageActions
            imageUrl={activeImage.url}
            onCreateAgain={handleCreateAgain}
            onUseAsInput={onUseAsInput ? handleUseAsInput : undefined}
            generationInfo={{
              prompt: activeImage.prompt || '',
              workflow: activeImage.workflow || '',
              params: activeImage.params
            }}
          />
        </div>
      )}
      
      {/* Thumbnail gallery */}
      <ThumbnailGallery 
        images={images}
        batchId={batchId}
        activeIndex={activeIndex}
        onThumbnailClick={onSetActiveIndex}
        onDeleteImage={onDeleteImage}
        onCreateAgain={onCreateAgain}
      />
      
      {/* Compact roll-up button attached to bottom of container */}
      <div className="flex justify-center">
        <Button 
          variant="ghost" 
          size="sm"
          className="rounded-t-none rounded-b-lg bg-card hover:bg-accent/20 text-xs h-7 px-3 -mt-1 border-t border-x shadow"
          onClick={() => onToggleExpand(batchId)}
        >
          <ChevronUp className="h-4 w-4 mr-1" />
          Roll Up
        </Button>
      </div>

      {/* Reference image popup */}
      {referenceImageUrl && (
        <Dialog open={showReferenceImage} onOpenChange={setShowReferenceImage}>
          <DialogContent className="max-w-lg">
            <DialogHeader>
              <DialogTitle>Reference Image</DialogTitle>
            </DialogHeader>
            <div className="flex flex-col items-center">
              <p className="text-sm mb-2 text-muted-foreground">Reference image used for generation</p>
              <div className="border rounded-md overflow-hidden">
                <img 
                  src={referenceImageUrl} 
                  alt="Reference image"
                  className="w-full h-auto"
                />
              </div>
            </div>
          </DialogContent>
        </Dialog>
      )}
      
      {/* Full screen dialog */}
      <Dialog open={showFullScreen} onOpenChange={setShowFullScreen}>
        <DialogContent className="max-w-screen-lg p-0 overflow-hidden">
          <DialogHeader className="p-4 pb-0">
            <DialogTitle>Image Detail</DialogTitle>
          </DialogHeader>
          <div className="p-4 pt-0">
            <div className="flex flex-col">
              {/* Image with click-to-close */}
              <div 
                className="w-full overflow-hidden rounded-md cursor-pointer" 
                onClick={() => setShowFullScreen(false)}
              >
                <img 
                  src={activeImage?.url} 
                  alt={activeImage?.prompt || "Generated image"}
                  className="w-full h-auto object-contain"
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
                  onClick={() => setShowFullScreen(false)}
                  variant="outline"
                >
                  Close
                </Button>
              </div>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
};

export default ImageDetailView;
