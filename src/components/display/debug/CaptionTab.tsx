
import React from 'react';
import { CardContent } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { PanelLeft } from "lucide-react";
import { CaptionPosition } from '../types';

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
            Use {"{tagname}"} to insert metadata values or {"{all}"} for all metadata.
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
            <Input 
              id="caption-size"
              value={captionSize}
              onChange={(e) => setCaptionSize(e.target.value)}
              placeholder="16px"
            />
          </div>
        </div>
        
        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label htmlFor="caption-color" className="text-sm">Text Color</Label>
              <div className="flex items-center space-x-2">
                <div 
                  className="w-3 h-3 rounded-full border" 
                  style={{ backgroundColor: `#${captionColor}` }}
                />
              </div>
            </div>
            <Input 
              id="caption-color"
              value={captionColor}
              onChange={(e) => setCaptionColor(e.target.value.replace(/[^0-9a-fA-F]/g, '').substring(0, 6))}
              placeholder="Hex color (without #)"
              maxLength={6}
            />
          </div>
          
          <div className="space-y-2">
            <Label htmlFor="caption-font" className="text-sm">Font Family</Label>
            <Input 
              id="caption-font"
              value={captionFont}
              onChange={(e) => setCaptionFont(e.target.value)}
              placeholder="Arial, sans-serif"
            />
          </div>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label htmlFor="caption-bg-color" className="text-sm">Background Color</Label>
              <div className="flex items-center space-x-2">
                <div 
                  className="w-3 h-3 rounded-full border" 
                  style={{ backgroundColor: captionBgColor }}
                />
              </div>
            </div>
            <Input 
              id="caption-bg-color"
              value={captionBgColor.replace('#', '')}
              onChange={(e) => setCaptionBgColor('#' + e.target.value.replace(/[^0-9a-fA-F]/g, '').substring(0, 6))}
              placeholder="Hex color (without #)"
              maxLength={6}
            />
          </div>

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
