
import React from 'react';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { LayoutGrid, Grid, List } from 'lucide-react';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { ViewMode } from './ImageDisplay';

interface ViewModeSelectorProps {
  viewMode: ViewMode;
  onViewModeChange: (value: string) => void;
}

const ViewModeSelector: React.FC<ViewModeSelectorProps> = ({
  viewMode,
  onViewModeChange
}) => {
  return (
    <Tabs 
      defaultValue="normal" 
      value={viewMode} 
      onValueChange={onViewModeChange}
      className="w-auto"
    >
      <TabsList className="grid grid-cols-3 h-8 w-auto">
        <Tooltip>
          <TooltipTrigger asChild>
            <TabsTrigger value="normal" className="px-1.5 sm:px-2">
              <LayoutGrid className="h-4 w-4" />
            </TabsTrigger>
          </TooltipTrigger>
          <TooltipContent>Normal View</TooltipContent>
        </Tooltip>
        
        <Tooltip>
          <TooltipTrigger asChild>
            <TabsTrigger value="small" className="px-1.5 sm:px-2">
              <Grid className="h-4 w-4" />
            </TabsTrigger>
          </TooltipTrigger>
          <TooltipContent>Small View</TooltipContent>
        </Tooltip>
        
        <Tooltip>
          <TooltipTrigger asChild>
            <TabsTrigger value="table" className="px-1.5 sm:px-2">
              <List className="h-4 w-4" />
            </TabsTrigger>
          </TooltipTrigger>
          <TooltipContent>Table View</TooltipContent>
        </Tooltip>
      </TabsList>
    </Tabs>
  );
};

export default ViewModeSelector;
