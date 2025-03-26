
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
  setActiveImageIndex?: (index: number) => void;
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
  viewMode,
  setActiveImageIndex
}) => {
  // Implement proper navigation for rolled-up view
  const handleNavigatePrev = (e?: React.MouseEvent) => {
    // Explicit event stopping for navigation
    if (e) {
      e.stopPropagation();
      e.preventDefault();
    }
    
    if (completedImages.length <= 1) return;
    
    // Get the previous index, wrapping around to the end if needed
    const prevIndex = (activeImageIndex - 1 + completedImages.length) % completedImages.length;
    
    // If parent provided a setActiveImageIndex function, use it directly
    if (setActiveImageIndex) {
      console.log(`Navigating to previous image: ${prevIndex} (direct update)`);
      setActiveImageIndex(prevIndex);
      return;
    }
    
    // Fallback to original behavior for backward compatibility
    if (completedImages[prevIndex]?.url) {
      console.log(`Navigating to previous image: ${prevIndex} (via onImageClick)`);
      onImageClick(
        completedImages[prevIndex].url, 
        completedImages[prevIndex].prompt || ''
      );
    }
  };
  
  const handleNavigateNext = (e?: React.MouseEvent) => {
    // Explicit event stopping for navigation
    if (e) {
      e.stopPropagation();
      e.preventDefault();
    }
    
    if (completedImages.length <= 1) return;
    
    // Get the next index, wrapping around to the beginning if needed
    const nextIndex = (activeImageIndex + 1) % completedImages.length;
    
    // If parent provided a setActiveImageIndex function, use it directly
    if (setActiveImageIndex) {
      console.log(`Navigating to next image: ${nextIndex} (direct update)`);
      setActiveImageIndex(nextIndex);
      return;
    }
    
    // Fallback to original behavior for backward compatibility
    if (completedImages[nextIndex]?.url) {
      console.log(`Navigating to next image: ${nextIndex} (via onImageClick)`);
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

  // Ensure activeImageIndex is within bounds - this is the key fix for the "Cannot read properties of undefined" error
  const safeActiveIndex = completedImages.length > 0 
    ? Math.min(Math.max(0, activeImageIndex), completedImages.length - 1) 
    : 0;

  return (
    <Card className="rounded-t-none">
      <CardContent className="p-2">
        <div className="grid gap-1 grid-cols-1">
          {completedImages.length > 0 ? (
            <ImageBatchItem
              key={`${batchId}-${safeActiveIndex}`}
              image={completedImages[safeActiveIndex]}
              batchId={batchId}
              index={safeActiveIndex}
              total={completedImages.length}
              onCreateAgain={handleCreateAgain}
              onUseAsInput={(url) => completedImages[safeActiveIndex]?.prompt !== undefined 
                ? onImageClick(url, completedImages[safeActiveIndex].prompt || '') 
                : onImageClick(url, '')}
              onDeleteImage={onDeleteImage}
              onFullScreen={() => handleFullScreenClick(completedImages[safeActiveIndex])}
              onImageClick={(url) => completedImages[safeActiveIndex]?.prompt !== undefined 
                ? onImageClick(url, completedImages[safeActiveIndex].prompt || '') 
                : onImageClick(url, '')}
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
