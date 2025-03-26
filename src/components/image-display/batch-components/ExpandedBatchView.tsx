
import React, { useState } from 'react';
import { Card, CardContent } from '@/components/ui/card';
import ImageBatchItem from '../ImageBatchItem';
import LoadingPlaceholder from '../LoadingPlaceholder';
import GenerationFailedPlaceholder from '../GenerationFailedPlaceholder';
import ThumbnailGallery from '../ThumbnailGallery';
import NewVariantPlaceholder from '../NewVariantPlaceholder';

interface ExpandedBatchViewProps {
  batchId: string;
  completedImages: Array<any>;
  anyGenerating: boolean;
  failedImages: Array<any>;
  activeImageIndex: number;
  setActiveImageIndex: (index: number) => void;
  handleCreateAgain: () => void;
  handleFullScreenClick: (image: any) => void;
  handleRemoveFailedImage: () => void;
  handleRetry: () => void;
  onImageClick: (url: string, prompt: string) => void;
  onDeleteImage: (batchId: string, index: number) => void;
  toggleExpand: (batchId: string) => void;
}

const ExpandedBatchView: React.FC<ExpandedBatchViewProps> = ({
  batchId,
  completedImages,
  anyGenerating,
  failedImages,
  activeImageIndex,
  setActiveImageIndex,
  handleCreateAgain,
  handleFullScreenClick,
  handleRemoveFailedImage,
  handleRetry,
  onImageClick,
  onDeleteImage,
  toggleExpand
}) => {
  const handleThumbnailClick = (index: number) => {
    setActiveImageIndex(index);
  };

  const handleUseAsInput = (url: string) => {
    // Call onImageClick directly instead of going to fullscreen
    if (completedImages[activeImageIndex]) {
      onImageClick(url, completedImages[activeImageIndex]?.prompt || '');
    }
  };

  const handleFullScreen = () => {
    if (completedImages[activeImageIndex]) {
      handleFullScreenClick(completedImages[activeImageIndex]);
    }
  };

  return (
    <Card className="rounded-t-none">
      <CardContent className="p-2">
        <div className="grid gap-4 grid-cols-1">
          {/* Main content area - will display either completed image, loading state, or error */}
          <div className="w-full overflow-hidden rounded-md">
            {completedImages.length > 0 ? (
              <div className="flex flex-col gap-2">
                <div className="aspect-[4/3] relative rounded-md overflow-hidden bg-black">
                  <ImageBatchItem
                    key={`${batchId}-${activeImageIndex}`}
                    image={completedImages[activeImageIndex]}
                    batchId={batchId}
                    index={activeImageIndex}
                    total={completedImages.length}
                    onCreateAgain={() => handleCreateAgain()}
                    onUseAsInput={(url) => handleUseAsInput(url)}
                    onDeleteImage={onDeleteImage}
                    onFullScreen={handleFullScreen}
                    onImageClick={(url) => handleUseAsInput(url)}
                    onNavigatePrev={activeImageIndex > 0 ? () => setActiveImageIndex(activeImageIndex - 1) : undefined}
                    onNavigateNext={activeImageIndex < completedImages.length - 1 ? () => setActiveImageIndex(activeImageIndex + 1) : undefined}
                    viewMode="normal"
                  />
                </div>
                {/* Thumbnail gallery for completed images - now with max-width container */}
                <div className="flex justify-start overflow-x-auto max-w-full">
                  <ThumbnailGallery
                    images={completedImages}
                    batchId={batchId}
                    activeIndex={activeImageIndex}
                    onThumbnailClick={handleThumbnailClick}
                    onDeleteImage={onDeleteImage}
                    onCreateAgain={() => handleCreateAgain()}
                  />
                </div>
              </div>
            ) : anyGenerating ? (
              <LoadingPlaceholder 
                prompt={anyGenerating && failedImages.length === 0 ? 
                  completedImages[0]?.prompt || failedImages[0]?.prompt || null : null} 
              />
            ) : failedImages.length > 0 ? (
              <GenerationFailedPlaceholder 
                prompt={failedImages[0]?.prompt || null}
                onRetry={handleRetry}
                onRemove={handleRemoveFailedImage}
              />
            ) : (
              <NewVariantPlaceholder 
                batchId={batchId}
                onClick={handleRetry} 
              />
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
};

export default ExpandedBatchView;
