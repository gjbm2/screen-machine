
import React from 'react';
import RolledUpBatchView from './batch-components/RolledUpBatchView';
import ExpandedBatchView from './batch-components/ExpandedBatchView';
import NewVariantPlaceholder from './NewVariantPlaceholder';
import { ViewMode } from './ImageDisplay';
import { useSortable } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';

interface ImageBatchProps {
  batchId: string;
  images: any[];
  isExpanded: boolean;
  toggleExpand: (batchId: string) => void;
  onImageClick: (url: string, prompt: string) => void;
  onCreateAgain: () => void;
  onDeleteImage: (batchId: string, index: number) => void;
  onDeleteContainer: (batchId: string) => void;
  activeImageUrl?: string | null;
  viewMode?: ViewMode;
  onFullScreenClick?: (image: any) => void;
  hasGeneratingImages?: boolean;
  activeGenerations?: string[]; // Add activeGenerations prop
}

const ImageBatch: React.FC<ImageBatchProps> = ({
  batchId,
  images,
  isExpanded,
  toggleExpand,
  onImageClick,
  onCreateAgain,
  onDeleteImage,
  onDeleteContainer,
  activeImageUrl = null,
  viewMode = 'normal',
  onFullScreenClick,
  hasGeneratingImages = false,
  activeGenerations = [] // Default to empty array
}) => {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
  } = useSortable({ id: batchId });
  
  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
  };

  // Function to create a new variant for this batch
  const handleCreateVariant = () => {
    onCreateAgain();
  };

  const hasImages = images && images.length > 0;

  return (
    <div ref={setNodeRef} style={style} {...attributes} className="mb-4">
      {isExpanded ? (
        <ExpandedBatchView
          batchId={batchId}
          images={images}
          onImageClick={onImageClick}
          toggleExpand={toggleExpand}
          onCreateAgain={onCreateAgain}
          onDeleteImage={onDeleteImage}
          onDeleteContainer={onDeleteContainer}
          dragHandleProps={listeners}
          onFullScreenClick={onFullScreenClick}
          viewMode={viewMode}
          activeGenerations={activeGenerations} // Pass activeGenerations
        />
      ) : (
        <RolledUpBatchView
          batchId={batchId}
          images={images}
          onImageClick={onImageClick}
          toggleExpand={toggleExpand}
          onCreateAgain={onCreateAgain}
          onDeleteImage={onDeleteImage}
          onDeleteContainer={onDeleteContainer}
          dragHandleProps={listeners}
          onFullScreenClick={onFullScreenClick}
          viewMode={viewMode}
          activeGenerations={activeGenerations} // Pass activeGenerations
        />
      )}
      
      {isExpanded && !hasGeneratingImages && (
        <div className="mt-2">
          <NewVariantPlaceholder 
            batchId={batchId} 
            onClick={handleCreateVariant} 
            className="w-full"
            activeGenerations={activeGenerations} // Pass activeGenerations
          />
        </div>
      )}
    </div>
  );
};

export default ImageBatch;
