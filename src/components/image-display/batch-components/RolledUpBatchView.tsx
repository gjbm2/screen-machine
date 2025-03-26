
import React from 'react';
import ImageBatchItem from '../ImageBatchItem';
import GenerationFailedPlaceholder from '../GenerationFailedPlaceholder';
import LoadingPlaceholder from '../LoadingPlaceholder';
import { Card } from '@/components/ui/card';
import { ViewMode } from '../ImageDisplay';

interface RolledUpBatchViewProps {
  batchId: string;
  completedImages: any[];
  anyGenerating: boolean;
  failedImages: any[];
  activeImageIndex: number;
  setActiveImageIndex: (index: number) => void;
  handleCreateAgain: () => void;
  handleFullScreenClick: (image: any) => void;
  handleRemoveFailedImage: () => void;
  handleRetry: () => void;
  onImageClick: (url: string, prompt: string) => void;
  onDeleteImage: (batchId: string, index: number) => void;
  viewMode: ViewMode;
}

const RolledUpBatchView: React.FC<RolledUpBatchViewProps> = ({
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
  viewMode
}) => {
  // Find generating images
  const generatingImages = failedImages.filter(img => img.status === 'generating');
  const errorImages = failedImages.filter(img => img.status === 'failed' || img.status === 'error');
  
  // Get the first completed image to display, if any
  const firstCompletedImage = completedImages.length > 0 ? completedImages[0] : null;
  
  // Function to navigate between images
  const handleNavigateNext = () => {
    if (activeImageIndex < completedImages.length - 1) {
      setActiveImageIndex(activeImageIndex + 1);
    }
  };
  
  const handleNavigatePrev = () => {
    if (activeImageIndex > 0) {
      setActiveImageIndex(activeImageIndex - 1);
    }
  };
  
  return (
    <div className="relative w-full h-full">
      {firstCompletedImage ? (
        <ImageBatchItem
          image={firstCompletedImage}
          batchId={batchId}
          index={activeImageIndex}
          total={completedImages.length}
          onCreateAgain={handleCreateAgain}
          onDeleteImage={onDeleteImage}
          onFullScreen={handleFullScreenClick}
          onImageClick={(url) => onImageClick(url, firstCompletedImage.prompt || '')}
          onNavigateNext={completedImages.length > 1 ? handleNavigateNext : undefined}
          onNavigatePrev={completedImages.length > 1 ? handleNavigatePrev : undefined}
          viewMode={viewMode}
          isRolledUp={true}
        />
      ) : errorImages.length > 0 ? (
        <Card className="h-full flex items-center justify-center">
          <GenerationFailedPlaceholder 
            errorMessage="Failed to generate image"
            onRemove={handleRemoveFailedImage}
            onRetry={handleRetry}
            isCompact={true}
          />
        </Card>
      ) : generatingImages.length > 0 ? (
        <Card className="h-full flex items-center justify-center">
          <LoadingPlaceholder 
            prompt={generatingImages[0]?.prompt || null}
            workflowName={generatingImages[0]?.workflow || undefined}
            hasReferenceImages={!!generatingImages[0]?.referenceImageUrl}
            isCompact={viewMode === 'small'}
          />
        </Card>
      ) : (
        <div className="h-full flex items-center justify-center bg-muted text-muted-foreground text-sm">
          No images available
        </div>
      )}
    </div>
  );
};

export default RolledUpBatchView;
