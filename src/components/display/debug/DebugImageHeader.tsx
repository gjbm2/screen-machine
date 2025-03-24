
import React from 'react';
import { CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Settings2, RefreshCw } from 'lucide-react';
import { ShowMode, PositionMode } from '../types';
import { ScreenSizeSelector } from './ScreenSizeSelector';

interface DebugImageHeaderProps {
  showMode: ShowMode;
  position: PositionMode;
  selectedScreenSize: string;
  setSelectedScreenSize: (size: string) => void;
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
          selectedSize={selectedScreenSize} 
          onSelect={setSelectedScreenSize} 
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
