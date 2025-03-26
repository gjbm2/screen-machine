
import React from 'react';
import { Grid } from '@/components/ui/grid';
import ImageBatchItem from '../ImageBatchItem';
import LoadingPlaceholder from '../LoadingPlaceholder';

interface SmallGridViewProps {
  sortedContainerIds: string[];
  batches: Record<string, any[]>;
  expandedContainers: Record<string, boolean>;
  handleToggleExpand: (batchId: string) => void;
  onUseGeneratedAsInput: (url: string) => void;
  onCreateAgain: (batchId?: string) => void;
  onDeleteImage: (batchId: string, index: number) => void;
  onDeleteContainer: (batchId: string) => void;
  onFullScreenClick: (image: any) => void;
  imageUrl: string | null;
  getAllImages: () => any[];
  handleSmallImageClick: (image: any) => void;
  isLoading: boolean;
  activeGenerations?: string[];
}

const SmallGridView: React.FC<SmallGridViewProps> = ({
  sortedContainerIds,
  batches,
  expandedContainers,
  handleToggleExpand,
  onUseGeneratedAsInput,
  onCreateAgain,
  onDeleteImage,
  onDeleteContainer,
  onFullScreenClick,
  imageUrl,
  getAllImages,
  handleSmallImageClick,
  isLoading,
  activeGenerations = []
}) => {
  // Get flattened list of all completed images across all batches
  const allImages = getAllImages().filter(img => img.status === 'completed');
  
  return (
    <div>
      {isLoading && sortedContainerIds.length === 0 && (
        <LoadingPlaceholder prompt="Generating your image..." />
      )}
      
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-2">
        {allImages.map((image, index) => (
          <div key={`${image.batchId}-${image.batchIndex || index}`} className="relative">
            <ImageBatchItem
              image={image}
              batchId={image.batchId}
              index={image.batchIndex || index}
              total={allImages.length}
              onImageClick={() => handleSmallImageClick(image)}
              onCreateAgain={() => onCreateAgain(image.batchId)}
              onDeleteImage={onDeleteImage}
              onFullScreen={() => onFullScreenClick(image)}
              onUseAsInput={() => onUseGeneratedAsInput(image.url)}
              viewMode="small"
              showActions={true}
            />
          </div>
        ))}
      </div>
    </div>
  );
};

export default SmallGridView;
