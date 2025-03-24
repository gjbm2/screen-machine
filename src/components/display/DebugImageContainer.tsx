
import React, { useState, useRef, useEffect } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { AspectRatio } from "@/components/ui/aspect-ratio";
import { ShowMode, PositionMode, CaptionPosition } from './types';
import { Alert, AlertDescription } from "@/components/ui/alert";
import { RefreshCw, Move, CornerBottomRight } from "lucide-react";
import { processCaptionWithMetadata } from './utils';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { useNavigate } from "react-router-dom";

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
  onSettingsChange?: () => void;
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
  metadata = {},
  onSettingsChange
}) => {
  const navigate = useNavigate();
  const [selectedScreenSize, setSelectedScreenSize] = useState('Current Viewport');
  const [imageDimensions, setImageDimensions] = useState({ width: 0, height: 0 });
  const [containerWidth, setContainerWidth] = useState(0);
  const [containerHeight, setContainerHeight] = useState(0);
  
  // For draggable functionality
  const [containerPosition, setContainerPosition] = useState({ x: window.innerWidth / 2 - 300, y: 200 });
  const [isDragging, setIsDragging] = useState(false);
  const [dragOffset, setDragOffset] = useState({ x: 0, y: 0 });
  const containerRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  
  // For resizable functionality
  const [containerSize, setContainerSize] = useState({ width: 600, height: 400 });
  const [isResizing, setIsResizing] = useState(false);
  const [resizeStart, setResizeStart] = useState({ x: 0, y: 0, width: 0, height: 0 });
  
  // Get the current screen dimensions based on selection
  const selectedSize = SCREEN_SIZES.find(size => size.name === selectedScreenSize) || SCREEN_SIZES[0];
  const viewportRatio = selectedSize.width / selectedSize.height;
  
  // Handle image load to get dimensions
  const handleImageLoad = (e: React.SyntheticEvent<HTMLImageElement>) => {
    const img = e.currentTarget;
    setImageDimensions({
      width: img.naturalWidth,
      height: img.naturalHeight
    });
  };
  
  // Update container dimensions when content changes
  useEffect(() => {
    if (contentRef.current) {
      const rect = contentRef.current.getBoundingClientRect();
      setContainerWidth(rect.width);
      setContainerHeight(rect.height);
    }
  }, [containerSize, selectedScreenSize]);
  
  // Reset all settings and go back to display page
  const handleReset = () => {
    navigate('/display');
  };

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
    
    if (isResizing) {
      const newWidth = Math.max(300, resizeStart.width + (e.clientX - resizeStart.x));
      const newHeight = Math.max(200, resizeStart.height + (e.clientY - resizeStart.y));
      setContainerSize({ width: newWidth, height: newHeight });
    }
  };

  const handleMouseUp = () => {
    setIsDragging(false);
    setIsResizing(false);
  };
  
  // Handle resize start
  const handleResizeStart = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    
    setIsResizing(true);
    setResizeStart({
      x: e.clientX,
      y: e.clientY,
      width: containerSize.width,
      height: containerSize.height
    });
  };

  // Set up global mouse event listeners
  useEffect(() => {
    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);
    
    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };
  }, [isDragging, dragOffset, isResizing, resizeStart]);
  
  // Calculate positioning styles for images based on position parameter
  const getPositioningStyles = (pos: PositionMode, mode: ShowMode): React.CSSProperties => {
    // Start with basic positioning
    let styles: React.CSSProperties = {
      position: 'absolute',
    };
    
    // Handle different display modes
    if (mode === 'actual' && imageDimensions.width > 0 && imageDimensions.height > 0) {
      // Calculate the actual display size based on screen dimensions
      const screenWidth = selectedSize.width;
      const screenHeight = selectedSize.height;
      
      // Use natural image dimensions directly
      styles.width = `${imageDimensions.width}px`;
      styles.height = `${imageDimensions.height}px`;
      styles.objectFit = 'none';
      
      // Position according to the selected position
      if (pos.includes('top')) {
        styles.top = '0';
      } else if (pos.includes('bottom')) {
        styles.bottom = '0';
      } else {
        // Center vertically
        styles.top = '50%';
        styles.transform = styles.transform ? styles.transform + ' translateY(-50%)' : 'translateY(-50%)';
      }
      
      if (pos.includes('left')) {
        styles.left = '0';
      } else if (pos.includes('right')) {
        styles.right = '0';
      } else {
        // Center horizontally
        styles.left = '50%';
        styles.transform = styles.transform ? styles.transform.replace('translateY', 'translate') : 'translateX(-50%)';
      }
      
      // Handle center case
      if (pos === 'center') {
        styles.transform = 'translate(-50%, -50%)';
      }
      
      return styles;
    }
    
    // Handle other display modes
    switch (mode) {
      case 'fill':
        styles = {
          ...styles,
          width: '100%',
          height: '100%',
          objectFit: 'cover',
        };
        break;
      
      case 'fit':
        styles = {
          ...styles,
          maxWidth: '100%',
          maxHeight: '100%',
          objectFit: 'contain',
        };
        break;
      
      case 'stretch':
        styles = {
          ...styles,
          width: '100%',
          height: '100%',
          objectFit: 'fill',
        };
        break;
      
      default:
        styles = {
          ...styles,
          maxWidth: '100%',
          maxHeight: '100%',
          objectFit: 'contain',
        };
    }
    
    // Apply positioning
    if (pos.includes('top')) {
      styles.top = '0';
    } else if (pos.includes('bottom')) {
      styles.bottom = '0';
    } else {
      // Center vertically
      styles.top = '50%';
      styles.transform = 'translateY(-50%)';
    }
    
    if (pos.includes('left')) {
      styles.left = '0';
    } else if (pos.includes('right')) {
      styles.right = '0';
    } else {
      // Center horizontally
      styles.left = '50%';
      styles.transform = styles.transform ? 'translate(-50%, -50%)' : 'translateX(-50%)';
    }
    
    // Handle center case
    if (pos === 'center') {
      styles.top = '50%';
      styles.left = '50%';
      styles.transform = 'translate(-50%, -50%)';
    }
    
    return styles;
  };
  
  // Calculate caption font size scaling for preview
  const getCaptionScaledFontSize = (baseSize: string) => {
    // Extract numeric portion and unit
    const matches = baseSize.match(/^(\d+(?:\.\d+)?)([a-z%]+)?$/i);
    if (!matches) return baseSize;
    
    const size = parseFloat(matches[1]);
    const unit = matches[2] || 'px';
    
    // Scale based on container width compared to selected screen size
    const screenWidth = selectedSize.width;
    const scaleFactor = containerWidth / screenWidth;
    
    // Apply scaling but limit to reasonable bounds
    const scaledSize = Math.max(8, Math.min(32, size * scaleFactor));
    
    return `${scaledSize}${unit}`;
  };

  // Calculate caption styles with scaling
  const getCaptionStyles = (): React.CSSProperties => {
    const scaledFontSize = getCaptionScaledFontSize(captionSize);
    
    const styles: React.CSSProperties = {
      position: 'absolute',
      padding: '8px 16px',
      backgroundColor: 'rgba(0, 0, 0, 0.7)',
      color: `#${captionColor}`,
      fontSize: scaledFontSize,
      fontFamily: captionFont,
      maxWidth: '80%',
      textAlign: 'center',
      borderRadius: '4px',
      zIndex: 10,
      whiteSpace: caption?.includes('\n') ? 'pre-line' : 'normal',
    };
    
    // Position caption
    if (captionPosition?.includes('top')) {
      styles.top = '10px';
    } else if (captionPosition?.includes('bottom')) {
      styles.bottom = '10px';
    } else {
      styles.top = '50%';
      styles.transform = 'translateY(-50%)';
    }
    
    if (captionPosition?.includes('left')) {
      styles.left = '10px';
    } else if (captionPosition?.includes('right')) {
      styles.right = '10px';
    } else {
      styles.left = '50%';
      styles.transform = captionPosition === 'bottom-center' || captionPosition === 'top-center' ? 
        'translateX(-50%)' : styles.transform || 'none';
      
      // Handle full center
      if (captionPosition === 'center') {
        styles.transform = 'translate(-50%, -50%)';
      }
    }
    
    return styles;
  };

  return (
    <Card 
      ref={containerRef}
      className="absolute z-10 cursor-grab overflow-visible"
      style={{ 
        left: `${containerPosition.x}px`, 
        top: `${containerPosition.y}px`,
        width: `${containerSize.width}px`,
        height: `${containerSize.height}px`,
        cursor: isDragging ? 'grabbing' : 'grab',
        resize: 'both'
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
            <Select 
              value={selectedScreenSize} 
              onValueChange={(val) => {
                setSelectedScreenSize(val);
                if (onSettingsChange) onSettingsChange();
              }}
            >
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
          
          <Button
            variant="outline"
            size="sm"
            onClick={handleReset}
          >
            Reset
          </Button>
          
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
      <CardContent className="overflow-hidden p-0 relative">
        {/* Maintain aspect ratio of selected viewport */}
        <AspectRatio 
          ratio={viewportRatio} 
          className="overflow-hidden"
          ref={contentRef}
        >
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
                  style={getPositioningStyles(position, showMode)}
                  onError={onImageError}
                  onLoad={handleImageLoad}
                />
                
                {caption && (
                  <div style={getCaptionStyles()}>
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
        
        {/* Resize handle */}
        <div
          className="absolute bottom-0 right-0 w-6 h-6 cursor-se-resize z-20 flex items-center justify-center"
          onMouseDown={handleResizeStart}
        >
          <CornerBottomRight className="h-4 w-4 text-gray-400" />
        </div>
        
        <div className="text-xs text-gray-500 mt-2 pl-4 pb-2">
          Preview dimensions: {selectedSize.width}×{selectedSize.height}
        </div>
      </CardContent>
    </Card>
  );
};
