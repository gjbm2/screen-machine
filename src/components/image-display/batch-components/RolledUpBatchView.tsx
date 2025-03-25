
import React from 'react';
import { Card, CardContent } from '@/components/ui/card';
import ImageBatchItem from '../ImageBatchItem';
import LoadingPlaceholder from '../LoadingPlaceholder';
import GenerationFailedPlaceholder from '../GenerationFailedPlaceholder';
import { ViewMode } from '../ImageDisplay';

interface RolledUpBatchViewProps {
  batchId: string;
  images: Array<any>; // Added images prop to match what ImageBatch is sending
  onCreateAgain: () => void;
  onDeleteImage: (batchId: string, index: number) => void;
  onImageClick: (url: string, prompt: string) => void;
  onFullScreenClick: (image: any) => void;
  viewMode: ViewMode;
}

const RolledUpBatchView: React.FC<RolledUpBatchViewProps> = ({
  batchId,
  images,
  onCreateAgain,
  onDeleteImage,
  onImageClick,
  onFullScreenClick,
  viewMode
}) => {
  // Process images to get completed, generating, and failed ones
  const completedImages = images.filter(img => img.status === 'completed')
    .sort((a, b) => {
      // Sort by timestamp (newest first)
      const timeA = a.timestamp || 0;
      const timeB = b.timestamp || 0;
      return timeB - timeA;
    });
  
  const anyGenerating = images.some(img => img.status === 'generating');
  const failedImages = images.filter(img => img.status === 'failed');
  
  // Default to first image (index 0) or handle empty array
  const activeImageIndex = completedImages.length > 0 ? 0 : 0;
  
  const handleRetry = () => {
    onCreateAgain();
  };
  
  const handleRemoveFailedImage = () => {
    if (failedImages.length > 0 && failedImages[0].batchIndex !== undefined) {
      onDeleteImage(batchId, failedImages[0].batchIndex);
    }
  };
  
  const handleNavigatePrev = () => {
    // This is just a placeholder since navigation is handled by the parent
    // in the rolled-up view, but needed for the ImageBatchItem props
  };
  
  const handleNavigateNext = () => {
    // This is just a placeholder since navigation is handled by the parent
    // in the rolled-up view, but needed for the ImageBatchItem props
  };

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
              onCreateAgain={onCreateAgain}
              onUseAsInput={(url) => onImageClick(url, completedImages[activeImageIndex]?.prompt || '')}
              onDeleteImage={onDeleteImage}
              onFullScreen={() => onFullScreenClick(completedImages[activeImageIndex])}
              onImageClick={(url) => onImageClick(url, completedImages[activeImageIndex]?.prompt || '')}
              onNavigatePrev={completedImages.length > 1 ? handleNavigatePrev : undefined}
              onNavigateNext={completedImages.length > 1 ? handleNavigateNext : undefined}
              viewMode={viewMode}
              showActions={true}
              isRolledUp={true}
            />
          ) : anyGenerating ? (
            <LoadingPlaceholder prompt={failedImages.length > 0 ? failedImages[0]?.prompt : null} />
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
