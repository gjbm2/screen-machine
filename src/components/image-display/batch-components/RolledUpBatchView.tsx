
import React from 'react';
import { Card } from '@/components/ui/card';
import { ChevronDown } from 'lucide-react';
import { Button } from '@/components/ui/button';
import BatchCountDisplay from '../BatchCountDisplay';
import ImageBatchItem from '../ImageBatchItem';
import GenerationFailedPlaceholder from '../GenerationFailedPlaceholder';
import { ViewMode } from '../ImageDisplay';

interface RolledUpBatchViewProps {
  batchId: string;
  images: any[];
  onImageClick: (url: string, prompt: string) => void;
  toggleExpand: (batchId: string) => void;
  onCreateAgain: () => void;
  onDeleteImage: (batchId: string, index: number) => void;
  onDeleteContainer: (batchId: string) => void;
  dragHandleProps: any;
  onFullScreenClick?: (image: any) => void;
  viewMode?: ViewMode;
  activeGenerations?: string[]; // Add activeGenerations prop
}

const RolledUpBatchView: React.FC<RolledUpBatchViewProps> = ({
  batchId,
  images,
  onImageClick,
  toggleExpand,
  onCreateAgain,
  onDeleteImage,
  onDeleteContainer,
  dragHandleProps,
  onFullScreenClick,
  viewMode = 'normal',
  activeGenerations = [] // Default to empty array
}) => {
  // Completed images in this batch
  const completedImages = images.filter(img => img.status === 'completed');
  
  // Generating images in this batch
  const generatingImages = images.filter(img => img.status === 'generating');
  
  // Failed images in this batch
  const failedImages = images.filter(img => img.status === 'failed' || img.status === 'error');
  
  // Get the first (newest) completed image if available
  const firstImage = completedImages.length > 0 ? completedImages[0] : null;
  
  // Get basic information from the first image
  const prompt = firstImage?.prompt || 'Unknown';
  const workflow = firstImage?.workflow || null;
  const timestamp = firstImage?.timestamp || null;

  // Handle image click
  const handleImageClick = (image: any) => {
    // Pass to ImageBatchItem's onImageClick
    if (image.url && onImageClick) {
      onImageClick(image.url, image.prompt || '');
    }
    
    // Also pass to fullscreen handler if provided
    if (onFullScreenClick) {
      onFullScreenClick(image);
    }
  };

  return (
    <Card className="overflow-hidden w-full flex flex-col">
      <div className="bg-card p-2 flex items-center justify-between border-b">
        <div className="flex items-center">
          <Button 
            variant="ghost" 
            size="sm" 
            className="h-8 w-8 p-0 mr-1"
            onClick={() => toggleExpand(batchId)}
          >
            <ChevronDown className="h-4 w-4" />
          </Button>
          <div {...dragHandleProps} className="cursor-grab active:cursor-grabbing">
            <BatchCountDisplay 
              batchCount={completedImages.length} 
              totalCount={images.length} 
              prompt={prompt}
              workflow={workflow}
              timestamp={timestamp}
              hasGenerating={generatingImages.length > 0}
              hasFailed={failedImages.length > 0}
            />
          </div>
        </div>
      </div>
      
      <div className="p-2 flex-1 flex">
        {firstImage ? (
          <div className="grid grid-cols-1 gap-2 w-full">
            <ImageBatchItem
              key={`${batchId}-${firstImage.batchIndex || 0}`}
              url={firstImage.url}
              prompt={firstImage.prompt}
              batchId={batchId}
              batchIndex={firstImage.batchIndex || 0}
              onImageClick={() => handleImageClick(firstImage)}
              onUseAsInput={() => {}}
              onCreateAgain={onCreateAgain}
              onFullScreenClick={() => {
                if (onFullScreenClick) onFullScreenClick(firstImage);
              }}
              onDeleteImage={() => onDeleteImage(batchId, firstImage.batchIndex || 0)}
              isActive={false}
              referenceImageUrl={firstImage.referenceImageUrl}
              isRolledUp={true}
              activeGenerations={activeGenerations} // Pass activeGenerations
            />
          </div>
        ) : generatingImages.length > 0 ? (
          <div className="grid grid-cols-1 gap-2 w-full">
            <ImageBatchItem
              key={`${batchId}-generating-0`}
              url=""
              prompt={generatingImages[0].prompt}
              batchId={batchId}
              batchIndex={-1}
              onImageClick={() => {}}
              onUseAsInput={() => {}}
              onCreateAgain={() => {}}
              onFullScreenClick={() => {}}
              onDeleteImage={() => {}}
              isPlaceholder={true}
              isActive={false}
              isRolledUp={true}
              activeGenerations={activeGenerations} // Pass activeGenerations
            />
          </div>
        ) : failedImages.length > 0 ? (
          <div className="w-full aspect-square">
            <GenerationFailedPlaceholder 
              prompt={failedImages[0].prompt} 
              onRetry={onCreateAgain}
              onRemove={() => onDeleteImage(batchId, failedImages[0].batchIndex || 0)}
              isCompact={true}
            />
          </div>
        ) : (
          <div className="w-full text-center p-4 text-muted-foreground">
            No images found
          </div>
        )}
      </div>
    </Card>
  );
};

export default RolledUpBatchView;
