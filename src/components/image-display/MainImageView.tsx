
import React, { useState, useEffect, useRef } from 'react';
import { Button } from '@/components/ui/button';
import NavigationControls from './NavigationControls';
import ZoomableImage from './ZoomableImage';
import { useIsMobile } from '@/hooks/use-mobile';

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
  onImageClick,
}) => {
  const [viewportWidth, setViewportWidth] = useState(window.innerWidth);
  const [viewportHeight, setViewportHeight] = useState(window.innerHeight);
  const [imageDimensions, setImageDimensions] = useState({ width: 0, height: 0 });
  const imageContainerRef = useRef<HTMLDivElement>(null);
  const isMobile = useIsMobile();
  
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

    // Adaptive sizing based on screen size
    const isLargeScreen = viewportHeight > 900;
    const isVeryLargeScreen = viewportHeight > 1200;
    
    // Scale control space down on larger screens
    const controlsSpacePercentage = isVeryLargeScreen ? 0.08 : (isLargeScreen ? 0.1 : 0.15);
    const controlsSpace = Math.min(Math.max(100, viewportHeight * controlsSpacePercentage), 160);
    
    // Allow content to use more of the screen on larger displays
    const widthPercentage = isVeryLargeScreen ? 0.95 : (isLargeScreen ? 0.92 : 0.9);
    const availableWidth = viewportWidth * widthPercentage;
    
    // Allow images to take up more vertical space on larger screens
    const heightPercentage = isVeryLargeScreen ? 0.9 : (isLargeScreen ? 0.85 : 0.75);
    const availableHeight = viewportHeight * heightPercentage - controlsSpace;
    
    const widthRatio = availableWidth / imageDimensions.width;
    const heightRatio = availableHeight / imageDimensions.height;
    
    // Use a more generous ratio on larger screens, but still respect aspect ratio
    const ratio = Math.min(widthRatio, heightRatio, isVeryLargeScreen ? 1.5 : (isLargeScreen ? 1.2 : 1));
    
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
    // Forward click to parent handler if provided
    if (onImageClick) {
      onImageClick(e);
    }
  };

  // Use our ZoomableImage component for the image display
  return (
    <div 
      ref={imageContainerRef}
      className="relative flex justify-center items-center bg-secondary/10 rounded-md overflow-hidden group w-auto min-w-0 h-full select-none cursor-pointer" 
      tabIndex={-1}
      style={{ outline: 'none', margin: '0 auto' }}
      onMouseDown={(e) => e.preventDefault()}
    >
      <div className="relative flex justify-center items-center h-full py-2 w-auto min-w-0">
        <ZoomableImage
          src={imageUrl}
          alt={altText}
          onLoad={handleImageLoadInternal}
          onClick={isMobile ? undefined : handleImageContainerClick}
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
