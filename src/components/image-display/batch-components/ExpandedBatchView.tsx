
import React from 'react';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { ExternalLink, RotateCcw, Trash } from 'lucide-react';
import { Separator } from '@/components/ui/separator';
import ImageBatchItem from '../ImageBatchItem';
import GenerationFailedPlaceholder from '../GenerationFailedPlaceholder';
import LoadingPlaceholder from '../LoadingPlaceholder';
import { Skeleton } from '@/components/ui/skeleton';

interface ExpandedBatchViewProps {
  batchId: string;
  completedImages: any[];
  anyGenerating: boolean;
  failedImages: any[];
  activeImageIndex: number;
  setActiveImageIndex: (index: number) => void;
  handleCreateAgain: () => void;
  handleFullScreenClick: (image: any) => void;
  handleRemoveFailedImage: () => void;
  handleRetry: () => void;
  onImageClick: (url: string, prompt: string) => void;
  onDeleteImage: (batchId: string, index: number) => void;
  toggleExpand: (batchId: string) => void;
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
  toggleExpand
}) => {
  // Find generating images
  const generatingImages = failedImages.filter(img => img.status === 'generating');
  const errorImages = failedImages.filter(img => img.status === 'failed' || img.status === 'error');
  
  // First display the main large image
  const firstCompletedImage = completedImages.length > 0 ? completedImages[0] : null;
  
  // Function to navigate between images
  const handleNavigateNext = () => {
    if (activeImageIndex < completedImages.length - 1) {
      setActiveImageIndex(activeImageIndex + 1);
    }
  };
  
  const handleNavigatePrev = () => {
    if (activeImageIndex > 0) {
      setActiveImageIndex(activeImageIndex - 1);
    }
  };

  return (
    <div className="flex flex-col gap-4 p-3">
      {/* Main large image or error indicator */}
      <div className="w-full pt-[100%] relative rounded-md overflow-hidden">
        {firstCompletedImage ? (
          <ImageBatchItem
            image={firstCompletedImage}
            batchId={batchId}
            index={0}
            total={completedImages.length}
            onCreateAgain={handleCreateAgain}
            onDeleteImage={onDeleteImage}
            onFullScreen={handleFullScreenClick}
            onImageClick={(url) => onImageClick(url, firstCompletedImage.prompt || '')}
            onNavigateNext={handleNavigateNext}
            onNavigatePrev={handleNavigatePrev}
            viewMode="normal"
            isExpandedMain={true}
          />
        ) : errorImages.length > 0 ? (
          <GenerationFailedPlaceholder 
            errorMessage="Failed to generate image"
            onRemove={handleRemoveFailedImage}
            onRetry={handleRetry}
          />
        ) : generatingImages.length > 0 ? (
          <Card className="absolute inset-0 flex items-center justify-center">
            <LoadingPlaceholder 
              prompt={generatingImages[0]?.prompt || null}
              workflowName={generatingImages[0]?.workflow || undefined}
              hasReferenceImages={!!generatingImages[0]?.referenceImageUrl}
            />
          </Card>
        ) : (
          <div className="absolute inset-0 bg-muted flex items-center justify-center text-muted-foreground">
            No images available
          </div>
        )}
      </div>
      
      {/* Thumbnails */}
      {(completedImages.length > 0 || generatingImages.length > 0) && (
        <div className="mt-3">
          <div className="flex flex-wrap gap-2">
            {/* Completed image thumbnails */}
            {completedImages.map((image, index) => (
              <div 
                key={`${batchId}-completed-${index}`}
                className={`w-16 h-16 rounded-md overflow-hidden cursor-pointer transition-all duration-150 
                  ${index === activeImageIndex ? 'ring-2 ring-primary' : 'ring-1 ring-muted'}`}
                onClick={() => setActiveImageIndex(index)}
              >
                <img 
                  src={image.url} 
                  alt={`Thumbnail ${index}`}
                  className="w-full h-full object-cover"
                />
              </div>
            ))}
            
            {/* Loading placeholders for generating images */}
            {generatingImages.map((image, index) => (
              <div 
                key={`${batchId}-generating-${index}`}
                className="w-16 h-16 rounded-md overflow-hidden"
              >
                <LoadingPlaceholder 
                  prompt={null}
                  imageNumber={index + 1}
                  isCompact={true}
                />
              </div>
            ))}
          </div>
        </div>
      )}
      
      {/* Actions */}
      <div className="flex flex-wrap justify-between items-center gap-2 mt-1">
        <div className="flex items-center gap-2">
          <Button 
            size="sm" 
            variant="ghost" 
            className="text-xs"
            onClick={handleCreateAgain}
          >
            <RotateCcw className="h-3.5 w-3.5 mr-1" /> Create again
          </Button>
          
          <Button 
            size="sm" 
            variant="ghost"
            className="text-xs"
            onClick={() => toggleExpand(batchId)}
          >
            Collapse
          </Button>
        </div>
        
        <div className="flex gap-2 items-center">
          <span className="text-xs text-muted-foreground">
            {completedImages.length} {completedImages.length === 1 ? 'image' : 'images'}
            {generatingImages.length > 0 && ` • ${generatingImages.length} generating`}
          </span>
        </div>
      </div>
    </div>
  );
};

export default ExpandedBatchView;
