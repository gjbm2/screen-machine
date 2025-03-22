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
  currentImage?: any;
  imageCount?: number;
  currentImageIndex?: number;
  onNavigate?: (direction: "prev" | "next") => void;
  isNavigating?: boolean;
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
  onClose,
  currentImage,
  imageCount,
  currentImageIndex,
  onNavigate,
  isNavigating
}) => {
  if (currentImage) {
    return (
      <div className="flex-grow overflow-hidden flex flex-col min-h-0 min-w-0 w-auto">
        <ImageDetailView
          batchId={currentImage.batchId || batchId}
          images={[currentImage]}
          activeIndex={0}
          onSetActiveIndex={() => {}}
          onNavigatePrev={(e) => onNavigate?.('prev')}
          onNavigateNext={(e) => onNavigate?.('next')}
          onToggleExpand={() => {}}
          onDeleteImage={() => onDeleteImage?.(currentImage.batchId, currentImage.batchIndex || 0)}
          onCreateAgain={() => onCreateAgain?.(currentImage.batchId)}
          onUseAsInput={(url) => onUseAsInput?.(url)}
          allImages={allImagesFlat}
          isNavigatingAllImages={Boolean(isNavigating)}
          onNavigateGlobal={handleNavigateGlobal}
          currentGlobalIndex={currentGlobalIndex !== null ? currentGlobalIndex : undefined}
          onImageClick={onImageClick}
          hidePrompt={true}
          onClose={onClose}
        />
      </div>
    );
  }

  return (
    <div className="flex-grow overflow-hidden flex flex-col min-h-0 min-w-0 w-auto">
      <ImageDetailView
        batchId={batchId}
        images={currentBatch.filter(img => img.status === 'completed')}
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
        hidePrompt={true}
        onClose={onClose}
      />
    </div>
  );
};

export default FullscreenContent;
