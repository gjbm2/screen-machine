
import React, { useRef, useEffect } from 'react';
import { CardContent } from "@/components/ui/card";
import { CaptionRenderer } from './CaptionRenderer';
import { ShowMode, PositionMode, CaptionPosition } from '../types';
import { ResizeHandle } from '../ResizeHandle';
import { SCREEN_SIZES } from './ScreenSizeSelector';

interface DebugImageContentProps {
  imageUrl: string | null;
  imageKey: number;
  showMode: ShowMode;
  position: PositionMode;
  backgroundColor: string;
  caption?: string | null;
  captionPosition?: CaptionPosition;
  captionSize?: string;
  captionColor?: string;
  captionFont?: string;
  captionBgColor?: string;
  captionBgOpacity?: number;
  selectedSize?: string;
  contentRef: React.RefObject<HTMLDivElement>;
  containerWidth: number;
  onImageError: () => void;
  onImageLoad: (e: React.SyntheticEvent<HTMLImageElement>) => void;
  imageDimensions: { width: number; height: number };
  imageRef: React.RefObject<HTMLImageElement>;
  viewportRatio: string;
  onResizeStart?: (e: React.MouseEvent) => void;
}

export const DebugImageContent: React.FC<DebugImageContentProps> = ({
  imageUrl,
  imageKey,
  showMode,
  position,
  backgroundColor,
  caption,
  captionPosition = 'bottom-center',
  captionSize = '16px',
  captionColor = 'ffffff',
  captionFont = 'Arial, sans-serif',
  captionBgColor = '#000000',
  captionBgOpacity = 0.7,
  selectedSize = 'Current Viewport',
  contentRef,
  containerWidth,
  onImageError,
  onImageLoad,
  imageDimensions,
  imageRef,
  viewportRatio,
  onResizeStart
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  
  // Apply selected screen size to container
  useEffect(() => {
    if (!containerRef.current) return;
    
    const selectedSizeObj = SCREEN_SIZES.find(size => size.name === selectedSize);
    if (!selectedSizeObj) return;
    
    // Calculate the maximum size that fits in the available space
    const parentWidth = contentRef.current?.clientWidth || window.innerWidth;
    const parentHeight = contentRef.current?.clientHeight || window.innerHeight;
    
    // Calculate aspect ratio of the selected size
    const aspectRatio = selectedSizeObj.width / selectedSizeObj.height;
    
    // Calculate maximum dimensions that fit within the parent container
    const maxWidth = Math.min(selectedSizeObj.width, parentWidth);
    const maxHeight = Math.min(selectedSizeObj.height, parentHeight);
    
    // Calculate dimensions based on aspect ratio
    let width, height;
    if (maxWidth / aspectRatio <= maxHeight) {
      // Width is the limiting factor
      width = maxWidth;
      height = width / aspectRatio;
    } else {
      // Height is the limiting factor
      height = maxHeight;
      width = height * aspectRatio;
    }
    
    // Apply dimensions to container
    containerRef.current.style.width = `${width}px`;
    containerRef.current.style.height = `${height}px`;
  }, [selectedSize, contentRef]);
  
  const handleImageLoad = (e: React.SyntheticEvent<HTMLImageElement>) => {
    // Forward the event to parent component
    onImageLoad(e);
  };
  
  return (
    <CardContent 
      className="p-0 flex-1 overflow-hidden flex items-center justify-center"
      ref={contentRef}
    >
      <div 
        ref={containerRef}
        className="relative overflow-hidden bg-gray-900"
        style={{ backgroundColor }}
      >
        {imageUrl ? (
          <>
            <img
              ref={imageRef}
              key={`image-${imageKey}`}
              src={imageUrl}
              alt="Preview"
              className="w-full h-full object-contain"
              onLoad={handleImageLoad}
              onError={onImageError}
              style={{
                objectFit: showMode === 'contain' ? 'contain' : 'cover',
                objectPosition: position === 'center' ? 'center' : position
              }}
            />
            
            {caption && (
              <CaptionRenderer 
                caption={caption}
                position={captionPosition}
                fontSize={captionSize}
                color={captionColor}
                fontFamily={captionFont}
                backgroundColor={captionBgColor}
                backgroundOpacity={captionBgOpacity}
                containerWidth={containerRef.current?.clientWidth || window.innerWidth}
                screenWidth={window.innerWidth}
              />
            )}
          </>
        ) : (
          <div className="w-full h-full flex items-center justify-center text-gray-500">
            No image to display
          </div>
        )}
        
        {onResizeStart && <ResizeHandle onMouseDown={onResizeStart} />}
      </div>
    </CardContent>
  );
};
