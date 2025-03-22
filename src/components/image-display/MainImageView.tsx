
import React, { useState, useEffect, useRef } from 'react';
import { Button } from '@/components/ui/button';
import { ExternalLink } from 'lucide-react';
import NavigationControls from './NavigationControls';
import { AspectRatio } from '@/components/ui/aspect-ratio';

interface MainImageViewProps {
  imageUrl: string;
  altText: string;
  onImageLoad: (e: React.SyntheticEvent<HTMLImageElement>) => void;
  onOpenInNewTab: (e: React.MouseEvent) => void;
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
  onOpenInNewTab,
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

  return (
    <div 
      ref={imageContainerRef}
      className="relative flex justify-center items-center bg-secondary/10 rounded-md overflow-hidden group w-full h-full"
      onTouchStart={handleTouchStart}
      onTouchEnd={handleTouchEnd}
      onClick={onImageClick}
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
      
      {/* Open in new tab button - moved to bottom right, will be in line with metadata */}
      <Button 
        variant="outline" 
        size="icon" 
        className="absolute bottom-2 right-2 bg-background/80 backdrop-blur-sm z-30"
        onClick={(e) => {
          e.stopPropagation();
          onOpenInNewTab(e);
        }}
        aria-label="Open image in new tab"
        title="" // Empty title to suppress the tooltip
      >
        <ExternalLink className="h-4 w-4" />
      </Button>
      
      {/* Navigation controls - Always use global navigation in fullscreen */}
      {allImages && allImages.length > 1 && onNavigateGlobal && (
        <NavigationControls 
          onPrevious={(e) => {
            e.stopPropagation();
            if ((currentGlobalIndex as number) > 0) {
              onNavigateGlobal((currentGlobalIndex as number) - 1);
            }
          }}
          onNext={(e) => {
            e.stopPropagation();
            if ((currentGlobalIndex as number) < allImages.length - 1) {
              onNavigateGlobal((currentGlobalIndex as number) + 1);
            }
          }}
          size="large"
          currentGlobalIndex={currentGlobalIndex}
          allImages={allImages}
        />
      )}
    </div>
  );
};

export default MainImageView;
