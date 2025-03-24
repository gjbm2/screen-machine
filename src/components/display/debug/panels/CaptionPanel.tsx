
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

interface CaptionPanelProps {
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

export const CaptionPanel: React.FC<CaptionPanelProps> = ({
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
  // Remove # if present for consistency
  const normalizeColor = (color: string) => color.replace('#', '');
  
  // Add # for input[type=color]
  const formatColorForPicker = (color: string) => color.startsWith('#') ? color : `#${color}`;
  
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
        
        {previewCaption && (
          <div className="bg-gray-100 dark:bg-gray-800 p-3 rounded-md text-sm mt-1 whitespace-pre-wrap">
            <div className="font-medium mb-1 text-xs text-muted-foreground">Preview:</div>
            {previewCaption}
          </div>
        )}
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
          <Input
            id="captionSize"
            value={captionSize}
            onChange={(e) => setCaptionSize(e.target.value)}
            placeholder="16px"
          />
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
          <Input
            id="captionFont"
            value={captionFont}
            onChange={(e) => setCaptionFont(e.target.value)}
            placeholder="Arial, sans-serif"
          />
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
        <Label htmlFor="captionBgOpacity">Background Opacity: {captionBgOpacity.toFixed(1)}</Label>
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
