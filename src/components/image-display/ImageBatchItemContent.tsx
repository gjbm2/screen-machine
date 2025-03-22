
import React, { useState } from 'react';
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
  const [isLoading, setIsLoading] = useState(true);
  const sizeClasses = viewMode === 'small' 
    ? 'aspect-square w-full h-full' 
    : 'aspect-square w-full';

  const handleLoad = () => {
    setIsLoading(false);
  };

  return (
    <div 
      className={`relative ${sizeClasses} cursor-pointer`}
      onClick={onClick}
    >
      {isLoading && imageUrl && (
        <div className="absolute inset-0 z-10">
          <ImageLoadingState />
        </div>
      )}
      
      {imageUrl ? (
        <img
          src={imageUrl}
          alt={prompt || `Generated image ${index + 1}`}
          className={`w-full h-full object-cover ${isLoading ? 'opacity-0' : 'opacity-100'}`}
          style={{ transition: 'opacity 0.2s ease-in-out' }}
          onLoad={handleLoad}
        />
      ) : (
        <ImageLoadingState />
      )}
    </div>
  );
};

export default ImageBatchItemContent;
