
import React from 'react';
import ImageDetailView from '../ImageDetailView';

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
  // Filter completed images for the detail view
  const completedImages = currentBatch.filter(img => img.status === 'completed');
  
  // Log what's being passed to the detail view
  React.useEffect(() => {
    console.log(`FullscreenContent: Passing ${completedImages.length} completed images with active index ${fullScreenImageIndex}`);
    if (completedImages[fullScreenImageIndex]) {
      console.log("Active image:", completedImages[fullScreenImageIndex]);
    }
  }, [completedImages, fullScreenImageIndex]);
  
  return (
    <div className="flex-grow overflow-hidden flex flex-col min-h-0 min-w-0 w-auto">
      <ImageDetailView
        batchId={batchId}
        images={completedImages}
        activeIndex={fullScreenImageIndex}
        onSetActiveIndex={setFullScreenImageIndex}
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
};

export default FullscreenContent;
