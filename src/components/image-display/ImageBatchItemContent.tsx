
import React from 'react';
import { ViewMode } from './ImageDisplay';
import ReferenceImageIndicator from './ReferenceImageIndicator';
import { AspectRatio } from '@/components/ui/aspect-ratio';

interface ImageBatchItemContentProps {
  imageUrl: string;
  prompt?: string;
  index: number;
  onClick: (e: React.MouseEvent) => void;
  viewMode: ViewMode;
  hasReferenceImages?: boolean;
  title?: string;
  isExpandedMain?: boolean;
}

const ImageBatchItemContent: React.FC<ImageBatchItemContentProps> = ({
  imageUrl,
  prompt,
  index,
  onClick,
  viewMode,
  hasReferenceImages = false,
  title,
  isExpandedMain = false
}) => {
  // For small view and normal view (except main image in unroll mode), use 1:1 aspect ratio
  // When in unroll mode for the main image, don't force aspect ratio
  const useSquareAspectRatio = viewMode === 'small' || (viewMode === 'normal' && !isExpandedMain);
  
  // Use 4:3 aspect ratio for the main image in unrolled mode
  const aspectRatio = isExpandedMain ? 4/3 : 1;
  
  return (
    <div className="w-full h-full" onClick={onClick}>
      {imageUrl ? (
        <div className="relative w-full h-full">
          {useSquareAspectRatio ? (
            <AspectRatio ratio={aspectRatio} className="overflow-hidden">
              <img
                src={imageUrl}
                alt={prompt || `Generated image ${index + 1}`}
                title={title || prompt}
                className="w-full h-full object-contain bg-[#f3f3f3]"
              />
              {hasReferenceImages && (
                <ReferenceImageIndicator 
                  imageUrl={imageUrl} 
                  position="bottom-left" 
                />
              )}
            </AspectRatio>
          ) : (
            // For expanded main image, use 4:3 aspect ratio
            <AspectRatio ratio={4/3} className="overflow-hidden">
              <img
                src={imageUrl}
                alt={prompt || `Generated image ${index + 1}`}
                title={title || prompt}
                className="w-full h-full object-contain bg-[#f3f3f3]"
              />
              {hasReferenceImages && (
                <ReferenceImageIndicator 
                  imageUrl={imageUrl} 
                  position="bottom-left" 
                />
              )}
            </AspectRatio>
          )}
        </div>
      ) : (
        <div className="w-full h-full bg-secondary/10 flex items-center justify-center text-muted-foreground text-sm">
          No image
        </div>
      )}
    </div>
  );
};

export default ImageBatchItemContent;
