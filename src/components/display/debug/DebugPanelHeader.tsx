
import React from 'react';
import { CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { RefreshCw, Copy, Check, Eye, Trash2, Settings, Loader2 } from "lucide-react";

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
  isTopFixed?: boolean;
  isChecking?: boolean;
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
  isMobile,
  isTopFixed = true,
  isChecking = false
}) => {
  return (
    <CardHeader className={`pb-2 flex flex-col gap-2 bg-card z-30 border-b ${
      isTopFixed ? 'sticky top-0' : ''
    }`}>
      <div className="flex justify-between items-center">
        <CardTitle className="text-lg flex items-center gap-2">
          <span>Display Configuration</span>
        </CardTitle>
        
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
      </div>
      
      <div className="flex justify-between items-center text-sm">
        <div className="flex items-center gap-1 text-muted-foreground">
          {isChecking ? (
            <>
              <Loader2 className="h-3 w-3 mr-1 animate-spin" />
              <span>Checking for updates...</span>
            </>
          ) : (
            <>
              <RefreshCw className="h-3 w-3 mr-1" />
              <span>Last checked: {formatTime(lastChecked)}</span>
            </>
          )}
          
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button 
                  variant="ghost" 
                  size="icon"
                  onClick={onCheckNow}
                  className="h-6 w-6 ml-1"
                  disabled={isChecking}
                >
                  <RefreshCw className={`h-3 w-3 ${isChecking ? 'animate-spin' : ''}`} />
                </Button>
              </TooltipTrigger>
              <TooltipContent>
                <p>Check for updates now</p>
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
        </div>
        
        <div className="flex space-x-2">
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button 
                  variant="outline" 
                  size="sm"
                  onClick={copyUrl}
                  className="h-7 w-7 p-0"
                >
                  {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
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
                  className="h-7 w-7 p-0 text-destructive hover:bg-destructive/10 hover:text-destructive"
                >
                  <Trash2 className="h-3.5 w-3.5" />
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
