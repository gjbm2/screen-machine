
import React from 'react';
import MainImageView from '../MainImageView';

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
  return (
    <div className="flex-grow flex items-center justify-center overflow-hidden">
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
