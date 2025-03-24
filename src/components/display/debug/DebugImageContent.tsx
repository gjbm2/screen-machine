import React, { useState } from 'react';
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
  
  const formattedCaptionBgColor = captionBgColor.startsWith('#') ? captionBgColor : `#${captionBgColor}`;
  
  const getImageStyleWithContext = () => {
    return getImageStyle(showMode, position, imageDimensions);
  };

  return (
    <CardContent 
      className="p-0 flex-1 overflow-auto flex items-center justify-center"
      ref={contentRef}
    >
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
    </CardContent>
  );
};
