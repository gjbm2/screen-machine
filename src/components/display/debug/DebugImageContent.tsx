
import React, { useRef, useEffect, useState } from 'react';
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
  const screenContainerRef = useRef<HTMLDivElement>(null);
  const [containerDimensions, setContainerDimensions] = useState({ width: 0, height: 0 });
  
  // Apply selected screen size to container and handle proper scaling
  useEffect(() => {
    if (!screenContainerRef.current || !contentRef.current) return;
    
    const selectedSizeObj = SCREEN_SIZES.find(size => size.name === selectedSize);
    if (!selectedSizeObj) return;
    
    // Get parent dimensions
    const parentWidth = contentRef.current.clientWidth;
    const parentHeight = contentRef.current.clientHeight;
    
    // Calculate aspect ratio of the selected size
    const aspectRatio = selectedSizeObj.width / selectedSizeObj.height;
    
    // Determine max dimensions that fit within the parent
    let maxWidth = parentWidth - 40; // Padding
    let maxHeight = parentHeight - 40; // Padding
    
    // Calculate dimensions based on aspect ratio
    let width, height;
    
    // Calculate which dimension is the limiting factor
    if (maxWidth / aspectRatio <= maxHeight) {
      width = maxWidth;
      height = width / aspectRatio;
    } else {
      height = maxHeight;
      width = height * aspectRatio;
    }
    
    // Set dimensions on the container
    screenContainerRef.current.style.width = `${width}px`;
    screenContainerRef.current.style.height = `${height}px`;
    
    setContainerDimensions({ width, height });
  }, [selectedSize, contentRef, containerWidth]);
  
  // Prepare the image display style based on the current mode
  const getImageStyle = (): React.CSSProperties => {
    let style: React.CSSProperties = {
      maxWidth: '100%',
      maxHeight: '100%',
    };
    
    switch (showMode) {
      case 'fill':
        style = {
          ...style,
          width: '100%',
          height: '100%',
          objectFit: 'cover',
          objectPosition: position,
        };
        break;
      case 'fit':
        style = {
          ...style,
          width: 'auto',
          height: 'auto',
          maxWidth: '100%',
          maxHeight: '100%',
          objectFit: 'contain',
          position: 'absolute',
        };
        break;
      case 'stretch':
        style = {
          ...style,
          width: '100%',
          height: '100%',
          objectFit: 'fill',
        };
        break;
      case 'actual':
        style = {
          ...style,
          width: imageDimensions.width > 0 ? `${imageDimensions.width}px` : 'auto',
          height: imageDimensions.height > 0 ? `${imageDimensions.height}px` : 'auto',
          objectFit: 'none',
        };
        break;
      default:
        style = {
          ...style,
          objectFit: 'contain',
        };
    }
    
    // Apply position to fit and actual modes
    if (showMode === 'fit' || showMode === 'actual') {
      switch (position) {
        case 'top-left':
          style.top = 0;
          style.left = 0;
          break;
        case 'top-center':
          style.top = 0;
          style.left = '50%';
          style.transform = 'translateX(-50%)';
          break;
        case 'top-right':
          style.top = 0;
          style.right = 0;
          break;
        case 'center-left':
          style.top = '50%';
          style.left = 0;
          style.transform = 'translateY(-50%)';
          break;
        case 'center':
          style.top = '50%';
          style.left = '50%';
          style.transform = 'translate(-50%, -50%)';
          break;
        case 'center-right':
          style.top = '50%';
          style.right = 0;
          style.transform = 'translateY(-50%)';
          break;
        case 'bottom-left':
          style.bottom = 0;
          style.left = 0;
          break;
        case 'bottom-center':
          style.bottom = 0;
          style.left = '50%';
          style.transform = 'translateX(-50%)';
          break;
        case 'bottom-right':
          style.bottom = 0;
          style.right = 0;
          break;
        default:
          style.top = '50%';
          style.left = '50%';
          style.transform = 'translate(-50%, -50%)';
      }
    }
    
    return style;
  };
  
  return (
    <CardContent 
      className="p-0 flex-1 overflow-auto flex items-center justify-center"
      ref={contentRef}
    >
      <div 
        ref={screenContainerRef}
        className="relative border border-gray-300 shadow-md flex items-center justify-center overflow-hidden"
        style={{ 
          backgroundColor: `#${backgroundColor}`, 
          transition: "width 0.3s, height 0.3s"
        }}
      >
        {imageUrl ? (
          <>
            <img
              ref={imageRef}
              key={`image-${imageKey}`}
              src={imageUrl}
              alt="Preview"
              className="max-w-full max-h-full"
              onLoad={onImageLoad}
              onError={onImageError}
              style={getImageStyle()}
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
                containerWidth={containerDimensions.width}
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
