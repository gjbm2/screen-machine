
import React from 'react';
import { CardContent } from "@/components/ui/card";
import { AspectRatio } from "@/components/ui/aspect-ratio";
import { MoveDiagonal } from "lucide-react";
import { ShowMode, PositionMode } from '../types';
import { getPositioningStyles } from './ImagePositionStyles';
import { CaptionRenderer } from './CaptionRenderer';

interface DebugImageContentProps {
  imageUrl: string | null;
  imageKey: number;
  showMode: ShowMode;
  position: PositionMode;
  backgroundColor: string;
  caption?: string | null;
  captionPosition?: string;
  captionSize?: string;
  captionColor?: string;
  captionFont?: string;
  captionBgColor?: string;
  captionBgOpacity?: number;
  contentRef: React.RefObject<HTMLDivElement>;
  containerWidth: number;
  onImageError: () => void;
  onImageLoad: (e: React.SyntheticEvent<HTMLImageElement>) => void;
  imageDimensions: { width: number; height: number };
  imageRef: React.RefObject<HTMLImageElement>;
  viewportRatio: number;
  selectedSize: { name: string; width: number; height: number };
  onResizeStart: (e: React.MouseEvent) => void;
}

export const DebugImageContent: React.FC<DebugImageContentProps> = ({
  imageUrl,
  imageKey,
  showMode,
  position,
  backgroundColor,
  caption,
  captionPosition,
  captionSize,
  captionColor,
  captionFont,
  captionBgColor,
  captionBgOpacity,
  contentRef,
  containerWidth,
  onImageError,
  onImageLoad,
  imageDimensions,
  imageRef,
  viewportRatio,
  selectedSize,
  onResizeStart
}) => {
  return (
    <CardContent className="overflow-hidden p-0 relative">
      <AspectRatio 
        ratio={viewportRatio} 
        className="overflow-hidden"
        ref={contentRef}
      >
        <div 
          className="w-full h-full relative flex items-center justify-center"
          style={{ backgroundColor: `#${backgroundColor}` }}
        >
          {imageUrl ? (
            <>
              <img
                key={imageKey}
                ref={imageRef}
                src={imageUrl}
                alt=""
                style={getPositioningStyles(position, showMode, imageDimensions)}
                onError={onImageError}
                onLoad={onImageLoad}
              />
              
              {caption && (
                <CaptionRenderer
                  caption={caption}
                  captionPosition={captionPosition}
                  captionSize={captionSize}
                  captionColor={captionColor}
                  captionFont={captionFont}
                  captionBgColor={captionBgColor}
                  captionBgOpacity={captionBgOpacity}
                  containerWidth={containerWidth}
                  screenWidth={selectedSize.width}
                />
              )}
            </>
          ) : (
            <div className="text-center p-4 text-gray-500">
              <p>No image selected</p>
              <p className="text-sm mt-2">Select an image from the available files list or enter a custom URL</p>
            </div>
          )}
        </div>
      </AspectRatio>
      
      <div
        className="absolute bottom-0 right-0 w-6 h-6 cursor-se-resize z-20 flex items-center justify-center"
        onMouseDown={onResizeStart}
      >
        <MoveDiagonal className="h-4 w-4 text-gray-400" />
      </div>
      
      <div className="text-xs text-gray-500 mt-2 pl-4 pb-2">
        Preview dimensions: {selectedSize.width}×{selectedSize.height}
      </div>
    </CardContent>
  );
};
