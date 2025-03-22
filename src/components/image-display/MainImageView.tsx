
import React, { useState, useEffect, useRef } from 'react';
import { Button } from '@/components/ui/button';
import NavigationControls from './NavigationControls';
import { useWindowSize } from '@/hooks/use-mobile';

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
  const { width: viewportWidth, height: viewportHeight } = useWindowSize();
  const [imageDimensions, setImageDimensions] = useState({ width: 0, height: 0 });
  const [containerSize, setContainerSize] = useState({ width: 0, height: 0 });
  const [calculatedImageSize, setCalculatedImageSize] = useState({ width: 0, height: 0 });
  const imageContainerRef = useRef<HTMLDivElement>(null);
  
  // Use ResizeObserver to track container size changes
  useEffect(() => {
    if (!imageContainerRef.current) return;
    
    const container = imageContainerRef.current;
    const resizeObserver = new ResizeObserver(entries => {
      for (const entry of entries) {
        setContainerSize({
          width: entry.contentRect.width,
          height: entry.contentRect.height
        });
      }
    });
    
    resizeObserver.observe(container);
    
    // Initial size measurement
    setContainerSize({
      width: container.clientWidth,
      height: container.clientHeight
    });
    
    return () => {
      resizeObserver.disconnect();
    };
  }, []);
  
  const handleImageLoadInternal = (e: React.SyntheticEvent<HTMLImageElement>) => {
    const img = e.currentTarget;
    setImageDimensions({
      width: img.naturalWidth,
      height: img.naturalHeight
    });
    onImageLoad(e);
  };

  // Calculate the optimal image size whenever relevant dependencies change
  useEffect(() => {
    if (imageDimensions.width === 0 || imageDimensions.height === 0 || !viewportWidth || !viewportHeight) return;
    
    // Adaptive sizing based on screen size
    const isLargeScreen = viewportHeight > 900;
    const isVeryLargeScreen = viewportHeight > 1200;
    
    // Scale control space down on larger screens
    const controlsSpacePercentage = isVeryLargeScreen ? 0.08 : (isLargeScreen ? 0.1 : 0.15);
    const controlsSpace = Math.min(Math.max(100, viewportHeight * controlsSpacePercentage), 160);
    
    // Keep vertical behavior the same as before
    const heightPercentage = isVeryLargeScreen ? 0.9 : (isLargeScreen ? 0.85 : 0.75);
    const availableHeight = viewportHeight * heightPercentage - controlsSpace;
    
    // Use the actual container width from ResizeObserver for more accurate horizontal sizing
    const actualContainerWidth = containerSize.width || 
                                (imageContainerRef.current?.clientWidth || viewportWidth);
    
    // Account for container padding/margins
    const containerPadding = 24;
    const availableWidth = Math.max(actualContainerWidth - containerPadding, 100);
    
    // Calculate scaling ratios
    const widthRatio = availableWidth / imageDimensions.width;
    const heightRatio = availableHeight / imageDimensions.height;
    
    // Use the smaller ratio to maintain aspect ratio
    const ratio = Math.min(widthRatio, heightRatio);
    
    const calculatedWidth = Math.floor(imageDimensions.width * ratio);
    const calculatedHeight = Math.floor(imageDimensions.height * ratio);
    
    setCalculatedImageSize({
      width: calculatedWidth,
      height: calculatedHeight
    });
  }, [imageDimensions, containerSize, viewportWidth, viewportHeight]);

  const optimalSize = {
    width: calculatedImageSize.width ? `${calculatedImageSize.width}px` : 'auto',
    height: calculatedImageSize.height ? `${calculatedImageSize.height}px` : 'auto',
    maxWidth: '100%'
  };

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
      className="relative flex justify-center items-center bg-secondary/10 rounded-md overflow-hidden group w-full h-full select-none cursor-pointer" 
      onTouchStart={handleTouchStart}
      onTouchEnd={handleTouchEnd}
      onClick={handleImageContainerClick}
      tabIndex={-1}
      style={{ outline: 'none' }}
      onMouseDown={(e) => e.preventDefault()} 
    >
      <div className="relative flex justify-center items-center py-2" 
           style={{
             width: 'fit-content', // Allow container to shrink to fit content
             margin: '0 auto'      // Center horizontally
           }}>
        <img 
          src={imageUrl}
          alt={altText}
          className="object-contain select-none" 
          style={optimalSize}
          onLoad={handleImageLoadInternal}
          draggable={false} 
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
