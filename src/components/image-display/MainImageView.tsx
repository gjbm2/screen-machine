
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

  const calculateOptimalSize = () => {
    if (imageDimensions.width === 0 || imageDimensions.height === 0) {
      return { width: 'auto', height: 'auto', maxWidth: '90%', maxHeight: 'calc(75vh - 120px)' };
    }

    // Calculate space needed for controls - adaptive based on viewport size
    const controlsSpace = Math.min(Math.max(120, viewportHeight * 0.15), 160);
    
    // Reserve space for the bottom panels
    const availableWidth = viewportWidth * 0.9;
    const availableHeight = viewportHeight * 0.75 - controlsSpace;
    
    const widthRatio = availableWidth / imageDimensions.width;
    const heightRatio = availableHeight / imageDimensions.height;
    
    const ratio = Math.min(widthRatio, heightRatio, 1);
    
    return { 
      width: 'auto', 
      height: 'auto', 
      maxWidth: `${Math.min(imageDimensions.width * ratio, availableWidth)}px`, 
      maxHeight: `${Math.min(imageDimensions.height * ratio, availableHeight)}px` 
    };
  };

  const optimalSize = calculateOptimalSize();

  const showPrevButton = currentGlobalIndex !== undefined && currentGlobalIndex > 0 && allImages && allImages.length > 1;
  const showNextButton = currentGlobalIndex !== undefined && allImages && currentGlobalIndex < allImages.length - 1;

  // Handle image click - forward to parent component handler
  const handleImageContainerClick = (e: React.MouseEvent) => {
    // Only handle clicks that are directly on the container or the image
    // Don't trigger for navigation buttons
    if (e.target === imageContainerRef.current || 
        (e.target as HTMLElement).tagName === 'IMG') {
      if (onImageClick) {
        onImageClick(e);
      }
    }
  };

  return (
    <div 
      ref={imageContainerRef}
      className="relative flex justify-center items-center bg-secondary/10 rounded-md overflow-hidden group w-full h-full select-none cursor-pointer" /* Added cursor-pointer to indicate clickability */
      onTouchStart={handleTouchStart}
      onTouchEnd={handleTouchEnd}
      onClick={handleImageContainerClick}
      tabIndex={-1}
      style={{ outline: 'none' }}
      onMouseDown={(e) => e.preventDefault()} // Prevent text selection on mouse down
    >
      <div className="relative flex justify-center items-center w-full h-full py-2">
        <img 
          src={imageUrl}
          alt={altText}
          className="object-contain select-none" /* Added select-none to prevent image selection */
          style={optimalSize}
          onLoad={handleImageLoadInternal}
          draggable={false} /* Prevent dragging the image */
        />
      </div>
      
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
