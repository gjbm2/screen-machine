
import React from 'react';
import { CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { RefreshCw, Trash2, Settings } from 'lucide-react';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { ShowMode, PositionMode } from '../types';
import { ScreenSizeSelector, ScreenSize } from './ScreenSizeSelector';

interface DebugImageHeaderProps {
  showMode: ShowMode;
  position: PositionMode;
  selectedSize: ScreenSize;
  setSelectedSize: (size: string) => void;
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
  // Add debug logging for the selected size
  console.log('[DebugImageHeader] Selected size:', selectedSize);
  
  const handleScreenSizeSelect = (sizeName: string) => {
    console.log('[DebugImageHeader] Size selected:', sizeName);
    setSelectedSize(sizeName);
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
        <ScreenSizeSelector 
          selectedSize={selectedSize.name} 
          onSelect={handleScreenSizeSelect}
          onSettingsChange={onSettingsChange}
        />
        
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button 
                size="icon" 
                variant="ghost" 
                className={`h-7 w-7 text-destructive hover:bg-destructive/10 ${imageChanged ? 'text-destructive' : ''}`}
                onClick={onReset}
              >
                <Trash2 className="h-4 w-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="bottom">
              <p>Reset to defaults</p>
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      </div>
    </CardHeader>
  );
};
