
import React from 'react';
import { TooltipProvider } from '@/components/ui/tooltip';
import DetailViewTouchHandler from './DetailViewTouchHandler';
import DetailViewContent from './DetailViewContent';

interface DetailViewContainerProps {
  batchId: string;
  images: Array<{
    url: string;
    prompt?: string;
    workflow: string;
    status?: 'generating' | 'completed' | 'error';
    params?: Record<string, any>;
    referenceImageUrl?: string;
    timestamp?: number;
    batchIndex?: number;
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
  onClose?: () => void; // Added for closing fullscreen view
}

const DetailViewContainer: React.FC<DetailViewContainerProps> = ({
  batchId,
  images,
  activeIndex,
  onSetActiveIndex,
  onNavigatePrev,
  onNavigateNext,
  onToggleExpand,
  onDeleteImage,
  onCreateAgain,
  onUseAsInput,
  onImageClick,
  allImages,
  isNavigatingAllImages,
  onNavigateGlobal,
  currentGlobalIndex,
  hidePrompt = false,
  onClose // New prop for closing
}) => {
  const handleSwipeLeft = () => {
    onNavigateNext({} as React.MouseEvent);
  };

  const handleSwipeRight = () => {
    onNavigatePrev({} as React.MouseEvent);
  };
  
  // For debugging
  React.useEffect(() => {
    console.log(`DetailViewContainer: Displaying image at index ${activeIndex} out of ${images.length} images`);
    if (images[activeIndex]) {
      console.log("Current image:", images[activeIndex].url);
      if (images[activeIndex].batchIndex !== undefined) {
        console.log("Batch index:", images[activeIndex].batchIndex);
      }
    }
  }, [activeIndex, images]);
  
  return (
    <DetailViewTouchHandler 
      onSwipeLeft={handleSwipeLeft}
      onSwipeRight={handleSwipeRight}
    >
      <div className="w-auto min-w-0 h-full flex flex-col overflow-hidden">
        <TooltipProvider>
          <DetailViewContent
            batchId={batchId}
            images={images}
            activeIndex={activeIndex}
            onSetActiveIndex={onSetActiveIndex}
            onNavigatePrev={onNavigatePrev}
            onNavigateNext={onNavigateNext}
            onToggleExpand={onToggleExpand}
            onDeleteImage={onDeleteImage}
            onCreateAgain={onCreateAgain}
            onUseAsInput={onUseAsInput}
            onImageClick={onImageClick}
            allImages={allImages}
            isNavigatingAllImages={isNavigatingAllImages}
            onNavigateGlobal={onNavigateGlobal}
            currentGlobalIndex={currentGlobalIndex}
            hidePrompt={hidePrompt}
            onClose={onClose} // Pass the onClose handler
          />
        </TooltipProvider>
      </div>
    </DetailViewTouchHandler>
  );
};

export default DetailViewContainer;
