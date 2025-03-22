
import React from 'react';
import MainImageView from '../MainImageView';
import ZoomableImage from '../ZoomableImage';
import { useIsMobile } from '@/hooks/use-mobile';
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
  const isMobile = useIsMobile();
  
  // Use direct ZoomableImage on mobile for better performance
  if (isMobile) {
    const showPrevButton = currentGlobalIndex !== undefined && currentGlobalIndex > 0 && allImages && allImages.length > 1;
    const showNextButton = currentGlobalIndex !== undefined && allImages && currentGlobalIndex < allImages.length - 1;
    
    return (
      <div className="flex-grow flex items-center justify-center overflow-hidden min-h-0 min-w-0 relative">
        <ZoomableImage
          src={activeImage.url}
          alt={activeImage.prompt || "Generated image"}
          onLoad={onImageLoad}
          onClick={onImageClick}
        />
        
        {allImages && allImages.length > 1 && onNavigateGlobal && (
          <NavigationControls 
            onPrevious={(e) => {
              e.stopPropagation();
              if (showPrevButton) {
                onNavigateGlobal((currentGlobalIndex as number) - 1);
              }
            }}
            onNext={(e) => {
              e.stopPropagation();
              if (showNextButton) {
                onNavigateGlobal((currentGlobalIndex as number) + 1);
              }
            }}
            size="large"
            currentGlobalIndex={currentGlobalIndex}
            allImages={allImages}
            showPrevButton={showPrevButton}
            showNextButton={showNextButton}
          />
        )}
      </div>
    );
  }
  
  // Use MainImageView for desktop (which now uses ZoomableImage internally)
  return (
    <div className="flex-grow flex items-center justify-center overflow-hidden min-h-0 min-w-0">
      <MainImageView
        imageUrl={activeImage.url}
        altText={activeImage.prompt || "Generated image"}
        onImageLoad={onImageLoad}
        allImages={allImages}
        isNavigatingAllImages={isNavigatingAllImages}
        onNavigateGlobal={onNavigateGlobal}
        currentGlobalIndex={currentGlobalIndex}
        onImageClick={onImageClick}
      />
    </div>
  );
};

export default DetailViewImageSection;
