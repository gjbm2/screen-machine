
import React from 'react';
import MainImageView from '../MainImageView';
import NavigationControls from '../NavigationControls';

interface DetailViewImageSectionProps {
  activeImage: {
    url: string;
    prompt?: string;
    workflow: string;
    params?: Record<string, any>;
    referenceImageUrl?: string;
  };
  onImageLoad: (e: React.SyntheticEvent<HTMLImageElement>) => void;
  onOpenInNewTab: (e: React.MouseEvent) => void;
  allImages?: Array<{
    url: string;
    batchId: string;
    batchIndex: number;
    prompt?: string;
  }>;
  isNavigatingAllImages?: boolean;
  onNavigateGlobal?: (imageIndex: number) => void;
  currentGlobalIndex?: number;
  onImageClick?: (e: React.MouseEvent) => void;
}

const DetailViewImageSection: React.FC<DetailViewImageSectionProps> = ({
  activeImage,
  onImageLoad,
  allImages,
  isNavigatingAllImages,
  onNavigateGlobal,
  currentGlobalIndex,
  onImageClick
}) => {
  const showPrev = isNavigatingAllImages && currentGlobalIndex !== undefined && currentGlobalIndex > 0;
  const showNext = isNavigatingAllImages && currentGlobalIndex !== undefined && allImages && currentGlobalIndex < allImages.length - 1;
  
  const handlePrevImage = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (onNavigateGlobal && currentGlobalIndex !== undefined && currentGlobalIndex > 0) {
      onNavigateGlobal(currentGlobalIndex - 1);
    }
  };

  const handleNextImage = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (onNavigateGlobal && currentGlobalIndex !== undefined && allImages && currentGlobalIndex < allImages.length - 1) {
      onNavigateGlobal(currentGlobalIndex + 1);
    }
  };

  return (
    <div className="flex-grow flex items-center justify-center overflow-hidden min-h-0 w-full relative">
      {/* Navigation controls - positioned at panel edges */}
      {(showPrev || showNext) && (
        <NavigationControls
          onPrevious={handlePrevImage}
          onNext={handleNextImage}
          size="medium"
          showPrevButton={showPrev}
          showNextButton={showNext}
        />
      )}
      
      <MainImageView
        imageUrl={activeImage.url}
        altText={activeImage.prompt || "Generated image"}
        onImageLoad={onImageLoad}
        allImages={allImages}
        isNavigatingAllImages={isNavigatingAllImages}
        onNavigateGlobal={onNavigateGlobal}
        currentGlobalIndex={currentGlobalIndex}
        handleTouchStart={() => {}}  // Touch handling moved to DetailViewTouchHandler
        handleTouchEnd={() => {}}    // Touch handling moved to DetailViewTouchHandler
        onImageClick={onImageClick}  // Pass the onImageClick prop
      />
    </div>
  );
};

export default DetailViewImageSection;
