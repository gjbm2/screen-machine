
import React, { useState } from 'react';
import ImageLoadingState from './ImageLoadingState';
import NavigationControls from './NavigationControls';

interface MainImageViewProps {
  imageUrl: string;
  altText: string;
  onImageLoad?: (e: React.SyntheticEvent<HTMLImageElement>) => void;
  allImages?: Array<{
    url: string;
    batchId: string;
    batchIndex: number;
    prompt?: string;
  }>;
  isNavigatingAllImages?: boolean;
  onNavigateGlobal?: (imageIndex: number) => void;
  currentGlobalIndex?: number;
  handleTouchStart?: (e: React.TouchEvent) => void;
  handleTouchEnd?: (e: React.TouchEvent) => void;
  onImageClick?: (e: React.MouseEvent) => void;
}

const MainImageView: React.FC<MainImageViewProps> = ({
  imageUrl,
  altText,
  onImageLoad,
  allImages,
  isNavigatingAllImages,
  onNavigateGlobal,
  currentGlobalIndex,
  handleTouchStart,
  handleTouchEnd,
  onImageClick,
}) => {
  const [isLoading, setIsLoading] = useState(true);

  const handleImageLoaded = (e: React.SyntheticEvent<HTMLImageElement>) => {
    setIsLoading(false);
    if (onImageLoad) {
      onImageLoad(e);
    }
  };

  // Navigation handlers
  const handleNavigatePrev = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (isNavigatingAllImages && onNavigateGlobal && currentGlobalIndex !== undefined && currentGlobalIndex > 0) {
      onNavigateGlobal(currentGlobalIndex - 1);
    }
  };

  const handleNavigateNext = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (isNavigatingAllImages && onNavigateGlobal && currentGlobalIndex !== undefined && allImages && currentGlobalIndex < allImages.length - 1) {
      onNavigateGlobal(currentGlobalIndex + 1);
    }
  };

  return (
    <div 
      className="w-auto h-full flex items-center justify-center relative"
      style={{ margin: '0 auto' }}
      onTouchStart={handleTouchStart}
      onTouchEnd={handleTouchEnd}
      onClick={onImageClick}
    >
      <div className="relative w-auto h-full flex items-center justify-center">
        {isLoading && (
          <div className="absolute inset-0 z-10 flex items-center justify-center">
            <ImageLoadingState />
          </div>
        )}
        
        <img
          src={imageUrl}
          alt={altText}
          className={`max-h-full object-contain ${isLoading ? 'opacity-0' : 'opacity-100'}`}
          style={{ 
            transition: 'opacity 0.2s ease-in-out',
            maxWidth: '100%',
          }}
          onLoad={handleImageLoaded}
        />
      </div>

      {/* Navigation Controls */}
      {isNavigatingAllImages && onNavigateGlobal && currentGlobalIndex !== undefined && allImages && (
        <NavigationControls
          onPrevious={handleNavigatePrev}
          onNext={handleNavigateNext}
          size="medium"
          showPrevButton={currentGlobalIndex > 0}
          showNextButton={currentGlobalIndex < allImages.length - 1}
        />
      )}
    </div>
  );
};

export default MainImageView;
