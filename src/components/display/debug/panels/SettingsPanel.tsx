
import React from 'react';
import { CardContent } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { ShowMode, PositionMode, TransitionType } from '../../types';
import { Trash2 } from 'lucide-react';

interface SettingsPanelProps {
  showMode: ShowMode;
  position: PositionMode;
  refreshInterval: number;
  backgroundColor: string;
  transition: TransitionType;
  setShowMode: (value: ShowMode) => void;
  setPosition: (value: PositionMode) => void;
  setRefreshInterval: (value: number) => void;
  setBackgroundColor: (value: string) => void;
  setTransition: (value: TransitionType) => void;
  resetSettings: () => void;
}

export const SettingsPanel: React.FC<SettingsPanelProps> = ({
  showMode,
  position,
  refreshInterval,
  backgroundColor,
  transition,
  setShowMode,
  setPosition,
  setRefreshInterval,
  setBackgroundColor,
  setTransition,
  resetSettings
}) => {
  return (
    <CardContent className="mt-0 flex-1 overflow-auto space-y-4">
      <div className="flex justify-between items-center">
        <h3 className="text-lg font-medium">Display Settings</h3>
        <Button
          variant="ghost"
          size="sm"
          onClick={resetSettings}
          className="h-8 px-2 text-xs text-destructive hover:bg-destructive/10 hover:text-destructive"
        >
          <Trash2 className="h-3.5 w-3.5 mr-1" />
          Reset to defaults
        </Button>
      </div>

      <div className="space-y-4">
        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label htmlFor="showMode">Display Mode</Label>
            <Select 
              value={showMode} 
              onValueChange={(value) => setShowMode(value as ShowMode)}
            >
              <SelectTrigger id="showMode">
                <SelectValue placeholder="Select display mode" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="fit">Fit</SelectItem>
                <SelectItem value="fill">Fill</SelectItem>
                <SelectItem value="stretch">Stretch</SelectItem>
                <SelectItem value="actual">Actual Size</SelectItem>
              </SelectContent>
            </Select>
          </div>
          
          <div className="space-y-2">
            <Label htmlFor="position">Position</Label>
            <Select 
              value={position} 
              onValueChange={(value) => setPosition(value as PositionMode)}
            >
              <SelectTrigger id="position">
                <SelectValue placeholder="Select position" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="center">Center</SelectItem>
                <SelectItem value="top-left">Top Left</SelectItem>
                <SelectItem value="top-center">Top Center</SelectItem>
                <SelectItem value="top-right">Top Right</SelectItem>
                <SelectItem value="center-left">Center Left</SelectItem>
                <SelectItem value="center-right">Center Right</SelectItem>
                <SelectItem value="bottom-left">Bottom Left</SelectItem>
                <SelectItem value="bottom-center">Bottom Center</SelectItem>
                <SelectItem value="bottom-right">Bottom Right</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
        
        <div className="space-y-2">
          <Label htmlFor="transition">Transition Type</Label>
          <Select 
            value={transition} 
            onValueChange={(value) => setTransition(value as TransitionType)}
          >
            <SelectTrigger id="transition">
              <SelectValue placeholder="Select transition" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="cut">Cut</SelectItem>
              <SelectItem value="fade-fast">Fast Fade</SelectItem>
              <SelectItem value="fade-slow">Slow Fade</SelectItem>
            </SelectContent>
          </Select>
        </div>
        
        <div className="space-y-2">
          <Label htmlFor="refreshInterval">Refresh Interval (seconds)</Label>
          <div className="flex items-center gap-2">
            <Slider
              value={[refreshInterval]}
              min={1}
              max={60}
              step={1}
              onValueChange={(value) => setRefreshInterval(value[0])}
              className="flex-1"
            />
            <span className="w-8 text-center">{refreshInterval}s</span>
          </div>
        </div>
        
        <div className="space-y-2">
          <Label htmlFor="backgroundColor">Background Color</Label>
          <div className="flex gap-2">
            <div 
              className="w-10 h-10 rounded border border-gray-300" 
              style={{ backgroundColor: `#${backgroundColor}` }}
            />
            <Input
              id="backgroundColor"
              value={backgroundColor}
              onChange={(e) => setBackgroundColor(e.target.value.replace('#', ''))}
              placeholder="Background color (hex)"
              className="flex-1"
              maxLength={6}
            />
            <input
              type="color"
              value={`#${backgroundColor}`}
              onChange={(e) => setBackgroundColor(e.target.value.substring(1))}
              className="w-10 h-10 p-1 rounded border border-gray-300"
            />
          </div>
        </div>
      </div>
    </CardContent>
  );
};
