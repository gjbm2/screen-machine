
import React, { useState, useEffect } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { DisplayParams, ShowMode, PositionMode, CaptionPosition, TransitionType } from './types';
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Slider } from "@/components/ui/slider";
import { Button } from "@/components/ui/button";
import { Form, FormField, FormItem, FormLabel, FormControl } from "@/components/ui/form";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useForm } from "react-hook-form";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useNavigate } from "react-router-dom";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Info, RefreshCw, ChevronDown, ChevronUp, Copy, Eye, EyeOff, Code } from "lucide-react";
import { Separator } from "@/components/ui/separator";
import { createUrlWithParams, extractImageMetadata } from './utils';
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Badge } from "@/components/ui/badge";
import { toast } from "sonner";

interface DebugPanelProps {
  params: DisplayParams;
  imageUrl: string | null;
  lastModified: string | null;
  lastChecked: Date | null;
  nextCheckTime: string;
  imageKey: number;
  outputFiles: string[];
  imageChanged?: boolean;
  onCheckNow?: () => void;
}

export const formatDateTime = (date: Date | null) => {
  if (!date) return 'N/A';
  return date.toLocaleTimeString();
};

interface DebugFormValues {
  output: string;
  showMode: ShowMode;
  position: PositionMode;
  refreshInterval: number;
  backgroundColor: string;
  customUrl: string;
  data: string;
  caption: string;
  captionPosition: CaptionPosition;
  captionSize: string;
  captionColor: string;
  captionFont: string;
  transition: TransitionType;
}

const PRESET_COLORS = [
  '000000', '333333', '555555', '777777', 
  '0A1172', '1C39BB', '3944BC', '7B68EE',
  '008000', '228B22', '2E8B57', '32CD32',
  '800000', 'A52A2A', 'B22222', 'CD5C5C', 
  'FFFFFF', 'F5F5F5', 'E8E8E8', 'D3D3D3'
];

