
import React, { useRef, useState, useEffect } from 'react';
import { TooltipProvider } from '@/components/ui/tooltip';
import ImageActions from '@/components/ImageActions';
import MainImageView from './MainImageView';
import ImageMetadata from './ImageMetadata';
import ReferenceImageSection from './ReferenceImageSection';
import ReferenceImageDialog from './ReferenceImageDialog';

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
  const [showReferenceImage, setShowReferenceImage] = useState(false);
  const referenceImageUrl = activeImage?.referenceImageUrl;
  const touchRef = useRef<HTMLDivElement>(null);
  const [startX, setStartX] = useState<number | null>(null);
  
  // Handle keyboard navigation
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'ArrowLeft') {
        // Navigate to previous image
        if (onNavigateGlobal && allImages && currentGlobalIndex !== undefined) {
          if (currentGlobalIndex > 0) {
            onNavigateGlobal(currentGlobalIndex - 1);
          }
        } else if (activeIndex > 0) {
          onNavigatePrev(e as unknown as React.MouseEvent);
        }
      } else if (e.key === 'ArrowRight') {
        // Navigate to next image
        if (onNavigateGlobal && allImages && currentGlobalIndex !== undefined) {
          if (currentGlobalIndex < allImages.length - 1) {
            onNavigateGlobal(currentGlobalIndex + 1);
          }
        } else if (activeIndex < images.length - 1) {
          onNavigateNext(e as unknown as React.MouseEvent);
        }
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => {
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [activeIndex, images.length, onNavigateNext, onNavigatePrev, allImages, currentGlobalIndex, onNavigateGlobal]);
  
  const handleCreateAgain = () => {
    onCreateAgain(batchId);
  };
  
  const handleUseAsInput = () => {
    if (onUseAsInput && activeImage.url) {
      onUseAsInput(activeImage.url);
    }
  };
  
  const handleDeleteImage = () => {
    onDeleteImage(batchId, activeIndex);
  };
  
  // Get image dimensions when loaded
  const [imageDimensions, setImageDimensions] = useState({ width: 0, height: 0 });
  
  const handleImageLoad = (e: React.SyntheticEvent<HTMLImageElement>) => {
    const img = e.currentTarget;
    setImageDimensions({
      width: img.naturalWidth,
      height: img.naturalHeight
    });
  };

  // Open image in new tab - now only happens when clicking the external link button
  const handleOpenInNewTab = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (activeImage?.url) {
      window.open(activeImage.url, '_blank', 'noopener,noreferrer');
    }
  };
  
  // Touch event handlers for swipe navigation
  const handleTouchStart = (e: React.TouchEvent) => {
    setStartX(e.touches[0].clientX);
  };

  const handleTouchEnd = (e: React.TouchEvent) => {
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
    <div className="p-4 space-y-4 w-full" ref={touchRef}>
      <TooltipProvider>
        {/* Selected image view - maximize image display */}
        {activeImage && (
          <MainImageView
            imageUrl={activeImage.url}
            altText={activeImage.prompt || "Generated image"}
            onImageLoad={handleImageLoad}
            onOpenInNewTab={handleOpenInNewTab}
            allImages={allImages}
            isNavigatingAllImages={isNavigatingAllImages}
            onNavigateGlobal={onNavigateGlobal}
            currentGlobalIndex={currentGlobalIndex}
            handleTouchStart={handleTouchStart}
            handleTouchEnd={handleTouchEnd}
          />
        )}
        
        {/* Image metadata */}
        <ImageMetadata
          dimensions={imageDimensions}
          timestamp={activeImage?.timestamp}
        />
        
        {/* Image Actions Bar - always visible in fullscreen mode */}
        {activeImage?.url && (
          <div className="flex justify-center space-x-3 py-2">
            <ImageActions
              imageUrl={activeImage.url}
              onCreateAgain={handleCreateAgain}
              onUseAsInput={onUseAsInput ? handleUseAsInput : undefined}
              onDeleteImage={handleDeleteImage}
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
          <div className="text-sm text-muted-foreground text-center max-w-lg mx-auto">
            <p>{activeImage.prompt}</p>
          </div>
        )}

        {/* Reference image at the bottom */}
        {referenceImageUrl && (
          <ReferenceImageSection
            referenceImageUrl={referenceImageUrl}
            onReferenceImageClick={() => setShowReferenceImage(true)}
          />
        )}

        {/* Reference image popup (full size view) */}
        {referenceImageUrl && (
          <ReferenceImageDialog
            isOpen={showReferenceImage}
            onOpenChange={setShowReferenceImage}
            imageUrl={referenceImageUrl}
          />
        )}
      </TooltipProvider>
    </div>
  );
};

export default ImageDetailView;
