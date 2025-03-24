
import React, { useState, useRef } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { AspectRatio } from "@/components/ui/aspect-ratio";
import { ShowMode, PositionMode, CaptionPosition } from './types';
import { Alert, AlertDescription } from "@/components/ui/alert";
import { RefreshCw, Move } from "lucide-react";
import { processCaptionWithMetadata } from './utils';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Label } from "@/components/ui/label";

// Define standard screen sizes with both landscape and portrait options
const SCREEN_SIZES = [
  { name: 'Current Viewport', width: window.innerWidth, height: window.innerHeight },
  { name: 'HD (1280x720)', width: 1280, height: 720 },
  { name: 'HD Portrait (720x1280)', width: 720, height: 1280 },
  { name: 'Full HD (1920x1080)', width: 1920, height: 1080 },
  { name: 'Full HD Portrait (1080x1920)', width: 1080, height: 1920 },
  { name: '4K UHD (3840x2160)', width: 3840, height: 2160 },
  { name: '4K UHD Portrait (2160x3840)', width: 2160, height: 3840 },
  { name: 'iPad (768x1024)', width: 768, height: 1024 },
  { name: 'iPad Landscape (1024x768)', width: 1024, height: 768 },
  { name: 'iPhone (375x667)', width: 375, height: 667 },
  { name: 'iPhone Landscape (667x375)', width: 667, height: 375 },
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
  
  // For draggable functionality
  const [containerPosition, setContainerPosition] = useState({ x: window.innerWidth / 2 - 300, y: 200 });
  const [isDragging, setIsDragging] = useState(false);
  const [dragOffset, setDragOffset] = useState({ x: 0, y: 0 });
  const containerRef = useRef<HTMLDivElement>(null);
  
  // Get the current screen dimensions based on selection
  const selectedSize = SCREEN_SIZES.find(size => size.name === selectedScreenSize) || SCREEN_SIZES[0];
  const viewportRatio = selectedSize.width / selectedSize.height;
  
  // Draggable handlers
  const handleMouseDown = (e: React.MouseEvent<HTMLDivElement>) => {
    if (e.target instanceof Element && e.target.closest('.card-header-drag-handle')) {
      setIsDragging(true);
      const rect = containerRef.current?.getBoundingClientRect();
      if (rect) {
        setDragOffset({
          x: e.clientX - rect.left,
          y: e.clientY - rect.top
        });
      }
    }
  };

  const handleMouseMove = (e: MouseEvent) => {
    if (isDragging) {
      const newX = e.clientX - dragOffset.x;
      const newY = e.clientY - dragOffset.y;
      setContainerPosition({ x: newX, y: newY });
    }
  };

  const handleMouseUp = () => {
    setIsDragging(false);
  };

  // Set up global mouse event listeners
  React.useEffect(() => {
    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);
    
    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };
  }, [isDragging, dragOffset]);
  
  // Calculate positioning styles for images based on position parameter
  const getPositioningStyles = (pos: PositionMode): React.CSSProperties => {
    // Base positioning styles for all modes
    const styles: React.CSSProperties = {
      position: 'absolute',
    };
    
    // Apply vertical positioning
    if (pos.includes('top')) {
      styles.top = 0;
    } else if (pos.includes('bottom')) {
      styles.bottom = 0;
    } else {
      // Center vertically
      styles.top = '50%';
      styles.transform = 'translateY(-50%)';
    }
    
    // Apply horizontal positioning
    if (pos.includes('left')) {
      styles.left = 0;
    } else if (pos.includes('right')) {
      styles.right = 0;
    } else {
      // Center horizontally
      styles.left = '50%';
      styles.transform = pos === 'center' ? 
        'translate(-50%, -50%)' : 
        pos.includes('top') || pos.includes('bottom') ? 
          'translateX(-50%)' : 
          `${styles.transform} translateX(-50%)`;
    }
    
    return styles;
  };

  return (
    <Card 
      ref={containerRef}
      className="w-2/3 max-w-3xl absolute z-10"
      style={{ 
        left: `${containerPosition.x}px`, 
        top: `${containerPosition.y}px`,
        cursor: isDragging ? 'grabbing' : 'auto'
      }}
      onMouseDown={handleMouseDown}
    >
      <CardHeader className="pb-2 flex flex-row justify-between items-center card-header-drag-handle cursor-grab">
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
                      ...getPositioningStyles(position),
                    } : showMode === 'fit' ? {
                      maxWidth: '100%',
                      maxHeight: '100%',
                      objectFit: 'contain',
                      ...getPositioningStyles(position),
                    } : showMode === 'stretch' ? {
                      width: '100%',
                      height: '100%',
                      objectFit: 'fill',
                      ...getPositioningStyles(position),
                    } : {
                      width: 'auto',
                      height: 'auto',
                      objectFit: 'none',
                      ...getPositioningStyles(position),
                    })
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
