
import React from 'react';
import { CardContent } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { PanelLeft, Info } from "lucide-react";
import { CaptionPosition } from '../types';
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

interface CaptionTabProps {
  caption: string;
  previewCaption: string | null;
  captionPosition: CaptionPosition;
  captionSize: string;
  captionColor: string;
  captionFont: string;
  captionBgColor: string;
  captionBgOpacity: number;
  setCaption: (value: string) => void;
  setCaptionPosition: (value: CaptionPosition) => void;
  setCaptionSize: (value: string) => void;
  setCaptionColor: (value: string) => void;
  setCaptionFont: (value: string) => void;
  setCaptionBgColor: (value: string) => void;
  setCaptionBgOpacity: (value: number) => void;
  insertAllMetadata: () => void;
}

const FONT_SIZES = [
  '10px', '12px', '14px', '16px', '18px', '20px', '24px', '28px', '32px', '36px', '48px'
];

const FONT_FAMILIES = [
  'Arial, sans-serif',
  'Helvetica, sans-serif',
  'Times New Roman, serif',
  'Georgia, serif',
  'Courier New, monospace',
  'Verdana, sans-serif',
  'Tahoma, sans-serif',
  'Trebuchet MS, sans-serif',
  'Impact, sans-serif',
  'Comic Sans MS, cursive'
];

