
import React, { useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { AspectRatio } from "@/components/ui/aspect-ratio";
import { ShowMode, PositionMode, CaptionPosition } from './types';
import { Alert, AlertDescription } from "@/components/ui/alert";
import { RefreshCw, Move } from "lucide-react";
import { processCaptionWithMetadata } from './utils';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Label } from "@/components/ui/label";

// Define standard screen sizes
const SCREEN_SIZES = [
  { name: 'Current Viewport', width: window.innerWidth, height: window.innerHeight },
  { name: 'HD (1280x720)', width: 1280, height: 720 },
  { name: 'Full HD (1920x1080)', width: 1920, height: 1080 },
  { name: '4K UHD (3840x2160)', width: 3840, height: 2160 },
  { name: 'iPad (768x1024)', width: 768, height: 1024 },
  { name: 'iPhone (375x667)', width: 375, height: 667 },
];

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
  metadata?: Record<string, string>;
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
  metadata = {}
}) => {
  const [selectedScreenSize, setSelectedScreenSize] = useState('Current Viewport');
  const [isDragging, setIsDragging] = useState(false);
  
  // Get the current screen dimensions based on selection
  const selectedSize = SCREEN_SIZES.find(size => size.name === selectedScreenSize) || SCREEN_SIZES[0];
  const viewportRatio = selectedSize.width / selectedSize.height;
  
  return (
    <Card className="w-2/3 max-w-3xl mx-auto cursor-move" draggable="true">
      <CardHeader className="pb-2 flex flex-row justify-between items-center">
        <div className="flex items-center">
          <Move className="h-4 w-4 text-muted-foreground mr-2" />
          <CardTitle className="text-lg">Image Preview ({showMode} mode, {position} position)</CardTitle>
        </div>
        
        <div className="flex items-center space-x-4">
          <div className="flex items-center space-x-2">
            <Label htmlFor="screen-size" className="text-sm">Screen Size:</Label>
            <Select value={selectedScreenSize} onValueChange={setSelectedScreenSize}>
              <SelectTrigger id="screen-size" className="w-[180px]">
                <SelectValue placeholder="Select screen size" />
              </SelectTrigger>
              <SelectContent>
                {SCREEN_SIZES.map((size) => (
                  <SelectItem key={size.name} value={size.name}>
                    {size.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          
          {imageChanged && (
            <Alert variant="default" className="py-2 border-amber-500 bg-amber-50">
              <RefreshCw className="h-4 w-4 text-amber-500 mr-2 animate-spin" />
              <AlertDescription className="text-amber-600">
                Image has been updated on the server
              </AlertDescription>
            </Alert>
          )}
        </div>
      </CardHeader>
      <CardContent>
        {/* Maintain aspect ratio of selected viewport */}
        <AspectRatio ratio={viewportRatio} className="overflow-hidden">
          <div 
            className="w-full h-full relative flex items-center justify-center"
            style={{ backgroundColor: `#${backgroundColor}` }}
          >
            {imageUrl && (
              <>
                <img
                  key={imageKey}
                  ref={imageRef}
                  src={imageUrl}
                  alt=""
                  style={{
                    ...(showMode === 'fill' ? {
                      width: '100%',
                      height: '100%',
                      objectFit: 'cover',
                    } : showMode === 'fit' ? {
                      maxWidth: '100%',
                      maxHeight: '100%',
                      objectFit: 'contain',
                    } : showMode === 'stretch' ? {
                      width: '100%',
                      height: '100%',
                      objectFit: 'fill',
                    } : {
                      width: 'auto',
                      height: 'auto',
                      objectFit: 'none',
                    }),
                    // Fixed position logic to handle all position types including 'center'
                    position: 'absolute',
                    ...(position.includes('top') ? { top: 0 } : 
                       position.includes('bottom') ? { bottom: 0 } : 
                       { top: '50%', transform: 'translateY(-50%)' }),
                    ...(position.includes('left') ? { left: 0 } : 
                       position.includes('right') ? { right: 0 } : 
                       { left: '50%', transform: position === 'center' ? 
                         'translate(-50%, -50%)' : position.includes('center-') ? 
                         'translateY(-50%)' : 'translateX(-50%)' }),
                  }}
                  onError={onImageError}
                />
                
                {caption && (
                  <div style={{
                    position: 'absolute',
                    padding: '8px 16px',
                    backgroundColor: 'rgba(0, 0, 0, 0.7)',
                    color: `#${captionColor}`,
                    fontSize: captionSize,
                    fontFamily: captionFont,
                    maxWidth: '80%',
                    textAlign: 'center',
                    borderRadius: '4px',
                    zIndex: 10,
                    whiteSpace: caption.includes('\n') ? 'pre-line' : 'normal',
                    ...(captionPosition?.includes('top') ? { top: '10px' } : 
                       captionPosition?.includes('bottom') ? { bottom: '10px' } : 
                       { top: '50%', transform: 'translateY(-50%)' }),
                    ...(captionPosition?.includes('left') ? { left: '10px' } : 
                       captionPosition?.includes('right') ? { right: '10px' } : 
                       { left: '50%', transform: captionPosition === 'bottom-center' || captionPosition === 'top-center' ? 
                         'translateX(-50%)' : 'none' }),
                  }}>
                    {caption}
                  </div>
                )}
              </>
            )}
            {!imageUrl && (
              <div className="text-center p-4 text-gray-500">
                <p>No image selected</p>
                <p className="text-sm mt-2">Select an image from the available files list or enter a custom URL</p>
              </div>
            )}
          </div>
        </AspectRatio>
        <div className="text-xs text-gray-500 mt-2">
          Preview dimensions: {selectedSize.width}×{selectedSize.height}
        </div>
      </CardContent>
    </Card>
  );
};
