
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
    <CardHeader className="px-3 py-2 flex-row justify-between items-center gap-2 border-b">
      <div className="flex items-center gap-2">
        <div className="text-sm font-medium">
          Preview
          <span className="text-xs text-muted-foreground ml-1">
            {showMode}/{position}
          </span>
        </div>
        
        {isMobile && togglePreview && (
          <Button 
            size="sm" 
            variant="outline" 
            className="h-7 text-xs"
            onClick={togglePreview}
          >
            <Settings className="h-3 w-3 mr-1" />
            {showingPreview ? "Settings" : "Preview"}
          </Button>
        )}
      </div>
      
      <div className="flex items-center gap-2">
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
          className="h-7 w-7"
          onClick={onSettingsChange}
        >
          <Settings2 className="h-4 w-4" />
        </Button>
        
        <Button 
          size="icon" 
          variant="ghost" 
          className={`h-7 w-7 ${imageChanged ? 'text-green-500' : ''}`}
          onClick={onReset}
        >
          <RefreshCw className="h-4 w-4" />
        </Button>
      </div>
    </CardHeader>
  );
};
