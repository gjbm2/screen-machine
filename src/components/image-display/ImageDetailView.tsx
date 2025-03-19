
import React from 'react';
import { Button } from '@/components/ui/button';
import { ChevronUp, Image, X } from 'lucide-react';
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
      {/* Reference image section - displayed without cropping */}
      {referenceImageUrl && (
        <div className="mb-4">
          <p className="text-sm text-muted-foreground mb-2">Reference image:</p>
          <div className="border rounded-md overflow-hidden flex justify-center h-auto max-h-40">
            <img 
              src={referenceImageUrl} 
              alt="Reference image"
              className="h-full w-auto object-contain cursor-pointer"
              onClick={() => setShowReferenceImage(true)}
            />
          </div>
        </div>
      )}
      
      {/* Selected image view */}
      <div className="aspect-square relative bg-secondary/10 rounded-md overflow-hidden max-w-lg mx-auto group">
        {activeImage && (
          <img 
            src={activeImage.url}
            alt={activeImage.prompt || "Generated image"}
            className="w-full h-full object-contain"
          />
        )}
        
        {/* Navigation controls in expanded view */}
        {images.length > 1 && (
          <NavigationControls 
            onPrevious={onNavigatePrev}
            onNext={onNavigateNext}
          />
        )}
      </div>
      
      {/* Image Actions Bar - always visible in fullscreen mode */}
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
            alwaysVisible={true}
          />
        </div>
      )}
      
      {/* Prompt info */}
      {activeImage?.prompt && (
        <div className="text-sm text-muted-foreground text-center max-w-lg mx-auto">
          <p>{activeImage.prompt}</p>
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

      {/* Reference image popup (full size view) */}
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
                  className="w-full h-auto object-contain"
                />
              </div>
            </div>
          </DialogContent>
        </Dialog>
      )}
    </div>
  );
};

export default ImageDetailView;
