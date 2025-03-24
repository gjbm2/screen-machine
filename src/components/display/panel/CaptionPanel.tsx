
import React from 'react';
import { CardContent } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Slider } from "@/components/ui/slider";
import { Button } from "@/components/ui/button";
import { CaptionPosition } from '../../types';
import { FileText } from "lucide-react";

// Font sizes and font families for dropdowns
const FONT_SIZES = [
  '10px', '12px', '14px', '16px', '18px', '20px', '24px', '28px', '32px', '36px', '48px', '64px', '72px', '96px', '120px'
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

interface CaptionPanelProps {
  caption: string;
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
  insertMetadataTag: (key: string) => void;
  applySettings?: () => void;
}

export const CaptionPanel: React.FC<CaptionPanelProps> = ({
  caption,
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
  insertAllMetadata,
  insertMetadataTag,
  applySettings
}) => {
  // Remove # if present for consistency
  const normalizeColor = (color: string) => color.replace('#', '');
  
  // Add # for input[type=color]
  const formatColorForPicker = (color: string) => color.startsWith('#') ? color : `#${color}`;
  
  // Ensure background color has # prefix for proper styling
  const bgColorWithHash = captionBgColor.startsWith('#') ? captionBgColor : `#${captionBgColor}`;
  
  // Calculate background opacity as hex for rgba
  const bgOpacityHex = Math.round(captionBgOpacity * 255).toString(16).padStart(2, '0');
  
  // Debug logs
  console.log('[CaptionPanel] Current caption settings:', {
    captionBgColor,
    bgColorWithHash,
    captionBgOpacity,
    bgOpacityHex,
    captionSize,
    captionFont
  });
  
  return (
    <CardContent className="mt-0 flex-1 overflow-auto space-y-4">
      <div className="flex justify-between items-center">
        <h3 className="text-lg font-medium">Caption</h3>
        <Button
          variant="outline"
          size="sm"
          onClick={insertAllMetadata}
          className="h-8 space-x-1"
        >
          <FileText className="h-3.5 w-3.5" />
          <span>All Metadata</span>
        </Button>
      </div>
      
      <div className="space-y-2">
        <Label htmlFor="caption-textarea">Caption Text</Label>
        <Textarea
          id="caption-textarea"
          placeholder="Enter caption text or use {key} for metadata. Use {all} to include all available metadata."
          value={caption}
          onChange={(e) => setCaption(e.target.value)}
          className="min-h-[120px]"
        />
      </div>
      
      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="captionPosition">Position</Label>
          <Select 
            value={captionPosition} 
            onValueChange={(value) => setCaptionPosition(value as CaptionPosition)}
          >
            <SelectTrigger id="captionPosition">
              <SelectValue placeholder="Caption Position" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="top-left">Top Left</SelectItem>
              <SelectItem value="top-center">Top Center</SelectItem>
              <SelectItem value="top-right">Top Right</SelectItem>
              <SelectItem value="middle-left">Middle Left</SelectItem>
              <SelectItem value="middle-center">Middle Center</SelectItem>
              <SelectItem value="middle-right">Middle Right</SelectItem>
              <SelectItem value="bottom-left">Bottom Left</SelectItem>
              <SelectItem value="bottom-center">Bottom Center</SelectItem>
              <SelectItem value="bottom-right">Bottom Right</SelectItem>
            </SelectContent>
          </Select>
        </div>
        
        <div className="space-y-2">
          <Label htmlFor="captionSize">Font Size</Label>
          <Select 
            value={captionSize} 
            onValueChange={setCaptionSize}
          >
            <SelectTrigger id="captionSize">
              <SelectValue placeholder="Select size" />
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
        <div className="space-y-2">
          <Label htmlFor="captionColor">Text Color</Label>
          <div className="flex gap-2">
            <div 
              className="w-10 h-10 rounded border border-gray-300" 
              style={{ backgroundColor: `#${captionColor}` }}
            />
            <Input
              id="captionColor"
              value={captionColor}
              onChange={(e) => setCaptionColor(normalizeColor(e.target.value))}
              placeholder="Text color (hex)"
              className="flex-1"
              maxLength={6}
            />
            <input
              type="color"
              value={formatColorForPicker(captionColor)}
              onChange={(e) => setCaptionColor(normalizeColor(e.target.value))}
              className="w-10 h-10 p-1 rounded border border-gray-300"
            />
          </div>
        </div>
        
        <div className="space-y-2">
          <Label htmlFor="captionFont">Font Family</Label>
          <Select 
            value={captionFont} 
            onValueChange={setCaptionFont}
          >
            <SelectTrigger id="captionFont">
              <SelectValue placeholder="Select font" />
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
      
      <div className="space-y-2">
        <Label htmlFor="captionBgColor">Background Color</Label>
        <div className="flex gap-2">
          <div 
            className="w-10 h-10 rounded border border-gray-300" 
            style={{ backgroundColor: captionBgColor }}
          />
          <Input
            id="captionBgColor"
            value={captionBgColor.replace('#', '')}
            onChange={(e) => setCaptionBgColor(`#${normalizeColor(e.target.value)}`)}
            placeholder="Background color (hex)"
            className="flex-1"
            maxLength={6}
          />
          <input
            type="color"
            value={captionBgColor}
            onChange={(e) => setCaptionBgColor(e.target.value)}
            className="w-10 h-10 p-1 rounded border border-gray-300"
          />
        </div>
      </div>
      
      <div className="space-y-2">
        <div className="flex justify-between">
          <Label htmlFor="captionBgOpacity">Background Opacity</Label>
          <span className="text-sm">{Math.round(captionBgOpacity * 100)}%</span>
        </div>
        <Slider
          id="captionBgOpacity"
          value={[captionBgOpacity]}
          min={0}
          max={1}
          step={0.1}
          onValueChange={(value) => setCaptionBgOpacity(value[0])}
        />
      </div>
    </CardContent>
  );
};
