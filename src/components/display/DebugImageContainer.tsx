
import React, { useEffect } from 'react';
import { Card } from "@/components/ui/card";
import { useNavigate } from "react-router-dom";
import { ShowMode, PositionMode, CaptionPosition } from './types';
import { useDebugImageContainer } from './debug/hooks/useDebugImageContainer';
import { DebugImageHeader } from './debug/DebugImageHeader';
import { DebugImageContent } from './debug/DebugImageContent';
import { getContainerStyles } from './debug/utils/DebugContainerStyles';

interface DebugImageContainerProps {
  imageUrl: string | null;
  imageKey: number;
  showMode: ShowMode;
  position: PositionMode;
  backgroundColor: string;
  onImageError: () => void;
  imageRef: React.RefObject<HTMLImageElement>;
  imageChanged?: boolean;
  caption?: string | null;
  captionPosition?: CaptionPosition;
  captionSize?: string;
  captionColor?: string;
  captionFont?: string;
  captionBgColor?: string;
  captionBgOpacity?: number;
  metadata?: Record<string, string>;
  onSettingsChange?: () => void;
  onFocus?: () => void;
  isFixedPanel?: boolean;
  togglePreview?: () => void;
  showingPreview?: boolean;
  isMobile?: boolean;
}

export const DebugImageContainer: React.FC<DebugImageContainerProps> = ({
  imageUrl,
  imageKey,
  showMode,
  position,
  backgroundColor,
  onImageError,
  imageRef,
  imageChanged,
  caption,
  captionPosition = 'bottom-center',
  captionSize = '16px',
  captionColor = 'ffffff',
  captionFont = 'Arial, sans-serif',
  captionBgColor = '#000000',
  captionBgOpacity = 0.7,
  metadata = {},
  onSettingsChange,
  onFocus,
  isFixedPanel = false,
  togglePreview,
  showingPreview,
  isMobile
}) => {
  const navigate = useNavigate();
  
  const {
    imageDimensions,
    containerWidth,
    containerHeight,
    containerPosition,
    isDragging,
    containerRef,
    contentRef,
    containerSize,
    viewportRatio,
    selectedSize,
    handleImageLoad,
    handleMouseDown,
    handleResizeStart,
    handleSizeSelection
  } = useDebugImageContainer();
  
  const handleReset = () => {
    navigate('/display');
  };

  const handlePanelMouseDown = (e: React.MouseEvent<HTMLDivElement>) => {
    if (isFixedPanel) return;
    
    if (onFocus) {
      onFocus();
    }
    handleMouseDown(e);
  };

  // Handle window resize for fixed panel mode
  useEffect(() => {
    if (isFixedPanel && contentRef.current) {
      const handleResize = () => {
        if (contentRef.current) {
          const newWidth = contentRef.current.offsetWidth;
          const newHeight = contentRef.current.offsetHeight;
          if (newWidth !== containerWidth || newHeight !== containerHeight) {
            // Force re-render to update content dimensions
            if (imageRef.current) {
              // Create a proper SyntheticEvent instead of just a plain object
              // @ts-ignore - This is a workaround for the type issue
              handleImageLoad({ currentTarget: imageRef.current, target: imageRef.current } as React.SyntheticEvent<HTMLImageElement>);
            }
          }
        }
      };
      
      window.addEventListener('resize', handleResize);
      return () => window.removeEventListener('resize', handleResize);
    }
  }, [isFixedPanel, contentRef, containerWidth, containerHeight, handleImageLoad, imageRef]);

  // Get container styles - with fixed max height/width for panels
  const { cardStyles, innerStyles } = getContainerStyles({
    isFixedPanel,
    containerPosition,
    containerSize,
    isDragging
  });

  return (
    <Card 
      ref={containerRef}
      className={cardStyles}
      style={innerStyles}
      onMouseDown={handlePanelMouseDown}
    >
      <DebugImageHeader
        showMode={showMode}
        position={position}
        selectedSize={selectedSize}
        setSelectedSize={handleSizeSelection}
        imageChanged={imageChanged}
        onSettingsChange={onSettingsChange}
        onReset={handleReset}
        togglePreview={togglePreview}
        showingPreview={showingPreview}
        isMobile={isMobile}
      />
      
      <DebugImageContent
        imageUrl={imageUrl}
        imageKey={imageKey}
        showMode={showMode}
        position={position}
        backgroundColor={backgroundColor}
        caption={caption}
        captionPosition={captionPosition}
        captionSize={captionSize}
        captionColor={captionColor}
        captionFont={captionFont}
        captionBgColor={captionBgColor}
        captionBgOpacity={captionBgOpacity}
        contentRef={contentRef}
        containerWidth={containerWidth}
        onImageError={onImageError}
        onImageLoad={handleImageLoad}
        imageDimensions={imageDimensions}
        imageRef={imageRef}
        viewportRatio={viewportRatio.toString()}
        selectedSize={selectedSize}
        onResizeStart={isFixedPanel ? undefined : handleResizeStart}
      />
    </Card>
  );
};
