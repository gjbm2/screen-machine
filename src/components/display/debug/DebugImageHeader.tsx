
import React from 'react';
import { CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Settings2, RefreshCw, Smartphone, Desktop, Tablet, Settings, RotateCcw } from 'lucide-react';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { ShowMode, PositionMode } from '../types';
import { ScreenSizeSelector, SCREEN_SIZES } from './ScreenSizeSelector';

interface DebugImageHeaderProps {
  showMode: ShowMode;
  position: PositionMode;
  selectedSize: { name: string; width: number; height: number };
  setSelectedSize: (size: { name: string; width: number; height: number } | string) => void;
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
  selectedSize,
  setSelectedSize,
  imageChanged,
  onSettingsChange,
  onReset,
  togglePreview,
  showingPreview,
  isMobile
}) => {
  // Helper function to select popular screen sizes
  const selectScreenSize = (size: string) => {
    const foundSize = SCREEN_SIZES.find(s => s.name === size);
    if (foundSize) {
      setSelectedSize(foundSize);
    }
  };

  return (
    <CardHeader className="px-3 py-2 flex-row justify-between items-center gap-2 border-b sticky top-0 bg-card z-30">
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
            {showingPreview ? <Settings className="h-3 w-3 mr-1" /> : <RefreshCw className="h-3 w-3 mr-1" />}
            {showingPreview ? "Settings" : "Preview"}
          </Button>
        )}
      </div>
      
      <div className="flex items-center gap-1.5">
        {/* Quick device presets */}
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button 
                size="icon" 
                variant="ghost" 
                className="h-7 w-7"
                onClick={() => selectScreenSize('Mobile')}
              >
                <Smartphone className="h-3.5 w-3.5" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="bottom">
              <p>Mobile (390×844)</p>
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
        
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button 
                size="icon" 
                variant="ghost" 
                className="h-7 w-7"
                onClick={() => selectScreenSize('Tablet')}
              >
                <Tablet className="h-3.5 w-3.5" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="bottom">
              <p>Tablet (768×1024)</p>
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
        
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button 
                size="icon" 
                variant="ghost" 
                className="h-7 w-7"
                onClick={() => selectScreenSize('Desktop')}
              >
                <Desktop className="h-3.5 w-3.5" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="bottom">
              <p>Desktop (1920×1080)</p>
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
        
        <ScreenSizeSelector 
          selectedSize={selectedSize.name} 
          onSelect={(sizeName) => {
            setSelectedSize(sizeName);
          }} 
          onSettingsChange={onSettingsChange}
        />
        
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button 
                size="icon" 
                variant="ghost" 
                className="h-7 w-7"
                onClick={onSettingsChange}
              >
                <Settings2 className="h-4 w-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="bottom">
              <p>Settings</p>
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
        
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button 
                size="icon" 
                variant="ghost" 
                className={`h-7 w-7 ${imageChanged ? 'text-green-500' : ''}`}
                onClick={onReset}
              >
                <RotateCcw className="h-4 w-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="bottom">
              <p>Reset View</p>
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      </div>
    </CardHeader>
  );
};
