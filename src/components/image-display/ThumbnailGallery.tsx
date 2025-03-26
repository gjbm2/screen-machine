
import React from 'react';
import { X, RotateCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import LoadingPlaceholder from './LoadingPlaceholder';

interface ThumbnailGalleryProps {
  images: Array<any>;
  generatingImages?: Array<any>;
  batchId: string;
  activeIndex: number;
  onThumbnailClick: (index: number) => void;
  onDeleteImage?: (batchId: string, index: number) => void;
  onCreateAgain?: () => void;
}

const ThumbnailGallery: React.FC<ThumbnailGalleryProps> = ({
  images,
  generatingImages = [],
  batchId,
  activeIndex,
  onThumbnailClick,
  onDeleteImage,
  onCreateAgain
}) => {
  // If no images to display, don't render anything
  if (images.length === 0 && generatingImages.length === 0) return null;

  return (
    <div className="flex flex-wrap gap-1">
      {/* Completed image thumbnails */}
      {images.map((image, index) => (
        <div 
          key={`thumb-${batchId}-${index}`}
          className={`relative rounded-md overflow-hidden cursor-pointer border-2 ${
            activeIndex === index ? 'border-primary' : 'border-transparent'
          }`}
          style={{ width: '70px', height: '70px' }}
          onClick={() => onThumbnailClick(index)}
        >
          <img 
            src={image.url} 
            alt={`Thumbnail ${index + 1}`}
            className="w-full h-full object-cover"
          />
          
          {onDeleteImage && (
            <Button
              variant="ghost"
              size="icon"
              className="absolute top-0 right-0 h-6 w-6 p-1 bg-black/50 hover:bg-black/70 text-white rounded-full"
              onClick={(e) => {
                e.stopPropagation();
                onDeleteImage(batchId, index);
              }}
            >
              <X className="h-3 w-3" />
            </Button>
          )}
        </div>
      ))}

      {/* Loading placeholders for generating images */}
      {generatingImages.map((image, index) => (
        <div 
          key={`generating-thumb-${batchId}-${index}`}
          className="relative rounded-md overflow-hidden"
          style={{ width: '70px', height: '70px' }}
        >
          <LoadingPlaceholder 
            prompt={image.prompt} 
            isCompact={true}
            workflowName={image.workflow}
            hasReferenceImages={Boolean(image.referenceImageUrl)}
          />
        </div>
      ))}

      {/* Create again button (the plus icon for creating more images) */}
      {onCreateAgain && (
        <div 
          className="flex items-center justify-center bg-muted/30 hover:bg-muted/50 rounded-md cursor-pointer transition-colors"
          style={{ width: '70px', height: '70px' }}
          onClick={onCreateAgain}
        >
          <RotateCw className="h-5 w-5 text-muted-foreground" />
        </div>
      )}
    </div>
  );
};

export default ThumbnailGallery;
