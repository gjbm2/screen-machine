
import React from 'react';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import ImageBatchItem from '../ImageBatchItem';
import LoadingPlaceholder from '../LoadingPlaceholder';
import GenerationFailedPlaceholder from '../GenerationFailedPlaceholder';
import { ImageGenerationStatus } from '@/types/workflows';

interface ExpandedBatchViewProps {
  batchId: string;
  completedImages: Array<any>;
  anyGenerating: boolean;
  failedImages: Array<any>;
  activeImageIndex: number;
  setActiveImageIndex: (index: number) => void;
  handleCreateAgain: () => void;
  handleFullScreenClick: (image: any) => void;
  handleRemoveFailedImage: () => void;
  handleRetry: () => void;
  onImageClick: (url: string, prompt: string) => void;
  onDeleteImage: (batchId: string, index: number) => void;
  toggleExpand: (id: string) => void;
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
  const handleNavigatePrev = () => {
    if (activeImageIndex > 0) {
      setActiveImageIndex(activeImageIndex - 1);
    }
  };
  
  const handleNavigateNext = () => {
    if (activeImageIndex < completedImages.length - 1) {
      setActiveImageIndex(activeImageIndex + 1);
    }
  };

  return (
    <Card className="rounded-t-none">
      <CardContent className="p-2">
        <div className="space-y-2">
          <div className="aspect-square relative bg-secondary/10 rounded-md overflow-hidden max-w-full mx-auto">
            {completedImages.length > 0 ? (
              <ImageBatchItem
                image={completedImages[activeImageIndex]}
                batchId={batchId}
                index={activeImageIndex}
                total={completedImages.length}
                onCreateAgain={handleCreateAgain}
                onUseAsInput={(url) => onImageClick(url, completedImages[activeImageIndex]?.prompt || '')}
                onDeleteImage={onDeleteImage}
                onFullScreen={() => handleFullScreenClick(completedImages[activeImageIndex])}
                onImageClick={(url) => onImageClick(url, completedImages[activeImageIndex]?.prompt || '')}
                onNavigatePrev={completedImages.length > 1 ? handleNavigatePrev : undefined}
                onNavigateNext={completedImages.length > 1 ? handleNavigateNext : undefined}
                viewMode="normal"
                showActions={true}
              />
            ) : anyGenerating ? (
              <LoadingPlaceholder prompt={failedImages.length > 0 ? failedImages[0]?.prompt : null} />
            ) : failedImages.length > 0 ? (
              <GenerationFailedPlaceholder 
                prompt={failedImages[0]?.prompt || null} 
                onRetry={handleRetry}
                onRemove={handleRemoveFailedImage}
              />
            ) : null}
          </div>

          <div className="flex flex-wrap gap-1 justify-start pt-1">
            {/* Order the thumbnails with most recent (highest timestamp) first */}
            {[...completedImages]
              .sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0))
              .map((image, idx) => {
                // Find the actual index in the original completedImages array for correct navigation
                const originalIndex = completedImages.findIndex(img => img.url === image.url);
                return (
                  <div 
                    key={`thumb-${batchId}-${idx}`}
                    className={`w-14 h-14 rounded-md overflow-hidden cursor-pointer border-2 ${
                      originalIndex === activeImageIndex ? 'border-primary' : 'border-transparent'
                    }`}
                    onClick={() => setActiveImageIndex(originalIndex)}
                  >
                    <img 
                      src={image.url} 
                      alt={`Thumbnail ${idx + 1}`}
                      className="w-full h-full object-cover"
                    />
                  </div>
                );
            })}
          </div>
          
          <div className="flex justify-center">
            <Button 
              variant="ghost" 
              size="sm"
              className="rounded-lg bg-card hover:bg-accent/20 text-xs h-7 px-3 border shadow"
              onClick={() => toggleExpand(batchId)}
            >
              Roll Up
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  );
};

export default ExpandedBatchView;
