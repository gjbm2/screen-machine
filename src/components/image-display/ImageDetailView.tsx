
import React, { TouchEvent, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Clock, Ruler, ExternalLink } from 'lucide-react';
import { TooltipProvider, Tooltip, TooltipTrigger, TooltipContent } from '@/components/ui/tooltip';
import NavigationControls from './NavigationControls';
import ImageActions from '@/components/ImageActions';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { formatDistanceToNow } from 'date-fns';

interface ImageDetailViewProps {
  batchId: string;
  images: Array<{
    url: string;
    prompt?: string;
    workflow: string;
    status?: 'generating' | 'completed' | 'error';
    params?: Record<string, any>;
    referenceImageUrl?: string;
    timestamp?: number;
  }>;
  activeIndex: number;
  onSetActiveIndex: (index: number) => void;
  onNavigatePrev: (e: React.MouseEvent) => void;
  onNavigateNext: (e: React.MouseEvent) => void;
  onToggleExpand: (batchId: string) => void;
  onDeleteImage: (batchId: string, index: number) => void;
  onCreateAgain: (batchId: string) => void;
  onUseAsInput?: ((imageUrl: string) => void) | null;
  allImages?: Array<{
    url: string;
    batchId: string;
    batchIndex: number;
    prompt?: string;
  }>;
  isNavigatingAllImages?: boolean;
  onNavigateGlobal?: (imageIndex: number) => void;
  currentGlobalIndex?: number;
}

const ImageDetailView: React.FC<ImageDetailViewProps> = ({
  batchId,
  images,
  activeIndex,
  onSetActiveIndex,
  onNavigatePrev,
  onNavigateNext,
  onDeleteImage,
  onCreateAgain,
  onUseAsInput,
  allImages,
  isNavigatingAllImages,
  onNavigateGlobal,
  currentGlobalIndex
}) => {
  const activeImage = images[activeIndex];
  const [showReferenceImage, setShowReferenceImage] = React.useState(false);
  const referenceImageUrl = activeImage?.referenceImageUrl;
  const touchRef = useRef<HTMLDivElement>(null);
  const [startX, setStartX] = useState<number | null>(null);
  
  const handleCreateAgain = () => {
    onCreateAgain(batchId);
  };
  
  const handleUseAsInput = () => {
    if (onUseAsInput && activeImage.url) {
      onUseAsInput(activeImage.url);
    }
  };
  
  const formatTimeAgo = (timestamp?: number) => {
    if (!timestamp) return "Unknown time";
    return formatDistanceToNow(new Date(timestamp), { addSuffix: true });
  };
  
  // Get image dimensions when loaded
  const [imageDimensions, setImageDimensions] = React.useState({ width: 0, height: 0 });
  
  const handleImageLoad = (e: React.SyntheticEvent<HTMLImageElement>) => {
    const img = e.currentTarget;
    setImageDimensions({
      width: img.naturalWidth,
      height: img.naturalHeight
    });
  };

  // Open image in new tab - now only happens when clicking the external link button
  const handleImageClick = () => {
    // No action on image click in fullscreen view
    return;
  };
  
  const handleOpenInNewTab = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (activeImage?.url) {
      window.open(activeImage.url, '_blank', 'noopener,noreferrer');
    }
  };
  
  // Touch event handlers for swipe navigation
  const handleTouchStart = (e: TouchEvent) => {
    setStartX(e.touches[0].clientX);
  };

  const handleTouchEnd = (e: TouchEvent) => {
    if (startX === null) return;
    
    const endX = e.changedTouches[0].clientX;
    const diff = startX - endX;
    
    // If swipe distance is sufficient (30px)
    if (Math.abs(diff) > 30) {
      // Always use global navigation for both swipe and arrow navigation in fullscreen
      if (onNavigateGlobal && allImages && allImages.length > 1) {
        // Global navigation across all images
        if (diff > 0 && (currentGlobalIndex as number) < allImages.length - 1) {
          // Swipe left, go to next image
          onNavigateGlobal(currentGlobalIndex as number + 1);
        } else if (diff < 0 && (currentGlobalIndex as number) > 0) {
          // Swipe right, go to previous image
          onNavigateGlobal(currentGlobalIndex as number - 1);
        }
      } else if (images.length > 1) {
        // Fallback to batch navigation if global navigation not available
        if (diff > 0 && activeIndex < images.length - 1) {
          // Swipe left, go to next image
          onNavigateNext(e as unknown as React.MouseEvent);
        } else if (diff < 0 && activeIndex > 0) {
          // Swipe right, go to previous image
          onNavigatePrev(e as unknown as React.MouseEvent);
        }
      }
    }
    
    setStartX(null);
  };
  
  return (
    <div className="p-4 space-y-4 w-full">
      {/* Selected image view - maximize image display */}
      <div 
        ref={touchRef}
        className="relative flex justify-center items-center min-h-[70vh] max-h-[80vh] bg-secondary/10 rounded-md overflow-hidden group w-full"
        onTouchStart={handleTouchStart}
        onTouchEnd={handleTouchEnd}
      >
        {activeImage && (
          <div className="relative flex justify-center items-center w-full h-full">
            <img 
              src={activeImage.url}
              alt={activeImage.prompt || "Generated image"}
              className="max-w-full max-h-full object-contain"
              onLoad={handleImageLoad}
              onClick={handleImageClick}
            />
            <Tooltip>
              <TooltipTrigger asChild>
                <Button 
                  variant="outline" 
                  size="icon" 
                  className="absolute top-2 right-2 bg-background/80 backdrop-blur-sm"
                  onClick={handleOpenInNewTab}
                >
                  <ExternalLink className="h-4 w-4" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Open image in new tab</TooltipContent>
            </Tooltip>
          </div>
        )}
        
        {/* Navigation controls - Always use global navigation in fullscreen */}
        {allImages && allImages.length > 1 && onNavigateGlobal && (
          <NavigationControls 
            onPrevious={(e) => {
              e.stopPropagation();
              if ((currentGlobalIndex as number) > 0) {
                onNavigateGlobal((currentGlobalIndex as number) - 1);
              }
            }}
            onNext={(e) => {
              e.stopPropagation();
              if ((currentGlobalIndex as number) < allImages.length - 1) {
                onNavigateGlobal((currentGlobalIndex as number) + 1);
              }
            }}
            size="large"
          />
        )}
      </div>
      
      {/* Image metadata */}
      <div className="flex justify-between items-center text-sm text-muted-foreground">
        <div className="flex items-center">
          <Ruler className="h-4 w-4 mr-1" />
          <span>{imageDimensions.width} × {imageDimensions.height} px</span>
        </div>
        <div className="flex items-center">
          <Clock className="h-4 w-4 mr-1" />
          <span>{formatTimeAgo(activeImage?.timestamp)}</span>
        </div>
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
            isFullScreen={true}
          />
        </div>
      )}
      
      {/* Prompt info */}
      {activeImage?.prompt && (
        <div className="text-sm text-muted-foreground text-center max-w-3xl mx-auto">
          <p>{activeImage.prompt}</p>
        </div>
      )}

      {/* Reference image at the bottom */}
      {referenceImageUrl && (
        <div className="mt-4 border-t pt-4">
          <p className="text-sm text-muted-foreground mb-2">Reference image:</p>
          <div className="flex justify-center">
            <div className="border rounded-md overflow-hidden w-24 h-24">
              <img 
                src={referenceImageUrl} 
                alt="Reference image"
                className="w-full h-full object-cover cursor-pointer"
                onClick={() => setShowReferenceImage(true)}
              />
            </div>
          </div>
        </div>
      )}

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
