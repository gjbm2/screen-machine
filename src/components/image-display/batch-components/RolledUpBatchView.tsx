
import React from 'react';
import { Card, CardContent } from '@/components/ui/card';
import ImageBatchItem from '../ImageBatchItem';
import LoadingPlaceholder from '../LoadingPlaceholder';
import GenerationFailedPlaceholder from '../GenerationFailedPlaceholder';

interface RolledUpBatchViewProps {
  batchId: string;
  completedImages: Array<any>;
  anyGenerating: boolean;
  failedImages: Array<any>;
  activeImageIndex: number;
  handleCreateAgain: () => void;
  handleFullScreenClick: (image: any) => void;
  handleRemoveFailedImage: () => void;
  handleRetry: () => void;
  onImageClick: (url: string, prompt: string) => void;
  onDeleteImage: (batchId: string, index: number) => void;
  viewMode: 'normal' | 'small' | 'table';
}

const RolledUpBatchView: React.FC<RolledUpBatchViewProps> = ({
  batchId,
  completedImages,
  anyGenerating,
  failedImages,
  activeImageIndex,
  handleCreateAgain,
  handleFullScreenClick,
  handleRemoveFailedImage,
  handleRetry,
  onImageClick,
  onDeleteImage,
  viewMode
}) => {
  // Implement proper navigation for rolled-up view
  const handleNavigatePrev = (e: React.MouseEvent) => {
    // Ensure the click doesn't propagate to parent elements
    e.stopPropagation();
    
    if (completedImages.length <= 1) return;
    
    // Get the previous index, wrapping around to the end if needed
    const prevIndex = (activeImageIndex - 1 + completedImages.length) % completedImages.length;
    
    // We can't directly update activeImageIndex here since it's managed by the parent
    // But we can dispatch a click event to the image to simulate changing the active image
    if (completedImages[prevIndex]?.url) {
      onImageClick(
        completedImages[prevIndex].url, 
        completedImages[prevIndex].prompt || ''
      );
    }
  };
  
  const handleNavigateNext = (e: React.MouseEvent) => {
    // Ensure the click doesn't propagate to parent elements
    e.stopPropagation();
    
    if (completedImages.length <= 1) return;
    
    // Get the next index, wrapping around to the beginning if needed
    const nextIndex = (activeImageIndex + 1) % completedImages.length;
    
    // Similar to prev, dispatch a click to change the active image
    if (completedImages[nextIndex]?.url) {
      onImageClick(
        completedImages[nextIndex].url, 
        completedImages[nextIndex].prompt || ''
      );
    }
  };

  // Find the first generating image (if any) to use its prompt for the loading placeholder
  const generatingImages = anyGenerating ? 
    failedImages.filter(img => img.status === 'generating' || !img.url) : [];
  const firstGeneratingImage = generatingImages.length > 0 ? generatingImages[0] : null;

  return (
    <Card className="rounded-t-none">
      <CardContent className="p-2">
        <div className="grid gap-1 grid-cols-1">
          {completedImages.length > 0 ? (
            <ImageBatchItem
              key={`${batchId}-${activeImageIndex}`}
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
              viewMode={viewMode}
              showActions={true}
              isRolledUp={true}
            />
          ) : anyGenerating ? (
            <LoadingPlaceholder 
              prompt={firstGeneratingImage?.prompt || null} 
              hasReferenceImages={firstGeneratingImage?.referenceImageUrl ? true : false}
              workflowName={firstGeneratingImage?.workflow || null}
              isCompact={viewMode === 'small'}
            />
          ) : failedImages.length > 0 ? (
            <GenerationFailedPlaceholder 
              prompt={failedImages[0]?.prompt || null} 
              onRetry={handleRetry}
              onRemove={handleRemoveFailedImage}
            />
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
};

export default RolledUpBatchView;
