
import React, { useState, useEffect, useRef } from 'react';
import { Button } from '@/components/ui/button';
import { ExternalLink } from 'lucide-react';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import NavigationControls from './NavigationControls';

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

  return (
    <div 
      ref={imageContainerRef}
      className="relative flex justify-center items-center bg-secondary/10 rounded-md overflow-hidden group"
      onTouchStart={handleTouchStart}
      onTouchEnd={handleTouchEnd}
      onClick={onImageClick}
      style={{ minHeight: '200px' }} // Minimum height to ensure visibility
    >
      <div className="relative flex justify-center items-center w-full h-full">
        <img 
          src={imageUrl}
          alt={altText}
          className="object-contain max-w-full max-h-[calc(95vh-120px)]"
          style={{ 
            maxWidth: '100%',
            maxHeight: '85vh',
            width: 'auto',
            height: 'auto',
          }}
          onLoad={onImageLoad}
        />
        <Tooltip>
          <TooltipTrigger asChild>
            <Button 
              variant="outline" 
              size="icon" 
              className="absolute top-2 right-2 bg-background/80 backdrop-blur-sm z-30"
              onClick={(e) => {
                e.stopPropagation();
                onOpenInNewTab(e);
              }}
            >
              <ExternalLink className="h-4 w-4" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>Open image in new tab</TooltipContent>
        </Tooltip>
      </div>
      
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
        />
      )}
    </div>
  );
};

export default MainImageView;
