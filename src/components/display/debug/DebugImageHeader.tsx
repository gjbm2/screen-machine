
import React from 'react';
import { CardHeader, CardTitle } from "@/components/ui/card";
import { Move, RefreshCw } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { ShowMode, PositionMode } from '../types';
import { ScreenSizeSelector } from './ScreenSizeSelector';

interface DebugImageHeaderProps {
  showMode: ShowMode;
  position: PositionMode;
  selectedScreenSize: string;
  setSelectedScreenSize: (value: string) => void;
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
    <CardHeader className="pb-2 flex flex-row justify-between items-center card-header-drag-handle cursor-grab">
      <div className="flex items-center">
        <Move className="h-4 w-4 text-muted-foreground mr-2" />
        <CardTitle className="text-lg">Image Preview ({showMode} mode, {position} position)</CardTitle>
      </div>
      
      <div className="flex items-center space-x-4">
        <ScreenSizeSelector 
          selectedScreenSize={selectedScreenSize}
          setSelectedScreenSize={setSelectedScreenSize}
          onSettingsChange={onSettingsChange}
        />
        
        <Button
          variant="outline"
          size="sm"
          onClick={onReset}
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
  );
};
