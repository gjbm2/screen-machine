
import React, { useState, useEffect, useRef } from 'react';
import { Button } from '@/components/ui/button';
import NavigationControls from './NavigationControls';

interface MainImageViewProps {
  imageUrl: string;
  altText: string;
  onImageLoad: (e: React.SyntheticEvent<HTMLImageElement>) => void;
  onOpenInNewTab?: (e: React.MouseEvent) => void;
  allImages?: Array<{ url: string; batchId: string; batchIndex: number; prompt?: string; }>;
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
  const [viewportWidth, setViewportWidth] = useState(window.innerWidth);
  const [viewportHeight, setViewportHeight] = useState(window.innerHeight);
  const [imageDimensions, setImageDimensions] = useState({ width: 0, height: 0 });
  const imageContainerRef = useRef<HTMLDivElement>(null);
  
  // Update viewport dimensions on resize
  useEffect(() => {
    const handleResize = () => {
      setViewportWidth(window.innerWidth);
      setViewportHeight(window.innerHeight);
    };
    
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  const handleImageLoadInternal = (e: React.SyntheticEvent<HTMLImageElement>) => {
    const img = e.currentTarget;
    setImageDimensions({
      width: img.naturalWidth,
      height: img.naturalHeight
    });
    onImageLoad(e);
  };

  // Calculate optimal image size
  const calculateOptimalSize = () => {
    if (imageDimensions.width === 0 || imageDimensions.height === 0) {
      return { width: 'auto', height: 'auto', maxWidth: '90%', maxHeight: '70vh' };
    }

    // Available space (accounting for controls and padding)
    const availableWidth = viewportWidth * 0.9; // 90% of viewport width
    const availableHeight = viewportHeight * 0.7; // 70% of viewport height (leaving room for controls)
    
    // Calculate scale factors
    const widthRatio = availableWidth / imageDimensions.width;
    const heightRatio = availableHeight / imageDimensions.height;
    
    // Use the smaller ratio to ensure image fits in both dimensions
    const ratio = Math.min(widthRatio, heightRatio, 1); // Cap at 100% (ratio = 1)
    
    return { 
      width: 'auto', 
      height: 'auto', 
      maxWidth: `${Math.min(imageDimensions.width * ratio, availableWidth)}px`, 
      maxHeight: `${Math.min(imageDimensions.height * ratio, availableHeight)}px` 
    };
  };

  const optimalSize = calculateOptimalSize();

  // Determine if previous/next buttons should be shown
  const showPrevButton = currentGlobalIndex !== undefined && currentGlobalIndex > 0 && allImages && allImages.length > 1;
  const showNextButton = currentGlobalIndex !== undefined && allImages && currentGlobalIndex < allImages.length - 1;

  return (
    <div 
      ref={imageContainerRef}
      className="relative flex justify-center items-center bg-secondary/10 rounded-md overflow-hidden group w-full h-full"
      onTouchStart={handleTouchStart}
      onTouchEnd={handleTouchEnd}
      onClick={onImageClick}
      // Prevent highlighting of hotspots when using arrow keys
      tabIndex={-1}
      style={{ outline: 'none' }}
    >
      <div className="relative flex justify-center items-center w-full h-full py-2">
        <img 
          src={imageUrl}
          alt={altText}
          className="object-contain"
          style={optimalSize}
          onLoad={handleImageLoadInternal}
        />
      </div>
      
      {/* Navigation controls - Only show when there are images to navigate to */}
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
};

export default MainImageView;
