
import React from 'react';
import ImageDetailView from '../ImageDetailView';
import { useIsMobile } from '@/hooks/use-mobile';
import DetailViewTouchHandler from '../detail-view/DetailViewTouchHandler';

interface FullscreenContentProps {
  batchId: string;
  currentBatch: any[];
  fullScreenImageIndex: number;
  setFullScreenImageIndex: (index: number) => void;
  onNavigatePrev: (e: React.MouseEvent) => void;
  onNavigateNext: (e: React.MouseEvent) => void;
  onDeleteImage: (batchId: string, index: number) => void;
  onCreateAgain: (batchId: string) => void;
  onUseAsInput: (url: string) => void;
  allImagesFlat: any[];
  currentGlobalIndex: number | null;
  handleNavigateGlobal: (index: number) => void;
  onImageClick: (e: React.MouseEvent) => void;
  onClose: () => void;
}

const FullscreenContent: React.FC<FullscreenContentProps> = ({
  batchId,
  currentBatch,
  fullScreenImageIndex,
  setFullScreenImageIndex,
  onNavigatePrev,
  onNavigateNext,
  onDeleteImage,
  onCreateAgain,
  onUseAsInput,
  allImagesFlat,
  currentGlobalIndex,
  handleNavigateGlobal,
  onImageClick,
  onClose
}) => {
  const isMobile = useIsMobile();
  
  // Filter completed images
  const completedImages = currentBatch.filter(img => img.status === 'completed');
  
  // Log all completed images and their batchIndexes for debugging
  console.log('FullscreenContent - All completed images batchIndexes:', 
    completedImages.map(img => ({ 
      batchIndex: img.batchIndex, 
      type: typeof img.batchIndex
    }))
  );
  console.log('FullscreenContent - Looking for batchIndex:', fullScreenImageIndex, 'type:', typeof fullScreenImageIndex);
  
  // Find the array index for the image with the matching batchIndex
  // Ensure we're comparing numbers to numbers (sometimes batchIndex could be a string)
  const targetArrayIndex = completedImages.findIndex(img => 
    Number(img.batchIndex) === Number(fullScreenImageIndex)
  );
  
  // Use a valid index if we couldn't find a match
  const activeArrayIndex = targetArrayIndex !== -1 ? targetArrayIndex : 0;
  
  console.log('FullscreenContent: Passing', completedImages.length, 'completed images');
  console.log('FullscreenContent: Target batchIndex is', fullScreenImageIndex, 'using array index', activeArrayIndex);
  
  // Double-check what image we're actually selecting
  if (completedImages[activeArrayIndex]) {
    console.log('FullscreenContent: Selected image has batchIndex:', completedImages[activeArrayIndex].batchIndex);
  }
  
  // Handle swipe events for mobile navigation
  const handleSwipeLeft = () => {
    console.log('FullscreenContent: Swipe left detected, navigating to next image');
    onNavigateNext({} as React.MouseEvent);
  };

  const handleSwipeRight = () => {
    console.log('FullscreenContent: Swipe right detected, navigating to previous image');
    onNavigatePrev({} as React.MouseEvent);
  };
  
  // Create the content to be rendered
  const contentView = (
    <div className="flex-grow overflow-hidden flex flex-col min-h-0 min-w-0 w-auto">
      <ImageDetailView
        batchId={batchId}
        images={completedImages}
        activeIndex={activeArrayIndex} // THIS IS THE CRITICAL FIX: Use activeArrayIndex instead of 0
        onSetActiveIndex={(index) => {
          // When the user selects an image in the detail view,
          // we need to update the fullScreenImageIndex with the batchIndex of the selected image
          if (completedImages[index]) {
            setFullScreenImageIndex(completedImages[index].batchIndex);
          }
        }}
        onNavigatePrev={onNavigatePrev}
        onNavigateNext={onNavigateNext}
        onToggleExpand={() => {}}
        onDeleteImage={onDeleteImage}
        onCreateAgain={onCreateAgain}
        onUseAsInput={(url) => onUseAsInput(url)}
        allImages={allImagesFlat}
        isNavigatingAllImages={true}
        onNavigateGlobal={handleNavigateGlobal}
        currentGlobalIndex={currentGlobalIndex !== null ? currentGlobalIndex : undefined}
        onImageClick={onImageClick}
        hidePrompt={true} // Hide the prompt since we now show it in the header
        onClose={onClose} // Add handler to close
      />
    </div>
  );
  
  // On mobile, wrap the content with the touch handler for swipe navigation
  if (isMobile) {
    return (
      <DetailViewTouchHandler
        onSwipeLeft={handleSwipeLeft}
        onSwipeRight={handleSwipeRight}
      >
        {contentView}
      </DetailViewTouchHandler>
    );
  }
  
  // On desktop, just return the content without touch handling
  return contentView;
};

export default FullscreenContent;
