
import React from 'react';
import { CardContent } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ShowMode, PositionMode, TransitionType } from '../types';

interface SettingsTabProps {
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

export const SettingsTab: React.FC<SettingsTabProps> = ({
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
    <CardContent className="pt-4 pb-2 overflow-y-auto max-h-[50vh]">
      <div className="space-y-4">
        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label htmlFor="show-mode" className="text-sm">Display Mode</Label>
            <Select 
              value={showMode} 
              onValueChange={(value) => setShowMode(value as ShowMode)}
            >
              <SelectTrigger id="show-mode">
                <SelectValue placeholder="Select mode" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="fit">Fit</SelectItem>
                <SelectItem value="fill">Fill</SelectItem>
                <SelectItem value="actual">Actual Size</SelectItem>
                <SelectItem value="stretch">Stretch</SelectItem>
              </SelectContent>
            </Select>
          </div>
          
          <div className="space-y-2">
            <Label htmlFor="position" className="text-sm">Position</Label>
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
          <div className="flex items-center justify-between">
            <Label htmlFor="refresh-interval" className="text-sm">Refresh Interval (seconds)</Label>
            <span className="text-xs text-gray-500">{refreshInterval}s</span>
          </div>
          <Slider 
            id="refresh-interval"
            min={1} 
            max={60} 
            step={1}
            value={[refreshInterval]}
            onValueChange={(value) => setRefreshInterval(value[0])}
          />
        </div>
        
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <Label htmlFor="background-color" className="text-sm">Background Color</Label>
            <div className="flex items-center space-x-2">
              <div 
                className="w-4 h-4 rounded-full border" 
                style={{ backgroundColor: `#${backgroundColor}` }}
              />
              <span className="text-xs text-gray-500">#{backgroundColor}</span>
            </div>
          </div>
          <Input 
            id="background-color"
            value={backgroundColor}
            onChange={(e) => setBackgroundColor(e.target.value.replace(/[^0-9a-fA-F]/g, '').substring(0, 6))}
            placeholder="Hex color (without #)"
            maxLength={6}
          />
        </div>
        
        <div className="space-y-2">
          <Label htmlFor="transition" className="text-sm">Transition Effect</Label>
          <Select 
            value={transition} 
            onValueChange={(value) => setTransition(value as TransitionType)}
          >
            <SelectTrigger id="transition">
              <SelectValue placeholder="Select transition" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="cut">Cut (No Transition)</SelectItem>
              <SelectItem value="fade-fast">Fast Fade</SelectItem>
              <SelectItem value="fade-slow">Slow Fade</SelectItem>
            </SelectContent>
          </Select>
        </div>
        
        <Button 
          variant="ghost" 
          size="sm"
          onClick={resetSettings}
          className="text-xs"
        >
          Reset to Defaults
        </Button>
      </div>
    </CardContent>
  );
};
