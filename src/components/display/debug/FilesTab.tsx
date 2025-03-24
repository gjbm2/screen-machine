
import React from 'react';
import { CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { TooltipProvider, Tooltip, TooltipTrigger, TooltipContent } from "@/components/ui/tooltip";
import { FileImage, ImageIcon, Info, RefreshCw } from "lucide-react";

interface FilesTabProps {
  outputFiles: string[];
  imageChanged: boolean | undefined;
  imageUrl: string | null;
  customUrl: string;
  setCustomUrl: (url: string) => void;
  selectFile: (file: string) => void;
  isCurrentFile: (file: string) => boolean;
  formatFileName: (file: string) => string;
}

export const FilesTab: React.FC<FilesTabProps> = ({
  outputFiles,
  imageChanged,
  imageUrl,
  customUrl,
  setCustomUrl,
  selectFile,
  isCurrentFile,
  formatFileName
}) => {
  // Debug the current file selections
  React.useEffect(() => {
    console.log('[FilesTab] Current imageUrl:', imageUrl);
    console.log('[FilesTab] Available outputFiles:', outputFiles);
  }, [imageUrl, outputFiles]);

  // Direct URL handler - don't double encode external URLs
  const handleUseCustomUrl = () => {
    if (customUrl && customUrl.trim() !== '') {
      console.log('[FilesTab] Using custom URL:', customUrl);
      selectFile(customUrl);
    }
  };

  return (
    <CardContent className="pt-4 h-full flex flex-col">
      <div className="space-y-4 flex-1 flex flex-col">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-medium">Available Output Files</h3>
          {imageChanged && (
            <Badge variant="outline" className="bg-amber-50 text-amber-700 border-amber-200">
              <RefreshCw className="h-3 w-3 mr-1 animate-spin" />
              Updated
            </Badge>
          )}
        </div>
        
        <ScrollArea className="flex-1 rounded-md border p-2 min-h-[200px]">
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
                    <Badge variant="secondary">
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
        
        <div className="space-y-2 mt-auto">
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
              onClick={handleUseCustomUrl}
            >
              Use
            </Button>
          </div>
        </div>
      </div>
    </CardContent>
  );
};
