
import React, { useState } from 'react';
import { Card, CardContent } from '@/components/ui/card';
import ImageBatchItem from '../ImageBatchItem';
import LoadingPlaceholder from '../LoadingPlaceholder';
import GenerationFailedPlaceholder from '../GenerationFailedPlaceholder';
import ThumbnailGallery from '../ThumbnailGallery';

interface ExpandedBatchViewProps {
  batchId: string;
  completedImages: Array<any>;
  anyGenerating: boolean;
  failedImages: Array<any>;
  activeImageIndex: number;
  setActiveImageIndex: React.Dispatch<React.SetStateAction<number>>;
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
  // Implement proper navigation for expanded view
  const handleNavigatePrev = () => {
    if (completedImages.length <= 1) return;
    const prevIndex = (activeImageIndex - 1 + completedImages.length) % completedImages.length;
    setActiveImageIndex(prevIndex);
  };
  
  const handleNavigateNext = () => {
    if (completedImages.length <= 1) return;
    const nextIndex = (activeImageIndex + 1) % completedImages.length;
    setActiveImageIndex(nextIndex);
  };

  // Find the first generating image (if any) to use its prompt for the loading placeholder
  const generatingImages = anyGenerating ? 
    failedImages.filter(img => img.status === 'generating' || !img.url) : [];
  const firstGeneratingImage = generatingImages.length > 0 ? generatingImages[0] : null;

  return (
    <Card className="rounded-t-none">
      <CardContent className="p-3 pt-3">
        <div className="grid gap-3 grid-cols-1">
          {completedImages.length > 0 && (
            <div className="grid grid-cols-1 gap-3">
              <ImageBatchItem
                key={`${batchId}-main-${activeImageIndex}`}
                image={completedImages[activeImageIndex]}
                batchId={batchId}
                index={activeImageIndex}
                total={completedImages.length}
                onCreateAgain={handleCreateAgain}
                onUseAsInput={(url) => onImageClick(url, completedImages[activeImageIndex]?.prompt || '')}
                onDeleteImage={onDeleteImage}
                onFullScreen={() => handleFullScreenClick(completedImages[activeImageIndex])}
                onImageClick={(url) => onImageClick(url, completedImages[activeImageIndex]?.prompt || '')}
                onNavigatePrev={completedImages.length > 1 ? handleNavigatePrev : undefined}
                onNavigateNext={completedImages.length > 1 ? handleNavigateNext : undefined}
                viewMode="normal"
                showActions={true}
                isExpandedMain={true}
              />
              
              {/* Thumbnails */}
              {completedImages.length > 1 && (
                <div className="grid grid-cols-4 sm:grid-cols-6 md:grid-cols-8 lg:grid-cols-10 xl:grid-cols-12 gap-2">
                  {completedImages.map((image, idx) => (
                    <div 
                      key={`${batchId}-thumb-${idx}`}
                      className={`cursor-pointer overflow-hidden rounded-md aspect-square border-2 transition-all ${
                        idx === activeImageIndex ? 'border-primary' : 'border-transparent'
                      }`}
                      onClick={() => setActiveImageIndex(idx)}
                    >
                      <img 
                        src={image.url} 
                        alt={`Thumbnail ${idx + 1}`}
                        className="w-full h-full object-cover"
                      />
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
          
          {anyGenerating && (
            <LoadingPlaceholder 
              prompt={firstGeneratingImage?.prompt || null} 
              hasReferenceImages={firstGeneratingImage?.referenceImageUrl ? true : false}
              workflowName={firstGeneratingImage?.workflow || null}
            />
          )}
          
          {!anyGenerating && failedImages.length > 0 && completedImages.length === 0 && (
            <GenerationFailedPlaceholder 
              prompt={failedImages[0]?.prompt || null} 
              onRetry={handleRetry}
              onRemove={handleRemoveFailedImage}
            />
          )}
        </div>
      </CardContent>
    </Card>
  );
};

export default ExpandedBatchView;
