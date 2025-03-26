
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
  const handleNavigatePrev = (e: React.MouseEvent) => {
    e.stopPropagation();
    // Navigate to previous image if available
    if (activeImageIndex > 0) {
      // This would be handled by the parent component
      console.log('Navigate to previous image', activeImageIndex - 1);
    }
  };
  
  const handleNavigateNext = (e: React.MouseEvent) => {
    e.stopPropagation();
    // Navigate to next image if available
    if (activeImageIndex < completedImages.length - 1) {
      // This would be handled by the parent component
      console.log('Navigate to next image', activeImageIndex + 1);
    }
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
