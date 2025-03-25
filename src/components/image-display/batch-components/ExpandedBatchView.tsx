
import React, { useState, useEffect } from 'react';
import { Separator } from '@/components/ui/separator';
import ImageBatchItem from '../ImageBatchItem';
import { ScrollArea } from '@/components/ui/scroll-area';
import LoadingPlaceholder from '../LoadingPlaceholder';
import GenerationFailedPlaceholder from '../GenerationFailedPlaceholder';
import NewVariantPlaceholder from '../NewVariantPlaceholder';

interface ExpandedBatchViewProps {
  batchId: string;
  images: any[];
  onCreateAgain: () => void;
  onDeleteImage: (batchId: string, index: number) => void;
  onImageClick: (url: string, prompt: string) => void;
  onFullScreenClick: (image: any) => void;
  thumbnailsAlignment?: 'left' | 'right';
}

const ExpandedBatchView: React.FC<ExpandedBatchViewProps> = ({
  batchId,
  images,
  onCreateAgain,
  onDeleteImage,
  onImageClick,
  onFullScreenClick,
  thumbnailsAlignment = 'left'
}) => {
  const [mainImage, setMainImage] = useState<any | null>(null);
  
  // Find completed images and sort them (newest first for thumbnails)
  const completedImages = images
    .filter(img => img.status === 'completed')
    .sort((a, b) => {
      // Sort by timestamp (newest first)
      const timeA = a.timestamp || 0;
      const timeB = b.timestamp || 0;
      return timeB - timeA;
    });
  
  const generatingImages = images.filter(img => img.status === 'generating');
  const failedImages = images.filter(img => img.status === 'failed');
  
  // Set main image to the first completed image or null if none
  useEffect(() => {
    if (completedImages.length > 0) {
      setMainImage(completedImages[0]);
    } else {
      setMainImage(null);
    }
  }, [completedImages.length]);
  
  // Handle manual thumbnail click
  const handleThumbnailClick = (image: any) => {
    setMainImage(image);
  };
  
  return (
    <div className="p-2 flex flex-col gap-2">
      {/* Main image display */}
      <div className="w-full aspect-square relative bg-muted/50 rounded-md overflow-hidden">
        {mainImage ? (
          <ImageBatchItem
            image={mainImage}
            batchId={batchId}
            index={mainImage.batchIndex || 0}
            total={completedImages.length}
            onCreateAgain={onCreateAgain}
            onDeleteImage={onDeleteImage}
            onUseAsInput={(url) => {
              // Directly handle the use as input action, don't trigger fullscreen
              if (mainImage && mainImage.url) {
                console.log('Using image as input from expanded view:', url);
                onImageClick(url, mainImage.prompt || '');
              }
            }}
            onFullScreen={() => {
              if (mainImage) {
                onFullScreenClick(mainImage);
              }
            }}
            viewMode="normal"
            onImageClick={(url) => onImageClick(url, mainImage.prompt || '')}
            showActions={true}
            isRolledUp={false}
          />
        ) : generatingImages.length > 0 ? (
          <LoadingPlaceholder prompt={generatingImages[0]?.prompt || null} />
        ) : failedImages.length > 0 ? (
          <GenerationFailedPlaceholder 
            prompt={failedImages[0]?.prompt || null} 
            onRetry={onCreateAgain}
          />
        ) : (
          <NewVariantPlaceholder batchId={batchId} onClick={onCreateAgain} />
        )}
      </div>
      
      {/* Thumbnail images row */}
      {completedImages.length > 1 && (
        <>
          <Separator className="my-1" />
          <ScrollArea className="w-full" type="scroll">
            <div className={`flex gap-1 py-1 overflow-x-auto ${thumbnailsAlignment === 'left' ? 'justify-start' : 'justify-end'}`}>
              {thumbnailsAlignment === 'left' 
                ? [...completedImages].map((image, idx) => (
                    <div 
                      key={`${image.batchId}-${image.batchIndex || idx}`}
                      className={`w-16 h-16 cursor-pointer rounded overflow-hidden flex-shrink-0 border-2 relative 
                        ${mainImage && mainImage.url === image.url ? 'border-primary' : 'border-transparent'}`}
                      onClick={() => handleThumbnailClick(image)}
                    >
                      <img 
                        src={image.url} 
                        alt={image.prompt || `Generated image ${idx + 1}`}
                        className="w-full h-full object-cover"
                      />
                    </div>
                  ))
                : [...completedImages].reverse().map((image, idx) => (
                    <div 
                      key={`${image.batchId}-${image.batchIndex || idx}`}
                      className={`w-16 h-16 cursor-pointer rounded overflow-hidden flex-shrink-0 border-2 relative 
                        ${mainImage && mainImage.url === image.url ? 'border-primary' : 'border-transparent'}`}
                      onClick={() => handleThumbnailClick(image)}
                    >
                      <img 
                        src={image.url} 
                        alt={image.prompt || `Generated image ${idx + 1}`}
                        className="w-full h-full object-cover"
                      />
                    </div>
                  ))
              }
            </div>
          </ScrollArea>
        </>
      )}
    </div>
  );
};

export default ExpandedBatchView;
