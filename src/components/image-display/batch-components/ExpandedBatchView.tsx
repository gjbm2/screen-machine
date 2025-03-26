
import React from 'react';
import NewVariantPlaceholder from '../NewVariantPlaceholder';
import { Button } from '@/components/ui/button';
import { Trash, Maximize, RefreshCw } from 'lucide-react';
import ImageBatchItem from '../ImageBatchItem';
import GenerationFailedPlaceholder from '../GenerationFailedPlaceholder';

interface ExpandedBatchViewProps {
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
  toggleExpand: (batchId: string) => void;
  activeGenerations?: string[]; // Add activeGenerations prop
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
  toggleExpand,
  activeGenerations = [] // Default to empty array
}) => {
  return (
    <div className="mt-1">
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-2">
        {completedImages.map((image, index) => (
          <ImageBatchItem
            key={`${batchId}-${image.batchIndex || index}`}
            imageUrl={image.url}
            prompt={image.prompt}
            batchId={batchId}
            batchIndex={image.batchIndex || index}
            onImageClick={() => onImageClick(image.url, image.prompt)}
            onUseAsInput={() => onImageClick(image.url, image.prompt)}
            onCreateAgain={handleCreateAgain}
            onFullScreenClick={() => handleFullScreenClick(image)}
            onDeleteImage={() => onDeleteImage(batchId, image.batchIndex || index)}
            isPlaceholder={false}
            hasError={false}
          />
        ))}
        
        {/* Failed image placeholder */}
        {failedImages.length > 0 && (
          <GenerationFailedPlaceholder
            onRemove={handleRemoveFailedImage}
            onRetry={handleRetry}
          />
        )}
        
        {/* New variant placeholder */}
        <NewVariantPlaceholder
          batchId={batchId}
          onClick={handleCreateAgain}
          activeGenerations={activeGenerations} // Pass activeGenerations to NewVariantPlaceholder
        />
      </div>
    </div>
  );
};

export default ExpandedBatchView;
