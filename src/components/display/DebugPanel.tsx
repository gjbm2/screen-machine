
import React, { useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { DisplayParams, ShowMode } from './types';
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

interface DebugPanelProps {
  params: DisplayParams;
  imageUrl: string | null;
  lastModified: string | null;
  lastChecked: Date | null;
  nextCheckTime: string;
  imageKey: number;
  outputFiles: string[];
}

export const formatDateTime = (date: Date | null) => {
  if (!date) return 'N/A';
  return date.toLocaleTimeString();
};

interface DebugFormValues {
  output: string;
  showMode: ShowMode;
  refreshInterval: number;
  backgroundColor: string;
  customUrl: string;
}

const PRESET_COLORS = [
  '000000', '333333', '555555', '777777', 
  '0A1172', '1C39BB', '3944BC', '7B68EE',
  '008000', '228B22', '2E8B57', '32CD32',
  '800000', 'A52A2A', 'B22222', 'CD5C5C', 
  'FFFFFF', 'F5F5F5', 'E8E8E8', 'D3D3D3'
];

export const DebugPanel: React.FC<DebugPanelProps> = ({
  params,
  imageUrl,
  lastModified,
  lastChecked,
  nextCheckTime,
  imageKey,
  outputFiles
}) => {
  const navigate = useNavigate();
  const [selectedColor, setSelectedColor] = useState(params.backgroundColor);
  
  const form = useForm<DebugFormValues>({
    defaultValues: {
      output: params.output || '',
      showMode: params.showMode,
      refreshInterval: params.refreshInterval,
      backgroundColor: params.backgroundColor,
      customUrl: ''
    }
  });

  const applySettings = (values: DebugFormValues) => {
    const outputParam = values.output || params.output;
    
    if (!outputParam) {
      alert("Output parameter is required");
      return;
    }
    
    const queryParams = new URLSearchParams();
    queryParams.set('output', outputParam);
    queryParams.set('show', values.showMode);
    queryParams.set('refresh', values.refreshInterval.toString());
    queryParams.set('background', values.backgroundColor);
    queryParams.set('debug', 'true');
    
    navigate(`/display?${queryParams.toString()}`);
  };

  const applyUrlChange = (url: string) => {
    if (!url) {
      alert("URL is required");
      return;
    }
    
    const queryParams = new URLSearchParams();
    queryParams.set('output', url);
    queryParams.set('show', params.showMode);
    queryParams.set('refresh', params.refreshInterval.toString());
    queryParams.set('background', params.backgroundColor);
    queryParams.set('debug', 'true');
    
    navigate(`/display?${queryParams.toString()}`);
  };

  const endDebugMode = () => {
    const queryParams = new URLSearchParams();
    if (params.output) queryParams.set('output', params.output);
    queryParams.set('show', params.showMode);
    queryParams.set('refresh', params.refreshInterval.toString());
    queryParams.set('background', params.backgroundColor);
    
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
              </div>

              <div className="space-y-4">
                <h3 className="font-bold mb-1">Current Settings:</h3>
                <ul className="space-y-1 mb-3">
                  <li><strong>Output:</strong> {params.output}</li>
                  <li><strong>Display Mode:</strong> {params.showMode}</li>
                  <li><strong>Refresh:</strong> {params.refreshInterval}s</li>
                  <li><strong>Background:</strong> #{params.backgroundColor}</li>
                  <li><strong>Image Key:</strong> {imageKey}</li>
                  <li><strong>Last Modified:</strong> {lastModified || 'Unknown'}</li>
                  <li><strong>Last Checked:</strong> {formatDateTime(lastChecked)}</li>
                  <li><strong>Next Check At:</strong> {nextCheckTime}</li>
                </ul>
                
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
                    <p className="text-gray-500">No files found</p>
                  )}
                </div>

                <a 
                  href={`/display?output=${params.output || ''}&show=${params.showMode}&refresh=${params.refreshInterval}&background=${params.backgroundColor}`}
                  className="block text-center bg-blue-500 hover:bg-blue-600 text-white py-2 px-4 rounded mt-4"
                >
                  End Debug Mode
                </a>
              </div>
            </div>
          </form>
        </Form>
      </CardContent>
    </Card>
  );
};
