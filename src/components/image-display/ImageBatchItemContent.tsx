
import React from 'react';
import { ViewMode } from './ImageDisplay';
import ImageLoadingState from './ImageLoadingState';

interface ImageBatchItemContentProps {
  imageUrl?: string;
  prompt?: string;
  index: number;
  onClick: (e: React.MouseEvent) => void;
  viewMode: ViewMode;
}

const ImageBatchItemContent: React.FC<ImageBatchItemContentProps> = ({
  imageUrl,
  prompt,
  index,
  onClick,
  viewMode
}) => {
  const sizeClasses = viewMode === 'small' 
    ? 'aspect-square w-full h-full' 
    : 'aspect-square w-full';

  return (
    <div 
      className={`relative ${sizeClasses} cursor-pointer`}
      onClick={onClick}
    >
      {imageUrl ? (
        <img
          src={imageUrl}
          alt={prompt || `Generated image ${index + 1}`}
          className="w-full h-full object-cover"
        />
      ) : (
        <ImageLoadingState />
      )}
    </div>
  );
};

export default ImageBatchItemContent;
