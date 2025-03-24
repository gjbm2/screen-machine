
import React, { useEffect, useRef } from 'react';
import { Card } from "@/components/ui/card";
import { useNavigate } from "react-router-dom";
import { ShowMode, PositionMode, CaptionPosition } from './types';
import { useDebugImageContainer } from './debug/useDebugImageContainer';
import { DebugImageHeader } from './debug/DebugImageHeader';
import { DebugImageContent } from './debug/DebugImageContent';

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
  onFocus
}) => {
  const navigate = useNavigate();
  
  const {
    selectedScreenSize,
    setSelectedScreenSizeObject,
    imageDimensions,
    containerWidth,
    containerHeight,
    containerPosition,
    isDragging,
    containerRef,
    contentRef,
    containerSize,
    selectedSize,
    viewportRatio,
    handleImageLoad,
    handleMouseDown,
    handleResizeStart
  } = useDebugImageContainer();
  
  const handleReset = () => {
    navigate('/display');
  };

  // Add focus handling to bring this panel to the top
  const handlePanelMouseDown = (e: React.MouseEvent<HTMLDivElement>) => {
    // Call the parent's focus handler to raise z-index
    if (onFocus) {
      onFocus();
    }
    // Call the original mouse down handler
    handleMouseDown(e);
  };

  return (
    <Card 
      ref={containerRef}
      className="absolute z-10 cursor-grab overflow-visible resizable-container"
      style={{ 
        left: `${containerPosition.x}px`, 
        top: `${containerPosition.y}px`,
        width: `${containerSize.width}px`,
        height: `${containerSize.height}px`,
        cursor: isDragging ? 'grabbing' : 'grab',
        resize: 'none'
      }}
      onMouseDown={handlePanelMouseDown}
    >
      <DebugImageHeader
        showMode={showMode}
        position={position}
        selectedScreenSize={selectedSize}
        setSelectedScreenSize={setSelectedScreenSizeObject}
        imageChanged={imageChanged}
        onSettingsChange={onSettingsChange}
        onReset={handleReset}
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
        viewportRatio={viewportRatio}
        selectedSize={selectedSize}
        onResizeStart={handleResizeStart}
      />
    </Card>
  );
};
