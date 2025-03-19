
import React from 'react';
import { 
  Sparkles, 
  Maximize, 
  Heart, 
  Palette, 
  XCircle,
  Filter 
} from 'lucide-react';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { Button } from '@/components/ui/button';
import refinersData from '@/data/refiners.json';
import { useWindowSize } from '@/hooks/use-mobile';

interface RefinerSelectorProps {
  selectedRefiner: string;
  onRefinerChange: (refinerId: string) => void;
}

const RefinerSelector: React.FC<RefinerSelectorProps> = ({
  selectedRefiner,
  onRefinerChange,
}) => {
  const { width } = useWindowSize();
  const showName = width >= 768; // Show name on medium screens and up
  
  const getRefinerIcon = (iconName: string) => {
    switch (iconName) {
      case 'sparkles':
        return <Sparkles className="h-4 w-4 mr-2" />;
      case 'maximize':
        return <Maximize className="h-4 w-4 mr-2" />;
      case 'heart':
        return <Heart className="h-4 w-4 mr-2" />;
      case 'palette':
        return <Palette className="h-4 w-4 mr-2" />;
      case 'x-circle':
        return <XCircle className="h-4 w-4 mr-2" />;
      default:
        return <Filter className="h-4 w-4 mr-2" />;
    }
  };

  // Find the current refiner
  const currentRefiner = refinersData.find(r => r.id === selectedRefiner) || refinersData[0];
  
  // Get the current refiner icon for the button
  const getCurrentRefinerIcon = () => {
    const iconName = currentRefiner?.icon || 'filter';
    switch (iconName) {
      case 'sparkles':
        return <Sparkles className="h-5 w-5" />;
      case 'maximize':
        return <Maximize className="h-5 w-5" />;
      case 'heart':
        return <Heart className="h-5 w-5" />;
      case 'palette':
        return <Palette className="h-5 w-5" />;
      case 'x-circle':
        return <XCircle className="h-5 w-5" />;
      default:
        return <Filter className="h-5 w-5" />;
    }
  };

  return (
    <TooltipProvider>
      <Tooltip delayDuration={300}>
        <DropdownMenu>
          <TooltipTrigger asChild>
            <DropdownMenuTrigger asChild>
              <Button
                variant="outline"
                className={`hover:bg-purple-500/10 text-purple-700 ${showName ? 'px-3' : ''}`}
                size={showName ? "default" : "icon"}
              >
                <div className="flex items-center">
                  {getCurrentRefinerIcon()}
                  {showName && (
                    <span className="ml-2 text-sm">{currentRefiner.name}</span>
                  )}
                </div>
              </Button>
            </DropdownMenuTrigger>
          </TooltipTrigger>
          <TooltipContent side="bottom">
            <p>Refiner: {currentRefiner.name}</p>
            <p className="text-xs text-muted-foreground">Select an image refiner</p>
          </TooltipContent>
          <DropdownMenuContent align="end" alignOffset={-5} sideOffset={5} className="bg-background/90 backdrop-blur-sm">
            {refinersData.map((refiner) => (
              <DropdownMenuItem
                key={refiner.id}
                onClick={() => onRefinerChange(refiner.id)}
                className="cursor-pointer"
              >
                {getRefinerIcon(refiner.icon)}
                <div>
                  <p>{refiner.name}</p>
                  <p className="text-xs text-muted-foreground">{refiner.description}</p>
                </div>
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      </Tooltip>
    </TooltipProvider>
  );
};

export default RefinerSelector;
