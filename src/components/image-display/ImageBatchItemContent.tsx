
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
}

const ImageBatchItemContent: React.FC<ImageBatchItemContentProps> = ({
  imageUrl,
  prompt,
  index,
  onClick,
  viewMode,
  hasReferenceImages = false,
  title
}) => {
  // When in normal view, the container will be 4:3 (set by parent component)
  // For small view, keep using square aspect ratio
  const aspectRatio = viewMode === 'small' ? 1 : undefined;

  return (
    <div className="w-full h-full" onClick={onClick}>
      {imageUrl ? (
        <div className="relative w-full h-full">
          <img
            src={imageUrl}
            alt={prompt || `Generated image ${index + 1}`}
            title={title || prompt}
            className="w-full h-full object-contain bg-black"
          />
          {hasReferenceImages && (
            <ReferenceImageIndicator 
              imageUrl={imageUrl} 
              position={viewMode === 'small' ? 'top-right' : 'bottom-left'} 
            />
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
