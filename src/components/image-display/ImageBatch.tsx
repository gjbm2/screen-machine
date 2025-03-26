
import React from 'react';
import ImageBatchItem from './ImageBatchItem';
import NewVariantPlaceholder from './NewVariantPlaceholder';
import { Card } from '@/components/ui/card';
import { cn } from '@/lib/utils';
import BatchCountDisplay from './BatchCountDisplay';
import PublishMenu from './PublishMenu';
import DeleteBatchDialog from './batch-components/DeleteBatchDialog';
import { useIsMobile } from '@/hooks/use-mobile';

interface ImageBatchProps {
  batchId: string;
  images: any[];
  onDelete: (batchId: string, index: number) => void;
  onCreateAgain: (batchId: string) => void;
  onFullscreenView: (batchId: string, imageIndex: number) => void;
  onUseAsInput: (url: string) => void;
  onDeleteBatch: (batchId: string) => void;
  activeGenerations?: string[]; // Add this prop
}

const ImageBatch: React.FC<ImageBatchProps> = ({
  batchId,
  images,
  onDelete,
  onCreateAgain,
  onFullscreenView,
  onUseAsInput,
  onDeleteBatch,
  activeGenerations = []  // Default to empty array
}) => {
  const isMobile = useIsMobile();
  const completedImages = images.filter(img => img.status === 'completed');
  
  // Only show images that have been completed
  return (
    <div className="space-y-2 relative" data-batch-id={batchId}>
      <div className="flex items-center justify-between">
        <BatchCountDisplay count={completedImages.length} />
        
        <div className="flex space-x-1">
          {completedImages.length > 0 && (
            <PublishMenu 
              imageUrl={completedImages[0].url}
              generationInfo={{
                prompt: completedImages[0].prompt,
                workflow: completedImages[0].workflow,
                params: completedImages[0].params
              }}
              isRolledUp={true}
              showLabel={false}
            />
          )}
          
          <DeleteBatchDialog
            onConfirm={() => onDeleteBatch(batchId)}
            label={`Delete all ${completedImages.length} images`}
            isRolledUp={true}
          />
        </div>
      </div>
      
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-3 lg:grid-cols-4 gap-2">
        {completedImages.map((image, index) => (
          <ImageBatchItem
            key={`${image.batchId}-${image.batchIndex || index}`}
            image={image}
            onDelete={() => onDelete(batchId, image.batchIndex)}
            onFullscreenView={() => onFullscreenView(batchId, image.batchIndex)}
            onUseAsInput={() => onUseAsInput(image.url)}
            onCreateAgain={() => onCreateAgain(batchId)}
          />
        ))}
        
        {/* Add the new variant placeholder with the activeGenerations prop */}
        <NewVariantPlaceholder 
          batchId={batchId} 
          onClick={onCreateAgain}
          activeGenerations={activeGenerations}
        />
      </div>
    </div>
  );
};

export default ImageBatch;
