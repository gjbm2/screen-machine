
import React, { useState, useEffect } from 'react';
import { CardContent } from "@/components/ui/card";
import { CaptionRenderer } from './CaptionRenderer';
import { ShowMode, PositionMode, CaptionPosition } from '../types';
import { ResizeHandle } from '../ResizeHandle';
import { ScreenContainer } from './components/ScreenContainer';
import { ImageDisplay } from './components/ImageDisplay';
import { getImageStyle } from './utils/ImagePositionStyles';
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
  const [containerDimensions, setContainerDimensions] = useState({ width: 0, height: 0 });
  const [containerVisible, setContainerVisible] = useState(false);
  
  const formattedCaptionBgColor = captionBgColor.startsWith('#') ? captionBgColor : `#${captionBgColor}`;
  
  const getImageStyleWithContext = () => {
    return getImageStyle(showMode, position, imageDimensions);
  };

  // Log to debug image loading issues
  useEffect(() => {
    console.log('[DebugImageContent] Current imageUrl:', imageUrl);
    console.log('[DebugImageContent] Current imageKey:', imageKey);
    console.log('[DebugImageContent] Selected size:', selectedSize);
    
    // Add a slight delay to ensure container dimensions are properly calculated
    const timer = setTimeout(() => {
      setContainerVisible(true);
    }, 50);
    
    return () => clearTimeout(timer);
  }, [imageUrl, imageKey, selectedSize]);

  // Reset container visibility when size changes to prevent janky transitions
  useEffect(() => {
    setContainerVisible(false);
    const timer = setTimeout(() => {
      setContainerVisible(true);
    }, 50);
    
    return () => clearTimeout(timer);
  }, [selectedSize]);

  // Track content ref dimensions for responsive adjustments
  useEffect(() => {
    if (contentRef.current) {
      const updateDimensions = () => {
        const rect = contentRef.current?.getBoundingClientRect();
        if (rect) {
          console.log('[DebugImageContent] Content area dimensions:', {
            width: rect.width,
            height: rect.height
          });
        }
      };
      
      // Call once and add listener
      updateDimensions();
      window.addEventListener('resize', updateDimensions);
      
      return () => window.removeEventListener('resize', updateDimensions);
    }
  }, [contentRef]);

  return (
    <CardContent 
      className="p-0 flex-1 overflow-auto flex items-center justify-center relative"
      ref={contentRef}
    >
      <div className="absolute inset-0 flex items-center justify-center overflow-auto p-4 box-border">
        <div className={`transition-opacity duration-200 ${containerVisible ? 'opacity-100' : 'opacity-0'}`}>
          <ScreenContainer
            selectedSize={selectedSize}
            contentRef={contentRef}
            containerWidth={containerWidth}
            backgroundColor={backgroundColor}
            onDimensionsChange={setContainerDimensions}
          >
            <ImageDisplay
              imageUrl={imageUrl}
              imageKey={imageKey}
              showMode={showMode}
              position={position}
              backgroundColor={backgroundColor}
              onImageError={onImageError}
              onImageLoad={onImageLoad}
              imageDimensions={imageDimensions}
              imageRef={imageRef}
              getImageStyle={getImageStyleWithContext}
            />
            
            {caption && (
              <CaptionRenderer 
                caption={caption}
                position={captionPosition}
                fontSize={captionSize}
                color={captionColor}
                fontFamily={captionFont}
                backgroundColor={formattedCaptionBgColor}
                backgroundOpacity={captionBgOpacity}
                containerWidth={containerDimensions.width}
                screenWidth={window.innerWidth}
                screenSize={
                  selectedSize !== 'Current Viewport' 
                    ? SCREEN_SIZES.find(size => size.name === selectedSize) 
                    : undefined
                }
              />
            )}
            
            {onResizeStart && <ResizeHandle onMouseDown={onResizeStart} />}
          </ScreenContainer>
        </div>
      </div>
    </CardContent>
  );
};