export const CaptionTab: React.FC<CaptionTabProps> = ({
  caption,
  previewCaption,
  captionPosition,
  captionSize,
  captionColor,
  captionFont,
  captionBgColor,
  captionBgOpacity,
  setCaption,
  setCaptionPosition,
  setCaptionSize,
  setCaptionColor,
  setCaptionFont,
  setCaptionBgColor,
  setCaptionBgOpacity,
  insertAllMetadata
}) => {
  // Helper to handle color input changes with validation
  const handleColorChange = (setter: (value: string) => void) => (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value.replace(/[^0-9a-fA-F]/g, '').substring(0, 6);
    setter(value);
  };

  // Color picker component to reuse
  const ColorPicker = ({ 
    label, 
    color, 
    onChange, 
    includeHash = false 
  }: { 
    label: string, 
    color: string, 
    onChange: (e: React.ChangeEvent<HTMLInputElement>) => void,
    includeHash?: boolean 
  }) => {
    const displayColor = includeHash ? color : `#${color}`;
    const inputColor = includeHash ? color.replace('#', '') : color;
    
    return (
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <Label htmlFor={`caption-${label.toLowerCase()}-color`} className="text-sm">{label}</Label>
          <div className="flex items-center space-x-2">
            <div 
              className="w-5 h-5 rounded-full border border-gray-300" 
              style={{ backgroundColor: displayColor }}
            />
          </div>
        </div>
        <Popover>
          <PopoverTrigger asChild>
            <Button variant="outline" className="w-full justify-between">
              <span>{displayColor}</span>
              <div 
                className="w-4 h-4 rounded-full border border-gray-300" 
                style={{ backgroundColor: displayColor }}
              />
            </Button>
          </PopoverTrigger>
          <PopoverContent className="w-64">
            <div className="space-y-3">
              <input 
                type="color" 
                value={displayColor}
                onChange={(e) => {
                  const newColor = e.target.value.substring(1);
                  if (includeHash) {
                    setCaptionBgColor(e.target.value);
                  } else {
                    setCaptionColor(newColor);
                  }
                }}
                className="w-full h-8"
              />
              <div className="flex items-center">
                <span className={includeHash ? "mr-1" : "hidden"}>#</span>
                <Input
                  id={`caption-${label.toLowerCase()}-color`}
                  value={inputColor}
                  onChange={onChange}
                  placeholder="Hex color (without #)"
                  maxLength={6}
                />
              </div>
            </div>
          </PopoverContent>
        </Popover>
      </div>
    );
  };

  return (
    <CardContent className="pt-4 pb-2 overflow-y-auto max-h-[50vh]">
      <div className="space-y-4">
        <div className="flex items-center gap-2">
          <Button 
            variant="outline" 
            size="sm" 
            onClick={insertAllMetadata}
            className="text-xs h-7"
          >
            <PanelLeft className="h-3 w-3 mr-1" />
            Insert All Metadata
          </Button>
          
          <Tooltip>
            <TooltipTrigger asChild>
              <Button variant="ghost" size="sm" className="h-7 px-2">
                <Info className="h-3.5 w-3.5" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="right" className="max-w-80">
              <p className="text-xs">
                In addition to metadata tags, you can use regex patterns by starting with / and ending with /flags.
                <br/><br/>
                Example: <code>/Hello, \w+!?/i</code>
              </p>
            </TooltipContent>
          </Tooltip>
        </div>
        
        <div className="space-y-2">
          <Label htmlFor="caption-textarea" className="text-sm">Caption Text</Label>
          <Textarea 
            id="caption-textarea"
            value={caption}
            onChange={(e) => setCaption(e.target.value)}
            placeholder="Enter caption text or use {tag} for metadata..."
            className="min-h-[100px]"
          />
          <div className="text-xs text-gray-500">
            Use {"{tagname}"} to insert metadata values, {"{all}"} for all metadata, or regex patterns like /pattern/flags.
          </div>
        </div>
        
        <div>
          <h3 className="text-sm font-medium mb-2">Caption Preview</h3>
          <div 
            className="p-3 rounded-md min-h-[60px] text-sm whitespace-pre-line"
            style={{
              backgroundColor: `${captionBgColor}${Math.round(captionBgOpacity * 255).toString(16).padStart(2, '0')}`,
              color: `#${captionColor}`,
            }}
          >
            {previewCaption || <span className="text-gray-400">No caption</span>}
          </div>
        </div>
        
        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label htmlFor="caption-position" className="text-sm">Caption Position</Label>
            <Select 
              value={captionPosition} 
              onValueChange={(value) => setCaptionPosition(value as CaptionPosition)}
            >
              <SelectTrigger id="caption-position">
                <SelectValue placeholder="Select position" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="top-left">Top Left</SelectItem>
                <SelectItem value="top-center">Top Center</SelectItem>
                <SelectItem value="top-right">Top Right</SelectItem>
                <SelectItem value="bottom-left">Bottom Left</SelectItem>
                <SelectItem value="bottom-center">Bottom Center</SelectItem>
                <SelectItem value="bottom-right">Bottom Right</SelectItem>
              </SelectContent>
            </Select>
          </div>
          
          <div className="space-y-2">
            <Label htmlFor="caption-size" className="text-sm">Font Size</Label>
            <Select 
              value={captionSize} 
              onValueChange={(value) => setCaptionSize(value)}
            >
              <SelectTrigger id="caption-size">
                <SelectValue placeholder="Select font size" />
              </SelectTrigger>
              <SelectContent>
                {FONT_SIZES.map(size => (
                  <SelectItem key={size} value={size}>{size}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
        
        <div className="grid grid-cols-2 gap-4">
          <ColorPicker 
            label="Text Color" 
            color={captionColor}
            onChange={handleColorChange(setCaptionColor)}
          />
          
          <div className="space-y-2">
            <Label htmlFor="caption-font" className="text-sm">Font Family</Label>
            <Select 
              value={captionFont} 
              onValueChange={(value) => setCaptionFont(value)}
            >
              <SelectTrigger id="caption-font">
                <SelectValue placeholder="Select font family" />
              </SelectTrigger>
              <SelectContent>
                {FONT_FAMILIES.map(font => (
                  <SelectItem key={font} value={font} style={{ fontFamily: font }}>
                    {font.split(',')[0]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <ColorPicker 
            label="Background Color" 
            color={captionBgColor}
            onChange={(e) => setCaptionBgColor('#' + e.target.value)}
            includeHash={true}
          />

          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label htmlFor="caption-bg-opacity" className="text-sm">Background Opacity</Label>
              <span className="text-xs text-gray-500">{Math.round(captionBgOpacity * 100)}%</span>
            </div>
            <Input 
              id="caption-bg-opacity"
              type="range"
              min="0"
              max="1"
              step="0.05"
              value={captionBgOpacity}
              onChange={(e) => setCaptionBgOpacity(parseFloat(e.target.value))}
              className="w-full"
            />
          </div>
        </div>
      </div>
    </CardContent>
  );
};
