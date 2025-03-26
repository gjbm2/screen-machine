
import React from 'react';
import ImageBatch from '../ImageBatch';
import { Button } from '@/components/ui/button'; // Fixed import from card to button
import LoadingPlaceholder from '../LoadingPlaceholder';

interface NormalGridViewProps {
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
  isLoading: boolean;
  activeGenerations?: string[]; // Add activeGenerations prop
}

const NormalGridView: React.FC<NormalGridViewProps> = ({
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
  isLoading,
  activeGenerations = [] // Default to empty array
}) => {
  return (
    <div className="flex flex-col gap-4 mb-4">
      {isLoading && sortedContainerIds.length === 0 && (
        <LoadingPlaceholder prompt="Generating your image..." />
      )}
      
      {sortedContainerIds.map(batchId => {
        const batchImages = batches[batchId] || [];
        
        // Special check for any images that are in 'generating' state
        const hasGeneratingImages = batchImages.some(img => img.status === 'generating');
        
        return (
          <ImageBatch
            key={batchId}
            batchId={batchId}
            images={batchImages}
            isExpanded={expandedContainers[batchId] || false}
            toggleExpand={handleToggleExpand}
            onImageClick={(url, prompt) => onUseGeneratedAsInput(url)}
            onCreateAgain={() => onCreateAgain(batchId)}
            onDeleteImage={onDeleteImage}
            onDeleteContainer={() => onDeleteContainer(batchId)}
            onFullScreenClick={onFullScreenClick}
            activeImageUrl={imageUrl}
            viewMode="normal"
            hasGeneratingImages={hasGeneratingImages}
            activeGenerations={activeGenerations} // Pass activeGenerations to ImageBatch
          />
        );
      })}
    </div>
  );
};

export default NormalGridView;
