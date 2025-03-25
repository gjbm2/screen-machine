
import React from 'react';
import { ChevronUp, ChevronDown, Trash } from 'lucide-react';
import { Button } from '@/components/ui/button';
import RolledUpBatchView from './batch-components/RolledUpBatchView';
import ExpandedBatchView from './batch-components/ExpandedBatchView';
import { ViewMode } from './ImageDisplay';

interface ImageBatchProps {
  batchId: string;
  images: any[];
  isExpanded: boolean;
  toggleExpand: (batchId: string) => void;
  onImageClick: (url: string, prompt: string) => void;
  onCreateAgain: () => void;
  onDeleteImage: (batchId: string, index: number) => void;
  onDeleteContainer: (batchId: string) => void;
  activeImageUrl: string | null;
  viewMode: ViewMode;
  onFullScreenClick: (image: any) => void;
  thumbnailsAlignment?: 'left' | 'right';
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
  activeImageUrl,
  viewMode,
  onFullScreenClick,
  thumbnailsAlignment = 'left'
}) => {
  const hasCompletedImages = Array.isArray(images) && images.some(img => img?.status === 'completed');
  const hasGeneratingImages = Array.isArray(images) && images.some(img => img?.status === 'generating');
  
  // Skip empty batches
  if (!Array.isArray(images) || images.length === 0) {
    console.warn(`Skipping empty batch with ID: ${batchId}`);
    return null;
  }
  
  // Handler for CreateAgain in this specific batch
  const handleCreateAgain = () => {
    onCreateAgain();
  };
  
  return (
    <div className="relative border rounded overflow-hidden bg-background shadow-sm mb-2">
      <div className="flex items-center justify-between px-2 py-1 bg-muted/30">
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => toggleExpand(batchId)}
            className="h-7 w-7 p-0"
            aria-label={isExpanded ? "Collapse batch" : "Expand batch"}
          >
            {isExpanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          </Button>
          <span className="text-xs font-medium">
            {isExpanded ? "Collapse" : "Expand"} ({images.length} image{images.length !== 1 ? 's' : ''})
          </span>
        </div>
        
        <Button
          variant="ghost"
          size="sm"
          onClick={() => onDeleteContainer(batchId)}
          className="h-7 w-7 p-0 hover:bg-red-100 hover:text-red-600"
          aria-label="Delete batch"
        >
          <Trash size={14} />
        </Button>
      </div>
      
      {isExpanded ? (
        <ExpandedBatchView
          batchId={batchId}
          images={images}
          onCreateAgain={handleCreateAgain}
          onDeleteImage={onDeleteImage}
          onImageClick={onImageClick}
          onFullScreenClick={onFullScreenClick}
          thumbnailsAlignment={thumbnailsAlignment}
        />
      ) : (
        <RolledUpBatchView
          batchId={batchId}
          images={images}
          onCreateAgain={handleCreateAgain}
          onDeleteImage={onDeleteImage}
          onImageClick={onImageClick}
          onFullScreenClick={onFullScreenClick}
          viewMode={viewMode}
        />
      )}
    </div>
  );
};

export default ImageBatch;
