
import React from 'react';
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

interface ScreenSizeSelectorProps {
  selectedScreenSize: string;
  setSelectedScreenSize: (value: string) => void;
  onSettingsChange?: () => void;
}

export const ScreenSizeSelector: React.FC<ScreenSizeSelectorProps> = ({
  selectedScreenSize,
  setSelectedScreenSize,
  onSettingsChange
}) => {
  return (
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
  );
};
