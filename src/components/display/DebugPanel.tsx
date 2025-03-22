import React, { useState, useEffect } from 'react';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { RefreshCw, Clock, Info, Copy, Check, Clipboard, FileImage, Settings, Image as ImageIcon, Eye, EyeOff, Palette, Type, Database } from "lucide-react";
import { DisplayParams } from './types';
import { createUrlWithParams } from './utils';
import { useNavigate } from 'react-router-dom';
import { toast } from 'sonner';

interface DebugPanelProps {
  params: DisplayParams;
  imageUrl: string | null;
  lastModified: string | null;
  lastChecked: string | null;
  nextCheckTime: string | null;
  imageKey: number;
  outputFiles: string[];
  imageChanged?: boolean;
  onCheckNow: () => void;
}

export const DebugPanel: React.FC<DebugPanelProps> = ({
  params,
  imageUrl,
  lastModified,
  lastChecked,
  nextCheckTime,
  imageKey,
  outputFiles,
  imageChanged,
  onCheckNow
}) => {
  const navigate = useNavigate();
  const [activeTab, setActiveTab] = useState("files");
  const [customUrl, setCustomUrl] = useState(params.output || "");
  const [showMode, setShowMode] = useState(params.showMode);
  const [position, setPosition] = useState(params.position);
  const [refreshInterval, setRefreshInterval] = useState(params.refreshInterval);
  const [backgroundColor, setBackgroundColor] = useState(params.backgroundColor);
  const [caption, setCaption] = useState(params.caption || "");
  const [captionPosition, setCaptionPosition] = useState(params.captionPosition || "bottom-center");
  const [captionSize, setCaptionSize] = useState(params.captionSize || "16px");
  const [captionColor, setCaptionColor] = useState(params.captionColor || "ffffff");
  const [captionFont, setCaptionFont] = useState(params.captionFont || "Arial, sans-serif");
  const [dataTag, setDataTag] = useState(params.data === undefined ? "" : (params.data || ""));
  const [transition, setTransition] = useState(params.transition || "cut");
  const [copied, setCopied] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);

  // Generate the URL for the current settings
  const generateUrl = () => {
    const newParams: DisplayParams = {
      output: customUrl || null,
      showMode,
      position,
      refreshInterval,
      backgroundColor,
      debugMode: false,
      caption: caption || null,
      captionPosition,
      captionSize,
      captionColor,
      captionFont,
      data: dataTag === "" ? undefined : dataTag,
      transition,
    };
    
    return createUrlWithParams(newParams);
  };

  // Apply the current settings
  const applySettings = () => {
    const url = generateUrl();
    navigate(url);
  };

  // Copy the URL to clipboard
  const copyUrl = () => {
    const url = window.location.origin + generateUrl();
    navigator.clipboard.writeText(url)
      .then(() => {
        setCopied(true);
        toast.success("URL copied to clipboard");
        setTimeout(() => setCopied(false), 2000);
      })
      .catch(err => {
        console.error('Failed to copy URL: ', err);
        toast.error("Failed to copy URL");
      });
  };

  // Select a file from the list
  const selectFile = (file: string) => {
    setCustomUrl(file);
    setActiveTab("settings");
  };

  // Format the file list for display
  const formatFileName = (file: string) => {
    // If it's a URL, extract the filename
    if (file.startsWith('http')) {
      try {
        const url = new URL(file);
        return url.pathname.split('/').pop() || file;
      } catch (e) {
        return file;
      }
    }
    return file;
  };

  // Reset to default settings
  const resetSettings = () => {
    setShowMode('fit');
    setPosition('center');
    setRefreshInterval(5);
    setBackgroundColor('000000');
    setCaption('');
    setCaptionPosition('bottom-center');
    setCaptionSize('16px');
    setCaptionColor('ffffff');
    setCaptionFont('Arial, sans-serif');
    setDataTag('');
    setTransition('cut');
  };

  // Determine if the current file is selected
  const isCurrentFile = (file: string) => {
    if (!imageUrl) return false;
    
    // Handle both relative and absolute URLs
    if (imageUrl.startsWith('http')) {
      return imageUrl === file;
    } else {
      // For relative URLs, compare just the filename
      const currentFile = imageUrl.split('/').pop();
      const compareFile = file.split('/').pop();
      return currentFile === compareFile;
    }
  };

  // Format the time for display
  const formatTime = (timeString: string | null) => {
    if (!timeString) return 'Never';
    
    try {
      const date = new Date(timeString);
      return date.toLocaleTimeString();
    } catch (e) {
      return timeString;
    }
  };

  return (
    <Card className="w-1/3 max-w-md absolute left-4 top-4 z-10 opacity-90 hover:opacity-100 transition-opacity">
      <CardHeader className="pb-2">
        <CardTitle className="text-lg flex justify-between items-center">
          <span>Display Debug Panel</span>
          <div className="flex space-x-2">
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button 
                    variant="outline" 
                    size="sm"
                    onClick={onCheckNow}
                    className="h-8 w-8 p-0"
                  >
                    <RefreshCw className="h-4 w-4" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>
                  <p>Check for updates now</p>
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
            
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button 
                    variant="outline" 
                    size="sm"
                    onClick={copyUrl}
                    className="h-8 w-8 p-0"
                  >
                    {copied ? <Check className="h-4 w-4" /> : <Clipboard className="h-4 w-4" />}
                  </Button>
                </TooltipTrigger>
                <TooltipContent>
                  <p>Copy display URL</p>
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
          </div>
        </CardTitle>
        <CardDescription>
          Configure display settings and preview images
        </CardDescription>
      </CardHeader>
      
      <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
        <TabsList className="grid grid-cols-2 mx-4">
          <TabsTrigger value="files" className="flex items-center">
            <FileImage className="mr-2 h-4 w-4" />
            Files
          </TabsTrigger>
          <TabsTrigger value="settings" className="flex items-center">
            <Settings className="mr-2 h-4 w-4" />
            Settings
          </TabsTrigger>
        </TabsList>
        
        <TabsContent value="files" className="mt-0">
          <CardContent className="pt-4">
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-medium">Available Output Files</h3>
                {imageChanged && (
                  <Badge variant="outline" className="bg-amber-50 text-amber-700 border-amber-200">
                    <RefreshCw className="h-3 w-3 mr-1 animate-spin" />
                    Updated
                  </Badge>
                )}
              </div>
              
              <ScrollArea className="h-[300px] rounded-md border p-2">
                {outputFiles.length > 0 ? (
                  <div className="space-y-2">
                    {outputFiles.map((file, index) => (
                      <div 
                        key={index}
                        className={`flex items-center justify-between p-2 rounded-md cursor-pointer hover:bg-gray-100 ${isCurrentFile(file) ? 'bg-blue-50 border border-blue-200' : ''}`}
                        onClick={() => selectFile(file)}
                      >
                        <div className="flex items-center">
                          <ImageIcon className="h-4 w-4 mr-2 text-gray-500" />
                          <span className="text-sm truncate max-w-[180px]">{formatFileName(file)}</span>
                        </div>
                        {isCurrentFile(file) && (
                          <Badge variant="secondary" size="sm">
                            Current
                          </Badge>
                        )}
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="flex flex-col items-center justify-center h-full text-gray-500">
                    <FileImage className="h-8 w-8 mb-2" />
                    <p className="text-sm">No output files found</p>
                  </div>
                )}
              </ScrollArea>
              
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <Label htmlFor="custom-url" className="text-sm">Custom URL or Path</Label>
                  <TooltipProvider>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Info className="h-4 w-4 text-gray-400" />
                      </TooltipTrigger>
                      <TooltipContent>
                        <p className="max-w-xs">Enter a URL or relative path to an image</p>
                      </TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                </div>
                <div className="flex space-x-2">
                  <Input 
                    id="custom-url" 
                    value={customUrl} 
                    onChange={(e) => setCustomUrl(e.target.value)}
                    placeholder="Enter URL or path..."
                  />
                  <Button 
                    variant="secondary"
                    onClick={() => setActiveTab("settings")}
                  >
                    Use
                  </Button>
                </div>
              </div>
            </div>
          </CardContent>
        </TabsContent>
        
        <TabsContent value="settings" className="mt-0">
          <CardContent className="pt-4 pb-2">
            <div className="space-y-4">
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <Label htmlFor="image-url" className="text-sm">Image URL/Path</Label>
                  <Badge variant={imageUrl ? "outline" : "secondary"} className="text-xs font-normal">
                    {imageKey > 0 ? `Key: ${imageKey}` : 'No Image'}
                  </Badge>
                </div>
                <Input 
                  id="image-url" 
                  value={customUrl} 
                  onChange={(e) => setCustomUrl(e.target.value)}
                  placeholder="Enter URL or path..."
                />
              </div>
              
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label htmlFor="show-mode" className="text-sm">Display Mode</Label>
                  <Select value={showMode} onValueChange={setShowMode}>
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
                  <Select value={position} onValueChange={setPosition}>
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
              
              <div className="flex items-center space-x-2">
                <Button 
                  variant="outline" 
                  size="sm"
                  onClick={() => setShowAdvanced(!showAdvanced)}
                  className="text-xs"
                >
                  {showAdvanced ? <EyeOff className="h-3 w-3 mr-1" /> : <Eye className="h-3 w-3 mr-1" />}
                  {showAdvanced ? "Hide Advanced" : "Show Advanced"}
                </Button>
                
                <Button 
                  variant="ghost" 
                  size="sm"
                  onClick={resetSettings}
                  className="text-xs ml-auto"
                >
                  Reset to Defaults
                </Button>
              </div>
              
              {showAdvanced && (
                <>
                  <Separator />
                  
                  <div className="space-y-4">
                    <div className="flex items-center">
                      <Type className="h-4 w-4 mr-2 text-gray-500" />
                      <h3 className="text-sm font-medium">Caption Settings</h3>
                    </div>
                    
                    <div className="space-y-2">
                      <Label htmlFor="caption" className="text-sm">Caption Text</Label>
                      <Input 
                        id="caption"
                        value={caption}
                        onChange={(e) => setCaption(e.target.value)}
                        placeholder="Enter caption text..."
                      />
                    </div>
                    
                    <div className="grid grid-cols-2 gap-4">
                      <div className="space-y-2">
                        <Label htmlFor="caption-position" className="text-sm">Caption Position</Label>
                        <Select value={captionPosition} onValueChange={setCaptionPosition}>
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
                    
                    <Separator />
                    
                    <div className="space-y-4">
                      <div className="flex items-center">
                        <Database className="h-4 w-4 mr-2 text-gray-500" />
                        <h3 className="text-sm font-medium">Metadata & Transitions</h3>
                      </div>
                      
                      <div className="space-y-2">
                        <div className="flex items-center justify-between">
                          <Label htmlFor="data-tag" className="text-sm">Metadata Tag</Label>
                          <TooltipProvider>
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <Info className="h-4 w-4 text-gray-400" />
                              </TooltipTrigger>
                              <TooltipContent>
                                <p className="max-w-xs">Leave empty to show all metadata, or specify a tag name</p>
                              </TooltipContent>
                            </Tooltip>
                          </TooltipProvider>
                        </div>
                        <Input 
                          id="data-tag"
                          value={dataTag}
                          onChange={(e) => setDataTag(e.target.value)}
                          placeholder="Leave empty for all metadata"
                        />
                      </div>
                      
                      <div className="space-y-2">
                        <Label htmlFor="transition" className="text-sm">Transition Effect</Label>
                        <Select value={transition} onValueChange={setTransition}>
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
                    </div>
                  </div>
                </>
              )}
            </div>
          </CardContent>
          
          <CardFooter className="flex justify-between pt-0">
            <div className="text-xs text-gray-500 flex items-center">
              <Clock className="h-3 w-3 mr-1" />
              <span>Last checked: {formatTime(lastChecked)}</span>
            </div>
            <Button onClick={applySettings}>Apply Settings</Button>
          </CardFooter>
        </TabsContent>
      </Tabs>
    </Card>
  );
};
