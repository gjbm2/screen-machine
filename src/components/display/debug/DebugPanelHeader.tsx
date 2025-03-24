
import React from 'react';
import { CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { RefreshCw, Copy, Clipboard, Check, Eye, RotateCcw, Settings } from "lucide-react";

interface DebugPanelHeaderProps {
  onCheckNow: () => void;
  copyUrl: () => void;
  resetDisplay: () => void;
  copied: boolean;
  lastChecked: Date | null;
  formatTime: (timeValue: Date | string | null) => string;
  togglePreview?: () => void;
  showingPreview?: boolean;
  isMobile?: boolean;
}

export const DebugPanelHeader: React.FC<DebugPanelHeaderProps> = ({
  onCheckNow,
  copyUrl,
  resetDisplay,
  copied,
  lastChecked,
  formatTime,
  togglePreview,
  showingPreview,
  isMobile
}) => {
  return (
    <CardHeader className="pb-2 flex flex-col gap-2 sticky top-0 bg-card z-30 border-b">
      <div className="flex justify-between items-center">
        <CardTitle className="text-lg flex items-center gap-2">
          <span>Display Configuration</span>
          <div className="text-xs text-muted-foreground flex items-center">
            <RefreshCw className="h-3 w-3 mr-1" />
            <span>Last checked: {formatTime(lastChecked)}</span>
          </div>
        </CardTitle>
        
        <div className="flex space-x-2">
          {/* Mobile switch view button - always in the same position */}
          {isMobile && togglePreview && (
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button 
                    variant="outline" 
                    size="sm"
                    onClick={togglePreview}
                    className="h-8 w-8 p-0"
                  >
                    {showingPreview ? <Settings className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </Button>
                </TooltipTrigger>
                <TooltipContent>
                  <p>{showingPreview ? "Show Settings" : "Show Preview"}</p>
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
          )}
          
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
                  {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
                </Button>
              </TooltipTrigger>
              <TooltipContent>
                <p>Copy display URL</p>
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
          
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button 
                  variant="outline" 
                  size="sm"
                  onClick={resetDisplay}
                  className="h-8 w-8 p-0"
                >
                  <RotateCcw className="h-4 w-4" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>
                <p>Reset display to defaults</p>
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
        </div>
      </div>
    </CardHeader>
  );
};
