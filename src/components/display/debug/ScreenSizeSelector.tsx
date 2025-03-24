
import React, { useEffect, useState } from 'react';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Label } from "@/components/ui/label";

export const SCREEN_SIZES = [
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

export interface ScreenSizeSelectorProps {
  selectedSize: string;
  onSelect: (sizeName: string) => void;
  onSettingsChange?: () => void;
  containerRef?: React.RefObject<HTMLDivElement>;
}

export const ScreenSizeSelector: React.FC<ScreenSizeSelectorProps> = ({
  selectedSize,
  onSelect,
  onSettingsChange,
  containerRef
}) => {
  // Track current size object to update dynamically
  const [currentSizes, setCurrentSizes] = useState(SCREEN_SIZES);
  
  // Update Current Viewport size when window is resized
  useEffect(() => {
    const handleResize = () => {
      const updatedSizes = [...currentSizes];
      updatedSizes[0] = { 
        name: 'Current Viewport', 
        width: window.innerWidth, 
        height: window.innerHeight 
      };
      setCurrentSizes(updatedSizes);
      
      // If container exists and Current Viewport is selected, apply the new size
      if (containerRef?.current && selectedSize === 'Current Viewport') {
        containerRef.current.style.width = `${updatedSizes[0].width}px`;
        containerRef.current.style.height = `${updatedSizes[0].height}px`;
      }
    };
    
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, [selectedSize, containerRef, currentSizes]);

  // Debug logging to see what's happening
  console.log('[ScreenSizeSelector] Current selection:', selectedSize);
  console.log('[ScreenSizeSelector] Available sizes:', currentSizes.map(s => s.name));

  return (
    <div className="flex items-center space-x-2">
      <Label htmlFor="screen-size" className="text-xs sr-only">Screen Size:</Label>
      <Select 
        value={selectedSize} 
        onValueChange={(val) => {
          console.log('[ScreenSizeSelector] Selected new size:', val);
          onSelect(val);
          if (onSettingsChange) onSettingsChange();
        }}
      >
        <SelectTrigger id="screen-size" className="h-7 text-xs px-2 w-[150px]">
          <SelectValue placeholder="Select screen size" />
        </SelectTrigger>
        <SelectContent className="z-50 bg-popover">
          {currentSizes.map((size) => (
            <SelectItem key={size.name} value={size.name} className="text-xs">
              {size.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
};
