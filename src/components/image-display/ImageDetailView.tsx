
import React, { useState } from 'react';
import { TooltipProvider } from '@/components/ui/tooltip';
import MainImageView from './MainImageView';
import ImageMetadata from './ImageMetadata';
import ReferenceImageSection from './ReferenceImageSection';
import ReferenceImageDialog from './ReferenceImageDialog';
import DetailViewActionBar from './detail-view/DetailViewActionBar';
import ImagePrompt from './detail-view/ImagePrompt';
import ImageKeyboardNavigation from './detail-view/ImageKeyboardNavigation';
import DetailViewTouchHandler from './detail-view/DetailViewTouchHandler';

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
  onImageClick?: (e: React.MouseEvent) => void;
  allImages?: Array<{
    url: string;
    batchId: string;
    batchIndex: number;
    prompt?: string;
  }>;
  isNavigatingAllImages?: boolean;
  onNavigateGlobal?: (imageIndex: number) => void;
  currentGlobalIndex?: number;
  hidePrompt?: boolean;
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
  onImageClick,
  allImages,
  isNavigatingAllImages,
  onNavigateGlobal,
  currentGlobalIndex,
  hidePrompt = false
}) => {
  const activeImage = images[activeIndex];
  const [showReferenceImage, setShowReferenceImage] = useState(false);
  const referenceImageUrl = activeImage?.referenceImageUrl;
  
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
  
  // Define swipe handlers for touch navigation
  const handleSwipeLeft = () => {
    if (onNavigateGlobal && allImages && currentGlobalIndex !== undefined) {
      if (currentGlobalIndex < allImages.length - 1) {
        onNavigateGlobal(currentGlobalIndex + 1);
      }
    } else if (activeIndex < images.length - 1) {
      onNavigateNext({} as React.MouseEvent);
    }
  };

  const handleSwipeRight = () => {
    if (onNavigateGlobal && allImages && currentGlobalIndex !== undefined) {
      if (currentGlobalIndex > 0) {
        onNavigateGlobal(currentGlobalIndex - 1);
      }
    } else if (activeIndex > 0) {
      onNavigatePrev({} as React.MouseEvent);
    }
  };
  
  return (
    <DetailViewTouchHandler 
      onSwipeLeft={handleSwipeLeft}
      onSwipeRight={handleSwipeRight}
    >
      <div className="w-full h-full flex flex-col">
        <TooltipProvider>
          {/* Keyboard navigation */}
          <ImageKeyboardNavigation 
            activeIndex={activeIndex}
            imagesLength={images.length}
            onNavigatePrev={onNavigatePrev}
            onNavigateNext={onNavigateNext}
            allImages={allImages}
            currentGlobalIndex={currentGlobalIndex}
            onNavigateGlobal={onNavigateGlobal}
          />

          {/* Selected image view - maximize image display */}
          {activeImage && (
            <div className="flex-grow flex items-center justify-center overflow-hidden">
              <MainImageView
                imageUrl={activeImage.url}
                altText={activeImage.prompt || "Generated image"}
                onImageLoad={handleImageLoad}
                onOpenInNewTab={handleOpenInNewTab}
                allImages={allImages}
                isNavigatingAllImages={isNavigatingAllImages}
                onNavigateGlobal={onNavigateGlobal}
                currentGlobalIndex={currentGlobalIndex}
                handleTouchStart={() => {}}  // Touch handling moved to DetailViewTouchHandler
                handleTouchEnd={() => {}}    // Touch handling moved to DetailViewTouchHandler
                onImageClick={onImageClick}  // Pass the onImageClick prop
              />
            </div>
          )}
          
          {/* Bottom panel with metadata and controls */}
          <div className="flex-shrink-0 p-2 space-y-2 bg-background">
            {/* Image metadata */}
            <ImageMetadata
              dimensions={imageDimensions}
              timestamp={activeImage?.timestamp}
            />
            
            {/* Image Actions Bar - all buttons in a single row */}
            {activeImage?.url && (
              <DetailViewActionBar 
                imageUrl={activeImage.url}
                onCreateAgain={handleCreateAgain}
                onUseAsInput={onUseAsInput ? handleUseAsInput : undefined}
                onDeleteImage={handleDeleteImage}
                generationInfo={{
                  prompt: activeImage.prompt || '',
                  workflow: activeImage.workflow || '',
                  params: activeImage.params
                }}
              />
            )}
            
            {/* Prompt info - conditionally rendered based on hidePrompt */}
            {!hidePrompt && <ImagePrompt prompt={activeImage?.prompt} />}

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
          </div>
        </TooltipProvider>
      </div>
    </DetailViewTouchHandler>
  );
};

export default ImageDetailView;
