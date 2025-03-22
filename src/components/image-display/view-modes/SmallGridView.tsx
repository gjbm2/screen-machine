
import React, { useState } from 'react';
import GenerationFailedPlaceholder from '../GenerationFailedPlaceholder';
import ImageLoadingState from '../ImageLoadingState';

interface SmallGridViewProps {
  images: any[];
  isLoading: boolean;
  onSmallImageClick: (image: any) => void;
  onCreateAgain: (batchId?: string) => void;
  onDeleteImage: (batchId: string, index: number) => void;
}

const SmallGridView: React.FC<SmallGridViewProps> = ({
  images,
  isLoading,
  onSmallImageClick,
  onCreateAgain,
  onDeleteImage
}) => {
  const [loadedImages, setLoadedImages] = useState<Record<string, boolean>>({});
  
  const handleImageLoad = (imageId: string) => {
    setLoadedImages(prev => ({
      ...prev,
      [imageId]: true
    }));
  };
  
  return (
    <div className="grid grid-cols-4 sm:grid-cols-6 md:grid-cols-8 gap-0.5">
      {images.map((image, idx) => {
        const imageId = `${image.batchId}-${image.batchIndex}`;
        return (
          <div 
            key={imageId} 
            className="aspect-square rounded-md overflow-hidden cursor-pointer"
            onClick={() => onSmallImageClick(image)}
          >
            {image.status === 'completed' ? (
              <div className="relative w-full h-full">
                {!loadedImages[imageId] && (
                  <div className="absolute inset-0 z-10">
                    <ImageLoadingState />
                  </div>
                )}
                <img 
                  src={image.url}
                  alt={image.prompt || `Generated image ${idx + 1}`}
                  className={`w-full h-full object-cover ${!loadedImages[imageId] ? 'opacity-0' : 'opacity-100'}`}
                  style={{ transition: 'opacity 0.2s ease-in-out' }}
                  onLoad={() => handleImageLoad(imageId)}
                />
              </div>
            ) : (
              <GenerationFailedPlaceholder 
                prompt={null} 
                onRetry={() => onCreateAgain(image.batchId)}
                onRemove={() => onDeleteImage(image.batchId || '', image.batchIndex || 0)}
                isCompact={true}
              />
            )}
          </div>
        );
      })}
      {isLoading && (
        <div className="aspect-square rounded-md overflow-hidden bg-muted flex items-center justify-center">
          <ImageLoadingState />
        </div>
      )}
    </div>
  );
};

export default SmallGridView;
