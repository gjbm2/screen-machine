
import React from 'react';
import { Sparkles, XCircle, Maximize, Heart, Palette } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card";
import { useIsMobile, useWindowSize } from '@/hooks/use-mobile';
import refinersData from '@/data/refiners.json';

interface RefinerSelectorProps {
  selectedRefiner: string;
  onRefinerChange: (refinerId: string) => void;
}

const RefinerSelector: React.FC<RefinerSelectorProps> = ({
  selectedRefiner,
  onRefinerChange,
}) => {
  const isMobile = useIsMobile();
  const { width } = useWindowSize();
  const isNarrow = width < 600;
  
  const selectedRefinerObj = refinersData.find(r => r.id === selectedRefiner);
  
  // Get the icon for a refiner
  const getRefinerIcon = (refinerId: string) => {
    switch (refinerId) {
      case 'enhance':
        return <Sparkles className="h-5 w-5" />;
      case 'upscale':
        return <Maximize className="h-5 w-5" />;
      case 'beautify':
        return <Heart className="h-5 w-5" />;
      case 'stylize':
        return <Palette className="h-5 w-5" />;
      case 'none':
      default:
        return <XCircle className="h-5 w-5" />;
    }
  };
  
  return (
    <div className="flex items-center h-[48px]">
      <HoverCard>
        <HoverCardTrigger asChild>
          <Button
            variant="outline"
            className="h-[36px] border border-input hover:bg-purple-500/10 text-purple-700"
            onClick={(e) => {
              // Prevent any default action or propagation
              e.preventDefault();
              e.stopPropagation();
            }}
            type="button"
          >
            {getRefinerIcon(selectedRefiner)}
            {!isNarrow && (
              <span className="ml-2 text-sm">{selectedRefinerObj?.name || 'No Refiner'}</span>
            )}
          </Button>
        </HoverCardTrigger>
        <HoverCardContent className="w-64 p-2">
          <div className="space-y-2">
            <h4 className="text-sm font-semibold">Refiners</h4>
            <div className="grid grid-cols-1 gap-1">
              {refinersData.map((refiner) => (
                <Button
                  key={refiner.id}
                  variant={refiner.id === selectedRefiner ? "secondary" : "ghost"}
                  size="sm"
                  className="justify-start text-sm h-9"
                  onClick={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    onRefinerChange(refiner.id);
                  }}
                  type="button"
                >
                  <div className="mr-2">
                    {getRefinerIcon(refiner.id)}
                  </div>
                  <div className="flex flex-col items-start">
                    <span>{refiner.name}</span>
                    <span className="text-xs text-muted-foreground">{refiner.description}</span>
                  </div>
                </Button>
              ))}
            </div>
          </div>
        </HoverCardContent>
      </HoverCard>
    </div>
  );
};

export default RefinerSelector;
