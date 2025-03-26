
import React from 'react';
import { Card } from '@/components/ui/card';
import { ChevronUp } from 'lucide-react';
import { Button } from '@/components/ui/button';
import BatchCountDisplay from '../BatchCountDisplay';
import ImageBatchItem from '../ImageBatchItem';
import GenerationFailedPlaceholder from '../GenerationFailedPlaceholder';
import { ViewMode } from '../ImageDisplay';

interface ExpandedBatchViewProps {
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

const ExpandedBatchView: React.FC<ExpandedBatchViewProps> = ({
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
  // Filter completed images for display
  const completedImages = images.filter(img => img.status === 'completed');
  const generatingImages = images.filter(img => img.status === 'generating');
  const failedImages = images.filter(img => img.status === 'failed' || img.status === 'error');
  
  // Get the first (newest) completed image if available for the batch thumbnail
  const firstImage = completedImages.length > 0 ? completedImages[0] : null;
  
  // Get basic information from the first image
  const prompt = firstImage?.prompt || 'Unknown';
  const workflow = firstImage?.workflow || null;
  const timestamp = firstImage?.timestamp || null;

  // Handle image click
  const handleImageClick = (image: any) => {
    if (image.url && onImageClick) {
      onImageClick(image.url, image.prompt || '');
    }
    if (onFullScreenClick) {
      onFullScreenClick(image);
    }
  };

  return (
    <Card className="overflow-hidden w-full">
      <div className="bg-card p-2 flex items-center justify-between border-b">
        <div className="flex items-center">
          <Button 
            variant="ghost" 
            size="sm" 
            className="h-8 w-8 p-0 mr-1"
            onClick={() => toggleExpand(batchId)}
          >
            <ChevronUp className="h-4 w-4" />
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
      
      <div className="p-2">
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-2">
          {/* Completed Images */}
          {completedImages.map((image, index) => (
            <ImageBatchItem
              key={`${batchId}-${image.batchIndex || index}`}
              url={image.url}
              prompt={image.prompt}
              batchId={batchId}
              batchIndex={image.batchIndex || index}
              onImageClick={() => handleImageClick(image)}
              onUseAsInput={() => {}}
              onCreateAgain={onCreateAgain}
              onFullScreenClick={() => {
                if (onFullScreenClick) onFullScreenClick(image);
              }}
              onDeleteImage={() => onDeleteImage(batchId, image.batchIndex || index)}
              isActive={false}
              referenceImageUrl={image.referenceImageUrl}
              isRolledUp={false}
              activeGenerations={activeGenerations} // Pass activeGenerations
            />
          ))}
          
          {/* Generating Images - Loading Placeholders */}
          {generatingImages.map((image, index) => (
            <ImageBatchItem
              key={`${batchId}-generating-${index}`}
              url=""
              prompt={image.prompt}
              batchId={batchId}
              batchIndex={-1}
              onImageClick={() => {}}
              onUseAsInput={() => {}}
              onCreateAgain={() => {}}
              onFullScreenClick={() => {}}
              onDeleteImage={() => {}}
              isPlaceholder={true}
              isActive={false}
              isRolledUp={false}
              activeGenerations={activeGenerations} // Pass activeGenerations
            />
          ))}
          
          {/* Failed Images */}
          {failedImages.map((image, index) => (
            <div key={`${batchId}-failed-${index}`} className="aspect-square">
              <GenerationFailedPlaceholder 
                prompt={image.prompt} 
                onRetry={onCreateAgain}
                onRemove={() => onDeleteImage(batchId, image.batchIndex || index)}
              />
            </div>
          ))}
        </div>
      </div>
    </Card>
  );
};

export default ExpandedBatchView;
