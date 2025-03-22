
import React, { useState } from 'react';
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
import { Info, RefreshCw } from "lucide-react";
import { Separator } from "@/components/ui/separator";
import { createUrlWithParams } from './utils';

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

  const applyUrlChange = (url: string) => {
    if (!url) {
      return;
    }
    
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

  return (
    <Card className="absolute top-4 left-4 z-10 w-auto min-w-96 max-w-[90vw] max-h-[90vh] overflow-auto bg-white/90 dark:bg-gray-800/90 shadow-lg">
      <CardHeader className="pb-2">
        <CardTitle className="text-lg">Debug Configuration</CardTitle>
      </CardHeader>
      <CardContent className="text-sm">
        {(!params.output || outputFiles.length === 0) && (
          <Alert className="mb-4">
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
        )}

        <Form {...form}>
          <form onSubmit={form.handleSubmit(applySettings)} className="space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="space-y-4">
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

                <div className="pt-2">
                  <FormLabel>Set Custom URL</FormLabel>
                  <div className="flex space-x-2 mt-1">
                    <Input 
                      placeholder="Enter full URL" 
                      value={form.watch('customUrl')}
                      onChange={(e) => form.setValue('customUrl', e.target.value)}
                    />
                    <Button 
                      type="button" 
                      onClick={() => applyUrlChange(form.watch('customUrl'))}
                      size="sm"
                    >
                      Apply
                    </Button>
                  </div>
                </div>

                <Separator className="my-2" />

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

                <Separator className="my-2" />

                <FormField
                  control={form.control}
                  name="caption"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Caption Text</FormLabel>
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
                  <>
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
                  </>
                )}
              </div>

              <div className="space-y-4">
                <div className="flex justify-between items-start">
                  <h3 className="font-bold mb-1">Current Settings:</h3>
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
                </div>
                <ul className="space-y-1 mb-3">
                  <li><strong>Output:</strong> {params.output || 'Not set'}</li>
                  <li><strong>Display Mode:</strong> {params.showMode}</li>
                  <li><strong>Position:</strong> {params.position}</li>
                  <li><strong>Refresh:</strong> {params.refreshInterval}s</li>
                  <li><strong>Background:</strong> #{params.backgroundColor}</li>
                  <li><strong>Transition:</strong> {params.transition}</li>
                  {params.caption && (
                    <>
                      <li><strong>Caption:</strong> {params.caption}</li>
                      <li><strong>Caption Position:</strong> {params.captionPosition}</li>
                      <li><strong>Caption Size:</strong> {params.captionSize}</li>
                      <li><strong>Caption Color:</strong> #{params.captionColor}</li>
                      <li><strong>Caption Font:</strong> {params.captionFont?.split(',')[0]}</li>
                    </>
                  )}
                  {params.data !== undefined && (
                    <li><strong>Metadata:</strong> {params.data || 'All metadata'}</li>
                  )}
                  <li><strong>Image Key:</strong> {imageKey}</li>
                  <li><strong>Last Modified:</strong> {lastModified || 'Unknown'}</li>
                  <li><strong>Last Checked:</strong> {formatDateTime(lastChecked)}</li>
                  <li><strong>Next Check At:</strong> {nextCheckTime}</li>
                  <li>
                    <strong>Status:</strong> 
                    {imageChanged ? (
                      <span className="text-amber-600 font-medium"> Image has changed since last load</span>
                    ) : (
                      <span className="text-green-600"> Up to date</span>
                    )}
                  </li>
                </ul>
                
                <Separator className="my-2" />
                
                <h3 className="font-bold mb-1">Available Images:</h3>
                <div className="max-h-60 overflow-y-auto border border-gray-200 dark:border-gray-700 rounded p-2">
                  {outputFiles.length > 0 ? (
                    <ul className="space-y-1">
                      {outputFiles.map((file, index) => (
                        <li key={index} className="hover:bg-gray-100 dark:hover:bg-gray-700 p-1 rounded">
                          <button 
                            className="text-blue-500 dark:text-blue-400 hover:underline text-left w-full"
                            onClick={() => selectOutputFile(file)}
                          >
                            {file}
                          </button>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="text-gray-500">Loading available files...</p>
                  )}
                </div>

                <div className="flex justify-between items-center mt-4">
                  {params.output ? (
                    <Button
                      type="button"
                      onClick={endDebugMode}
                      className="block text-center"
                    >
                      End Debug Mode
                    </Button>
                  ) : (
                    <div className="text-gray-500 text-sm italic">
                      Select an image to enable production mode
                    </div>
                  )}
                  
                  <div className="text-xs text-gray-500 mt-2">
                    {params.output && (
                      <div className="rounded bg-gray-100 p-2 text-gray-700 dark:bg-gray-700 dark:text-gray-300 font-mono text-[10px] break-all">
                        {createUrlWithParams(params).replace('/display?', '')}
                      </div>
                    )}
                  </div>
                </div>
              </div>
            </div>
          </form>
        </Form>
      </CardContent>
    </Card>
  );
};
