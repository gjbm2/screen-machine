
import React from 'react';
import { CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Settings2, RefreshCw, Settings } from 'lucide-react';
import { ShowMode, PositionMode } from '../types';
import { ScreenSizeSelector, SCREEN_SIZES } from './ScreenSizeSelector';

interface DebugImageHeaderProps {
  showMode: ShowMode;
  position: PositionMode;
  selectedScreenSize: { name: string; width: number; height: number };
  setSelectedScreenSize: (size: { name: string; width: number; height: number }) => void;
  imageChanged?: boolean;
  onSettingsChange?: () => void;
  onReset: () => void;
  togglePreview?: () => void;
  showingPreview?: boolean;
  isMobile?: boolean;
}

export const DebugImageHeader: React.FC<DebugImageHeaderProps> = ({
  showMode,
  position,
  selectedScreenSize,
  setSelectedScreenSize,
  imageChanged,
  onSettingsChange,
  onReset,
  togglePreview,
  showingPreview,
  isMobile
}) => {
  return (
    <CardHeader className="px-2 py-2 flex-row justify-between items-center space-x-2">
      <div className="text-sm font-medium truncate flex-shrink-1 min-w-0 whitespace-nowrap">
        Preview {showMode}/{position}
        {isMobile && togglePreview && (
          <Button 
            size="icon" 
            variant="ghost" 
            className="h-6 w-6 ml-2"
            onClick={togglePreview}
          >
            <Settings className="h-4 w-4" />
          </Button>
        )}
      </div>
      <div className="flex items-center gap-1">
        <ScreenSizeSelector 
          selectedSize={selectedScreenSize.name} 
          onSelect={(sizeName) => {
            const found = SCREEN_SIZES.find(s => s.name === sizeName);
            if (found) {
              setSelectedScreenSize(found);
            }
          }} 
          onSettingsChange={onSettingsChange}
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