const CAPTION_FONTS = [
  'Arial, sans-serif',
  'Verdana, sans-serif',
  'Helvetica, sans-serif',
  'Times New Roman, serif',
  'Georgia, serif',
  'Courier New, monospace',
  'Trebuchet MS, sans-serif',
  'Impact, sans-serif',
  'Comic Sans MS, cursive'
];

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
  const [selectedColor, setSelectedColor] = useState(params.backgroundColor);
  const [helpCollapsed, setHelpCollapsed] = useState(true);
  const [metadataPreview, setMetadataPreview] = useState<Record<string, string>>({});
  const [isLoadingMetadata, setIsLoadingMetadata] = useState(false);
  const [activeTab, setActiveTab] = useState<string>("settings");
  
  const form = useForm<DebugFormValues>({
    defaultValues: {
      output: params.output || '',
      showMode: params.showMode,
      position: params.position,
      refreshInterval: params.refreshInterval,
      backgroundColor: params.backgroundColor,
      customUrl: '',
      data: params.data || '',
      caption: params.caption || '',
      captionPosition: params.captionPosition || 'bottom-center',
      captionSize: params.captionSize || '16px',
      captionColor: params.captionColor || 'ffffff',
      captionFont: params.captionFont || 'Arial, sans-serif',
      transition: params.transition || 'cut'
    }
  });

  const applySettings = (values: DebugFormValues) => {
    const outputParam = values.output || params.output;
    
    const queryParams = new URLSearchParams();
    if (outputParam) {
      queryParams.set('output', outputParam);
    }
    queryParams.set('show', values.showMode);
    queryParams.set('position', values.position);
    queryParams.set('refresh', values.refreshInterval.toString());
    queryParams.set('background', values.backgroundColor);
    queryParams.set('debug', 'true');
    
    // Add optional parameters
    if (values.data) {
      queryParams.set('data', values.data);
    }
    
    if (values.caption) {
      queryParams.set('caption', values.caption);
      queryParams.set('caption-position', values.captionPosition);
      queryParams.set('caption-size', values.captionSize);
      queryParams.set('caption-color', values.captionColor);
      queryParams.set('caption-font', values.captionFont);
    }
    
    // Add transition parameter
    queryParams.set('transition', values.transition);
    
    navigate(`/display?${queryParams.toString()}`);
  };

  const applyUrlChange = async (url: string) => {
    if (!url) {
      toast.error("Please enter a URL");
      return;
    }
    
    // Show loading state
    setIsLoadingMetadata(true);
    
    // Handle URL encoding for parameters in custom URLs
    let processedUrl = url;
    
    // If the URL contains query parameters, ensure they're properly encoded
    if (url.includes('?') || url.includes('&')) {
      try {
        // For URLs with parameters, we need to encode more carefully
        const urlObj = new URL(url.startsWith('http') ? url : `http://example.com/${url}`);
        processedUrl = encodeURIComponent(url);
      } catch (e) {
        console.error('Error processing URL:', e);
        processedUrl = encodeURIComponent(url);
      }
    }
    
    // Attempt to fetch metadata from the image URL
    try {
      const metadata = await extractImageMetadata(url, '');
      setMetadataPreview(metadata);
      toast.success("Metadata loaded successfully");
      
      // Switch to the metadata tab
      setActiveTab("metadata");
    } catch (error) {
      console.error("Error fetching metadata:", error);
      toast.error("Could not load metadata from image");
    } finally {
      setIsLoadingMetadata(false);
    }
    
    // Update the URL regardless of metadata success
    const queryParams = new URLSearchParams();
    queryParams.set('output', processedUrl);
    queryParams.set('show', params.showMode);
    queryParams.set('position', params.position);
    queryParams.set('refresh', params.refreshInterval.toString());
    queryParams.set('background', params.backgroundColor);
    queryParams.set('debug', 'true');
    
    // Preserve existing caption parameters
    if (params.caption) {
      queryParams.set('caption', params.caption);
      if (params.captionPosition) queryParams.set('caption-position', params.captionPosition);
      if (params.captionSize) queryParams.set('caption-size', params.captionSize);
      if (params.captionColor) queryParams.set('caption-color', params.captionColor);
      if (params.captionFont) queryParams.set('caption-font', params.captionFont);
    }
    
    // Preserve data parameter
    if (params.data !== undefined) {
      if (params.data !== null) {
        queryParams.set('data', params.data);
      } else {
        queryParams.set('data', '');
      }
    }
    
    // Preserve transition parameter
    if (params.transition) {
      queryParams.set('transition', params.transition);
    }
    
    navigate(`/display?${queryParams.toString()}`);
  };

  const endDebugMode = () => {
    const queryParams = new URLSearchParams();
    if (params.output) queryParams.set('output', params.output);
    queryParams.set('show', params.showMode);
    queryParams.set('position', params.position);
    queryParams.set('refresh', params.refreshInterval.toString());
    queryParams.set('background', params.backgroundColor);
    
    // Pass through caption parameters
    if (params.caption) {
      queryParams.set('caption', params.caption);
      if (params.captionPosition) queryParams.set('caption-position', params.captionPosition);
      if (params.captionSize) queryParams.set('caption-size', params.captionSize);
      if (params.captionColor) queryParams.set('caption-color', params.captionColor);
      if (params.captionFont) queryParams.set('caption-font', params.captionFont);
    }
    
    // Pass through data parameter
    if (params.data !== undefined) {
      if (params.data !== null) {
        queryParams.set('data', params.data);
      } else {
        queryParams.set('data', '');
      }
    }
    
    // Pass through transition parameter
    if (params.transition) {
      queryParams.set('transition', params.transition);
    }
    
    navigate(`/display?${queryParams.toString()}`);
  };

  const selectOutputFile = (file: string) => {
    form.setValue('output', file);
    applySettings({
      ...form.getValues(),
      output: file
    });
  };

  const copyMetadataTag = (tag: string) => {
    // Get current caption value
    const currentCaption = form.getValues('caption');
    
    // Insert {tag} at the current end of the caption
    const newCaption = currentCaption ? `${currentCaption} {${tag}}` : `{${tag}}`;
    form.setValue('caption', newCaption);
    
    toast.success(`Added {${tag}} to caption`);
  };

  const useAllMetadata = () => {
    form.setValue('caption', '{all}');
    toast.success("Set caption to show all metadata");
  };

  return (
    <Card className="absolute top-4 left-4 z-10 w-auto min-w-96 max-w-[90vw] max-h-[90vh] overflow-auto bg-white/95 dark:bg-gray-800/95 shadow-lg border-slate-200">
      <CardHeader className="pb-2 flex flex-row justify-between items-center">
        <CardTitle className="text-lg">Display Configuration</CardTitle>
        <div className="flex items-center space-x-2">
          {onCheckNow && (
            <Button 
              type="button" 
              onClick={onCheckNow}
              size="sm"
              variant="outline"
              className="flex items-center gap-1"
            >
              <RefreshCw className="h-3 w-3" />
              Check Now
            </Button>
          )}
          <Button 
            type="button" 
            size="sm"
            variant="outline"
            onClick={endDebugMode}
            disabled={!params.output}
          >
            <Eye className="h-3 w-3 mr-1" />
            Exit Debug
          </Button>
        </div>
      </CardHeader>
      <CardContent className="text-sm p-0">
        <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
          <div className="px-4 border-b">
            <TabsList className="w-full justify-start">
              <TabsTrigger value="settings" className="flex-1">Settings</TabsTrigger>
              <TabsTrigger value="files" className="flex-1">
                Files
                <Badge variant="outline" className="ml-2 text-xs">
                  {outputFiles.length}
                </Badge>
              </TabsTrigger>
              <TabsTrigger value="metadata" className="flex-1">
                Metadata
                <Badge variant="outline" className="ml-2 text-xs">
                  {Object.keys(metadataPreview).length}
                </Badge>
              </TabsTrigger>
              <TabsTrigger value="info" className="flex-1">Info</TabsTrigger>
            </TabsList>
          </div>

          <ScrollArea className="h-[70vh]">
            <TabsContent value="settings" className="pt-2 px-4 space-y-4 m-0">
              {(!params.output || outputFiles.length === 0) && (
                <Collapsible open={!helpCollapsed} onOpenChange={setHelpCollapsed}>
                  <div className="flex justify-between items-center">
                    <h3 className="text-sm font-medium">Display Page Configuration Help</h3>
                    <CollapsibleTrigger asChild>
                      <Button variant="ghost" size="sm">
                        {helpCollapsed ? <ChevronDown size={16} /> : <ChevronUp size={16} />}
                      </Button>
                    </CollapsibleTrigger>
                  </div>
                  <CollapsibleContent>
                    <Alert className="my-2">
                      <Info className="h-4 w-4" />
                      <AlertTitle>Display Page Configuration</AlertTitle>
                      <AlertDescription>
                        <p className="mb-2">This debug view helps you configure the display page. Set your preferences and generate a shareable URL.</p>
                        <ul className="list-disc list-inside space-y-1 text-xs">
                          <li><strong>output</strong>: (required) Image to display from /output/ directory or full URL</li>
                          <li><strong>show</strong>: Display mode - 'fit', 'fill', 'actual', or 'stretch'</li>
                          <li><strong>position</strong>: Image position - e.g., 'center', 'top-left', 'bottom-right'</li>
                          <li><strong>refresh</strong>: Check for image updates every X seconds</li>
                          <li><strong>background</strong>: Background color hexcode</li>
                          <li><strong>caption</strong>: Text to display over the image (use {"{key}"} to insert metadata values or {"{all}"} for all metadata)</li>
                          <li><strong>caption-position</strong>: Where to display the caption</li>
                          <li><strong>data</strong>: Extract and display image metadata (use empty value for all metadata)</li>
                          <li><strong>transition</strong>: How to transition between image updates - 'cut', 'fade-fast', or 'fade-slow'</li>
                        </ul>
                      </AlertDescription>
                    </Alert>
                  </CollapsibleContent>
                </Collapsible>
              )}

              <Form {...form}>
                <form onSubmit={form.handleSubmit(applySettings)} className="space-y-4">
                  <div className="grid grid-cols-1 md:grid-cols-1 gap-4">
                    <div className="pt-2">
                      <FormLabel className="font-medium">Set Image URL</FormLabel>
                      <div className="flex space-x-2 mt-1">
                        <Input 
                          placeholder="Enter full URL or image path" 
                          value={form.watch('customUrl')}
                          onChange={(e) => form.setValue('customUrl', e.target.value)}
                        />
                        <Button 
                          type="button" 
                          onClick={() => applyUrlChange(form.watch('customUrl'))}
                          size="sm"
                          disabled={isLoadingMetadata}
                        >
                          {isLoadingMetadata ? (
                            <RefreshCw className="h-4 w-4 animate-spin mr-1" />
                          ) : (
                            <Eye className="h-4 w-4 mr-1" />
                          )}
                          Apply
                        </Button>
                      </div>
                    </div>

                    <div className="grid grid-cols-2 gap-4">
                      <FormField
                        control={form.control}
                        name="showMode"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>Display Mode</FormLabel>
                            <FormControl>
                              <Select 
                                value={field.value} 
                                onValueChange={(value: ShowMode) => {
                                  field.onChange(value);
                                  form.handleSubmit(applySettings)();
                                }}
                              >
                                <SelectTrigger>
                                  <SelectValue placeholder="Select display mode" />
                                </SelectTrigger>
                                <SelectContent>
                                  <SelectItem value="fit">Fit</SelectItem>
                                  <SelectItem value="fill">Fill</SelectItem>
                                  <SelectItem value="actual">Actual Size</SelectItem>
                                  <SelectItem value="stretch">Stretch</SelectItem>
                                </SelectContent>
                              </Select>
                            </FormControl>
                          </FormItem>
                        )}
                      />

                      <FormField
                        control={form.control}
                        name="position"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>Position</FormLabel>
                            <FormControl>
                              <Select 
                                value={field.value} 
                                onValueChange={(value: PositionMode) => {
                                  field.onChange(value);
                                  form.handleSubmit(applySettings)();
                                }}
                              >
                                <SelectTrigger>
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
                            </FormControl>
                          </FormItem>
                        )}
                      />
                    </div>

                    <div className="grid grid-cols-2 gap-4">
                      <FormField
                        control={form.control}
                        name="refreshInterval"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>Refresh Interval: {field.value}s</FormLabel>
                            <FormControl>
                              <Slider 
                                value={[field.value]} 
                                min={1} 
                                max={30} 
                                step={1}
                                onValueChange={(values) => {
                                  field.onChange(values[0]);
                                  form.handleSubmit(applySettings)();
                                }}
                              />
                            </FormControl>
                          </FormItem>
                        )}
                      />

                      <FormField
                        control={form.control}
                        name="transition"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>Transition Effect</FormLabel>
                            <FormControl>
                              <Select 
                                value={field.value} 
                                onValueChange={(value: TransitionType) => {
                                  field.onChange(value);
                                  form.handleSubmit(applySettings)();
                                }}
                              >
                                <SelectTrigger>
                                  <SelectValue placeholder="Select transition effect" />
                                </SelectTrigger>
                                <SelectContent>
                                  <SelectItem value="cut">Cut (Immediate)</SelectItem>
                                  <SelectItem value="fade-fast">Fade (1 second)</SelectItem>
                                  <SelectItem value="fade-slow">Fade (10 seconds)</SelectItem>
                                </SelectContent>
                              </Select>
                            </FormControl>
                          </FormItem>
                        )}
                      />
                    </div>

                    <FormField
                      control={form.control}
                      name="backgroundColor"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Background Color</FormLabel>
                          <div className="space-y-2">
                            <div className="flex items-center space-x-2">
                              <div 
                                className="w-8 h-8 border border-gray-300 rounded"
                                style={{ backgroundColor: `#${field.value}` }}
                              />
                              <FormControl>
                                <Input 
                                  placeholder="Hex color (without #)" 
                                  value={field.value}
                                  onChange={(e) => {
                                    field.onChange(e.target.value);
                                  }}
                                  onBlur={() => form.handleSubmit(applySettings)()}
                                  maxLength={6}
                                  className="w-32"
                                />
                              </FormControl>
                            </div>
                            <div className="flex flex-wrap gap-2 pt-2">
                              {PRESET_COLORS.map(color => (
                                <div
                                  key={color}
                                  className="w-6 h-6 rounded cursor-pointer border border-gray-300"
                                  style={{ backgroundColor: `#${color}` }}
                                  onClick={() => {
                                    field.onChange(color);
                                    form.handleSubmit(applySettings)();
                                  }}
                                  title={`#${color}`}
                                />
                              ))}
                            </div>
                          </div>
                        </FormItem>
                      )}
                    />

                    <Separator className="my-1" />
                    
                    <div className="grid grid-cols-1 gap-4">
                      <FormField
                        control={form.control}
                        name="data"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>Metadata Display</FormLabel>
                            <FormControl>
                              <Input 
                                placeholder="Leave empty for all metadata or enter tag name" 
                                value={field.value || ''}
                                onChange={(e) => field.onChange(e.target.value)}
                                onBlur={() => form.handleSubmit(applySettings)()}
                              />
                            </FormControl>
                          </FormItem>
                        )}
                      />
                    </div>

                    <Separator className="my-1" />

                    <div className="space-y-4">
                      <FormField
                        control={form.control}
                        name="caption"
                        render={({ field }) => (
                          <FormItem>
                            <div className="flex justify-between">
                              <FormLabel>Caption Text</FormLabel>
                              <Button 
                                type="button" 
                                variant="outline" 
                                size="xs" 
                                onClick={useAllMetadata}
                                className="h-6 text-xs"
                              >
                                Use All Metadata
                              </Button>
                            </div>
                            <FormControl>
                              <Input 
                                placeholder="Enter caption text (use {key} for metadata or {all} for all)" 
                                value={field.value || ''}
                                onChange={(e) => field.onChange(e.target.value)}
                                onBlur={() => form.handleSubmit(applySettings)()}
                              />
                            </FormControl>
                          </FormItem>
                        )}
                      />

                      {form.watch('caption') && (
                        <div className="grid grid-cols-2 gap-4">
                          <FormField
                            control={form.control}
                            name="captionPosition"
                            render={({ field }) => (
                              <FormItem>
                                <FormLabel>Caption Position</FormLabel>
                                <FormControl>
                                  <Select 
                                    value={field.value} 
                                    onValueChange={(value: CaptionPosition) => {
                                      field.onChange(value);
                                      form.handleSubmit(applySettings)();
                                    }}
                                  >
                                    <SelectTrigger>
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
                                </FormControl>
                              </FormItem>
                            )}
                          />

                          <FormField
                            control={form.control}
                            name="captionSize"
                            render={({ field }) => (
                              <FormItem>
                                <FormLabel>Caption Size</FormLabel>
                                <FormControl>
                                  <Input 
                                    placeholder="Font size (e.g., 16px)" 
                                    value={field.value}
                                    onChange={(e) => field.onChange(e.target.value)}
                                    onBlur={() => form.handleSubmit(applySettings)()}
                                  />
                                </FormControl>
                              </FormItem>
                            )}
                          />
                        </div>
                      )}

                      {form.watch('caption') && (
                        <div className="grid grid-cols-2 gap-4">
                          <FormField
                            control={form.control}
                            name="captionColor"
                            render={({ field }) => (
                              <FormItem>
                                <FormLabel>Caption Color</FormLabel>
                                <div className="space-y-2">
                                  <div className="flex items-center space-x-2">
                                    <div 
                                      className="w-8 h-8 border border-gray-300 rounded"
                                      style={{ backgroundColor: `#${field.value}` }}
                                    />
                                    <FormControl>
                                      <Input 
                                        placeholder="Hex color (without #)" 
                                        value={field.value}
                                        onChange={(e) => field.onChange(e.target.value)}
                                        onBlur={() => form.handleSubmit(applySettings)()}
                                        maxLength={6}
                                        className="w-32"
                                      />
                                    </FormControl>
                                  </div>
                                  <div className="flex flex-wrap gap-2 pt-2">
                                    {PRESET_COLORS.map(color => (
                                      <div
                                        key={color}
                                        className="w-6 h-6 rounded cursor-pointer border border-gray-300"
                                        style={{ backgroundColor: `#${color}` }}
                                        onClick={() => {
                                          field.onChange(color);
                                          form.handleSubmit(applySettings)();
                                        }}
                                        title={`#${color}`}
                                      />
                                    ))}
                                  </div>
                                </div>
                              </FormItem>
                            )}
                          />

                          <FormField
                            control={form.control}
                            name="captionFont"
                            render={({ field }) => (
                              <FormItem>
                                <FormLabel>Caption Font</FormLabel>
                                <FormControl>
                                  <Select 
                                    value={field.value} 
                                    onValueChange={(value: string) => {
                                      field.onChange(value);
                                      form.handleSubmit(applySettings)();
                                    }}
                                  >
                                    <SelectTrigger>
                                      <SelectValue placeholder="Select font" />
                                    </SelectTrigger>
                                    <SelectContent>
                                      {CAPTION_FONTS.map(font => (
                                        <SelectItem key={font} value={font}>
                                          <span style={{ fontFamily: font }}>{font.split(',')[0]}</span>
                                        </SelectItem>
                                      ))}
                                    </SelectContent>
                                  </Select>
                                </FormControl>
                              </FormItem>
                            )}
                          />
                        </div>
                      )}
                    </div>
                  </div>
                </form>
              </Form>

              {/* URL Parameters box */}
              <div className="mt-4">
                <h3 className="text-sm font-medium mb-1">URL Parameters</h3>
                <div className="bg-slate-100 dark:bg-slate-800 p-2 rounded-md text-xs font-mono overflow-x-auto">
                  {params.output ? createUrlWithParams(params).replace('/display?', '') : 'Configure display settings to generate URL parameters'}
                </div>
                {params.output && (
                  <Button 
                    variant="outline" 
                    size="sm" 
                    className="mt-2"
                    onClick={() => {
                      navigator.clipboard.writeText(window.location.origin + createUrlWithParams(params));
                      toast.success("URL copied to clipboard");
                    }}
                  >
                    <Copy className="h-3 w-3 mr-1" />
                    Copy Full URL
                  </Button>
                )}
              </div>
            </TabsContent>

            <TabsContent value="files" className="pt-2 px-4 m-0">
              <div className="mb-2 flex items-center justify-between">
                <h3 className="font-medium">Available Images</h3>
                <Badge variant="outline">{outputFiles.length} files</Badge>
              </div>
              
              <div className="border border-gray-200 dark:border-gray-700 rounded-md overflow-hidden">
                {outputFiles.length > 0 ? (
                  <div className="grid grid-cols-1 divide-y divide-gray-200 dark:divide-gray-700">
                    {outputFiles.map((file, index) => (
                      <div key={index} className="hover:bg-gray-50 dark:hover:bg-gray-800 p-2">
                        <button 
                          className="text-blue-500 dark:text-blue-400 hover:underline text-left w-full flex items-center"
                          onClick={() => selectOutputFile(file)}
                        >
                          <span className="truncate">{file}</span>
                        </button>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="p-4 text-center text-gray-500">
                    <p>No output files found</p>
                    <p className="text-xs mt-1">Files should be in the /output directory</p>
                  </div>
                )}
              </div>
            </TabsContent>

            <TabsContent value="metadata" className="pt-2 px-4 m-0">
              <div className="mb-2 flex items-center justify-between">
                <h3 className="font-medium">Image Metadata</h3>
                <div className="flex items-center space-x-2">
                  <Badge variant="outline">{Object.keys(metadataPreview).length} fields</Badge>
                </div>
              </div>
              
              {Object.keys(metadataPreview).length > 0 ? (
                <div className="border border-gray-200 dark:border-gray-700 rounded-md overflow-hidden">
                  <div className="grid grid-cols-1 divide-y divide-gray-200 dark:divide-gray-700">
                    {Object.entries(metadataPreview).map(([key, value], index) => (
                      <div key={index} className="p-2 flex justify-between items-center hover:bg-gray-50 dark:hover:bg-gray-800">
                        <div className="flex-1 mr-2">
                          <div className="font-medium text-gray-700 dark:text-gray-300">{key}</div>
                          <div className="text-gray-600 dark:text-gray-400 text-xs truncate max-w-72">{value}</div>
                        </div>
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => copyMetadataTag(key)}
                          title={`Add {${key}} to caption`}
                        >
                          <Code className="h-3 w-3 mr-1" />
                          Use
                        </Button>
                      </div>
                    ))}
                  </div>
                </div>
              ) : (
                <Alert>
                  <Info className="h-4 w-4" />
                  <AlertTitle>No Metadata Available</AlertTitle>
                  <AlertDescription>
                    Apply a URL to load its metadata, or enter an image path and click Apply.
                  </AlertDescription>
                </Alert>
              )}
              
              <div className="mt-4 flex space-x-2">
                <Button 
                  onClick={useAllMetadata} 
                  variant="outline"
                  disabled={Object.keys(metadataPreview).length === 0}
                >
                  <Code className="h-4 w-4 mr-1" />
                  Use All Metadata
                </Button>
                
                {imageUrl && (
                  <Button 
                    onClick={async () => {
                      setIsLoadingMetadata(true);
                      try {
                        const metadata = await extractImageMetadata(imageUrl, '');
                        setMetadataPreview(metadata);
                        toast.success("Metadata refreshed");
                      } catch (error) {
                        toast.error("Failed to refresh metadata");
                      } finally {
                        setIsLoadingMetadata(false);
                      }
                    }}
                    variant="outline"
                    disabled={isLoadingMetadata}
                  >
                    {isLoadingMetadata ? (
                      <RefreshCw className="h-4 w-4 animate-spin mr-1" />
                    ) : (
                      <RefreshCw className="h-4 w-4 mr-1" />
                    )}
                    Refresh
                  </Button>
                )}
              </div>
            </TabsContent>

            <TabsContent value="info" className="pt-2 px-4 m-0">
              <div className="mb-2">
                <h3 className="font-medium">Image Status Information</h3>
              </div>
              
              <div className="space-y-2">
                <div className="grid grid-cols-2 gap-1 text-sm">
                  <div className="font-medium">Output Source:</div>
                  <div className="truncate">{params.output || 'Not set'}</div>
                  
                  <div className="font-medium">Last Modified:</div>
                  <div>{lastModified || 'Unknown'}</div>
                  
                  <div className="font-medium">Last Checked:</div>
                  <div>{formatDateTime(lastChecked)}</div>
                  
                  <div className="font-medium">Next Check At:</div>
                  <div>{nextCheckTime}</div>
                  
                  <div className="font-medium">Status:</div>
                  <div>
                    {imageChanged ? (
                      <span className="text-amber-600 font-medium flex items-center">
                        <RefreshCw className="h-3 w-3 mr-1 animate-spin" />
                        Image has changed since last load
                      </span>
                    ) : (
                      <span className="text-green-600 flex items-center">
                        <span className="h-2 w-2 bg-green-500 rounded-full mr-1"></span>
                        Up to date
                      </span>
                    )}
                  </div>
                </div>
                
                <div className="mt-4">
                  <Button 
                    onClick={onCheckNow} 
                    variant="outline" 
                    disabled={!imageUrl}
                    className="w-full"
                  >
                    <RefreshCw className="h-4 w-4 mr-2" />
                    Check for Updates Now
                  </Button>
                </div>
              </div>
            </TabsContent>
          </ScrollArea>
        </Tabs>
      </CardContent>
    </Card>
  );
};
