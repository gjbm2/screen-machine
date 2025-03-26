
import React from 'react';
import ImageBatchItemContent from './ImageBatchItemContent';
import { Card } from '@/components/ui/card';
import LoadingPlaceholder from './LoadingPlaceholder';

interface ImageBatchItemProps {
  url: string;
  prompt: string;
  batchId: string;
  batchIndex: number;
  onImageClick: () => void;
  onUseAsInput: () => void;
  onCreateAgain: () => void;
  onFullScreenClick: () => void;
  onDeleteImage: () => void;
  isActive?: boolean;
  isPlaceholder?: boolean;
  hasError?: boolean;
  referenceImageUrl?: string | null;
  isRolledUp?: boolean;
  activeGenerations?: string[]; // Add activeGenerations prop
}

const ImageBatchItem: React.FC<ImageBatchItemProps> = ({
  url,
  prompt,
  batchId,
  batchIndex,
  onImageClick,
  onUseAsInput,
  onCreateAgain,
  onFullScreenClick,
  onDeleteImage,
  isActive = false,
  isPlaceholder = false,
  hasError = false,
  referenceImageUrl = null,
  isRolledUp = false,
  activeGenerations = [] // Default to empty array
}) => {
  if (isPlaceholder) {
    return (
      <LoadingPlaceholder 
        prompt={prompt} 
        isCompact={isRolledUp} 
      />
    );
  }

  if (hasError) {
    return (
      <div className="w-full h-full flex items-center justify-center bg-muted text-muted-foreground">
        Error loading image
      </div>
    );
  }

  return (
    <Card className={`overflow-hidden ${isActive ? 'ring-2 ring-primary' : ''}`}>
      <div className="relative">
        <ImageBatchItemContent
          url={url}
          prompt={prompt}
          onClick={onImageClick}
          onUseAsInput={onUseAsInput}
          onCreateAgain={onCreateAgain}
          onFullScreenClick={onFullScreenClick}
          onDeleteImage={onDeleteImage}
          referenceImageUrl={referenceImageUrl}
          isRolledUp={isRolledUp}
          batchId={batchId}
          activeGenerations={activeGenerations} // Pass activeGenerations
        />
      </div>
    </Card>
  );
};

export default ImageBatchItem;
