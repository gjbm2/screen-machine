
import React from 'react';
import { Button } from '@/components/ui/button';
import { Maximize, RefreshCw } from 'lucide-react';
import NewVariantPlaceholder from '../NewVariantPlaceholder';
import GenerationFailedPlaceholder from '../GenerationFailedPlaceholder';
import { ViewMode } from '../ImageDisplay';
import ImageBatchItem from '../ImageBatchItem';

interface RolledUpBatchViewProps {
  batchId: string;
  completedImages: any[];
  anyGenerating: boolean;
  failedImages: any[];
  activeImageIndex: number;
  setActiveImageIndex: React.Dispatch<React.SetStateAction<number>>;
  handleCreateAgain: () => void;
  handleFullScreenClick: (image: any) => void;
  handleRemoveFailedImage: () => void;
  handleRetry: () => void;
  onImageClick: (url: string, prompt: string) => void;
  onDeleteImage: (batchId: string, index: number) => void;
  viewMode: ViewMode;
  activeGenerations?: string[]; // Add activeGenerations prop
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
  viewMode,
  activeGenerations = [] // Default to empty array
}) => {
  // Current image to display in rolled up view
  const currentImage = completedImages[activeImageIndex] || null;
  
  return (
    <div className="pt-1 grid grid-cols-3 gap-2">
      {/* Display the current image in the rolled up view */}
      {currentImage && (
        <div className="col-span-2">
          <ImageBatchItem
            key={`${batchId}-${currentImage.batchIndex || activeImageIndex}`}
            imageUrl={currentImage.url}
            prompt={currentImage.prompt}
            batchId={batchId}
            batchIndex={currentImage.batchIndex || activeImageIndex}
            onImageClick={() => onImageClick(currentImage.url, currentImage.prompt)}
            onUseAsInput={() => onImageClick(currentImage.url, currentImage.prompt)}
            onCreateAgain={handleCreateAgain}
            onFullScreenClick={() => handleFullScreenClick(currentImage)}
            onDeleteImage={() => onDeleteImage(batchId, currentImage.batchIndex || activeImageIndex)}
            isPlaceholder={false}
            hasError={false}
            isRolledUp={true}
          />
        </div>
      )}
      
      {/* Display placeholders in small view */}
      <div className="col-span-1 grid grid-cols-1 gap-2">
        {/* Show new variant placeholder */}
        <NewVariantPlaceholder
          batchId={batchId}
          onClick={handleCreateAgain}
          className="h-full"
          activeGenerations={activeGenerations} // Pass activeGenerations to NewVariantPlaceholder
        />
        
        {/* Show failed placeholder if any failed images */}
        {failedImages.length > 0 && (
          <GenerationFailedPlaceholder
            onRemove={handleRemoveFailedImage}
            onRetry={handleRetry}
            compact={true}
          />
        )}
      </div>
    </div>
  );
};

export default RolledUpBatchView;
