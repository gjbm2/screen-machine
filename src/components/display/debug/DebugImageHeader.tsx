
import React from 'react';
import { CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Settings2, RefreshCw } from 'lucide-react';
import { ShowMode, PositionMode } from '../types';
import { ScreenSizeSelector } from './ScreenSizeSelector';

interface DebugImageHeaderProps {
  showMode: ShowMode;
  position: PositionMode;
  selectedScreenSize: { name: string; width: number; height: number };
  setSelectedScreenSize: (size: { name: string; width: number; height: number }) => void;
  imageChanged?: boolean;
  onSettingsChange?: () => void;
  onReset: () => void;
}

export const DebugImageHeader: React.FC<DebugImageHeaderProps> = ({
  showMode,
  position,
  selectedScreenSize,
  setSelectedScreenSize,
  imageChanged,
  onSettingsChange,
  onReset
}) => {
  return (
    <CardHeader className="px-2 py-2 flex-row justify-between items-center space-x-2 card-header-drag-handle cursor-grab">
      <div className="text-sm font-medium truncate flex-shrink-1 min-w-0 whitespace-nowrap">
        Preview {showMode}/{position}
      </div>
      <div className="flex items-center gap-1">
        <ScreenSizeSelector 
          selectedSize={selectedScreenSize.name} 
          onSelect={(sizeName) => {
            const sizeOptions = [
              { name: 'Mobile', width: 375, height: 667 },
              { name: 'Tablet', width: 768, height: 1024 },
              { name: 'Desktop', width: 1280, height: 720 },
              { name: 'Large Desktop', width: 1920, height: 1080 }
            ];
            const newSize = sizeOptions.find(s => s.name === sizeName) || sizeOptions[0];
            setSelectedScreenSize(newSize);
          }} 
        />
        <Button 
          size="icon" 
          variant="ghost" 
          className="h-6 w-6"
          onClick={onSettingsChange}
        >
          <Settings2 className="h-4 w-4" />
        </Button>
        <Button 
          size="icon" 
          variant="ghost" 
          className={`h-6 w-6 ${imageChanged ? 'text-blue-500' : ''}`}
          onClick={onReset}
        >
          <RefreshCw className="h-4 w-4" />
        </Button>
      </div>
    </CardHeader>
  );
};
