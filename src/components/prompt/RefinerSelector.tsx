import React, { useRef, useState, useEffect } from 'react';
import * as LucideIcons from 'lucide-react';
import { XCircle, Zap } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from '@/components/ui/hover-card';
import {
  Popover,
  PopoverTrigger,
  PopoverContent,
} from '@/components/ui/popover';
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
  const { width, height: viewportHeight } = useWindowSize();
  const isNarrow = width < 600;

  const [isOpen, setIsOpen] = useState(false);
  const [maxContentHeight, setMaxContentHeight] = useState<number | undefined>(undefined);

  const buttonRef = useRef<HTMLButtonElement>(null);

  // Create auto option
  const autoOption = {
    id: 'auto',
    name: 'Auto',
    description: 'Automatically select the best refiner based on your workflow and prompt',
    icon: 'Zap'
  };

  // Find selected refiner or auto option
  const selectedRefinerObj = selectedRefiner === 'auto' 
    ? autoOption 
    : refinersData.find(r => r.id === selectedRefiner);

  const getRefinerIcon = (iconName?: string) => {
    if (!iconName) return <XCircle className="h-5 w-5" />;
    if (iconName === 'Zap') return <Zap className="h-5 w-5" />;
    const IconComponent = (LucideIcons as any)[iconName];
    return IconComponent ? <IconComponent className="h-5 w-5" /> : <XCircle className="h-5 w-5" />;
  };

  const handleSelectRefiner = (refinerId: string) => {
    onRefinerChange(refinerId);
    setIsOpen(false);
  };

  const renderRefinerMenu = () => (
    <div className="space-y-2">
      <h4 className="text-sm font-semibold">Refiners</h4>
      <div className="grid grid-cols-1 gap-1">
        {/* Auto option first */}
        <Button
          key="auto"
          variant={selectedRefiner === 'auto' ? "secondary" : "ghost"}
          size="sm"
          className="justify-start text-sm h-auto py-2"
          onClick={() => handleSelectRefiner('auto')}
          type="button"
        >
          <div className="mr-2 flex-shrink-0">
            <Zap className="h-5 w-5" />
          </div>
          <div className="flex flex-col items-start overflow-hidden">
            <span className="truncate w-full text-left text-sm font-medium">Auto</span>
            <span className="text-xs text-muted-foreground w-full text-left whitespace-normal">Automatically select the best refiner based on your workflow and prompt</span>
          </div>
        </Button>
        
        {/* Separator */}
        <div className="border-t border-border my-1" />
        
        {/* Regular refiners */}
        {refinersData.map((refiner) => (
          <Button
            key={refiner.id}
            variant={refiner.id === selectedRefiner ? "secondary" : "ghost"}
            size="sm"
            className="justify-start text-sm h-auto py-2"
            onClick={() => handleSelectRefiner(refiner.id)}
            type="button"
          >
            <div className="mr-2 flex-shrink-0">
              {getRefinerIcon(refiner.icon)}
            </div>
            <div className="flex flex-col items-start overflow-hidden">
              <span className="truncate w-full text-left text-sm">{refiner.name}</span>
              <span className="text-xs text-muted-foreground w-full text-left whitespace-normal">{refiner.description}</span>
            </div>
          </Button>
        ))}
      </div>
    </div>
  );

  useEffect(() => {
    if (isMobile && isOpen && buttonRef.current) {
      const rect = buttonRef.current.getBoundingClientRect();
      const spaceBelow = viewportHeight - rect.bottom - 16;
      const maxHeight = Math.max(spaceBelow, 150);
      setMaxContentHeight(maxHeight);
    }
  }, [isOpen, isMobile, viewportHeight]);

  return (
    <div className="flex items-center h-[48px]">
      {isMobile ? (
        <Popover open={isOpen} onOpenChange={setIsOpen}>
          <PopoverTrigger asChild>
            <Button
              ref={buttonRef}
              variant="outline"
              className="h-[36px] border border-input hover:bg-purple-500/10 text-purple-700 flex items-center px-3 text-sm"
              type="button"
              onClick={() => setIsOpen(prev => !prev)}
            >
              {getRefinerIcon(selectedRefinerObj?.icon)}
              {!isNarrow && (
                <span className="ml-2 text-sm truncate max-w-[80px]">
                  {selectedRefinerObj?.name || 'No Refiner'}
                </span>
              )}
            </Button>
          </PopoverTrigger>
          <PopoverContent
            align="start"
            side="bottom"
            sideOffset={8}
            collisionPadding={8}
            avoidCollisions
            className="w-64 p-2 overflow-y-auto"
            style={{ maxHeight: maxContentHeight }}
          >
            {renderRefinerMenu()}
          </PopoverContent>
        </Popover>
      ) : (
        <HoverCard open={isOpen} onOpenChange={setIsOpen} openDelay={100} closeDelay={100}>
          <HoverCardTrigger asChild>
            <Button
              ref={buttonRef}
              variant="outline"
              className="h-[36px] border border-input hover:bg-purple-500/10 text-purple-700 flex items-center px-3 text-sm"
              type="button"
              onClick={() => setIsOpen(prev => !prev)}
            >
              {getRefinerIcon(selectedRefinerObj?.icon)}
              {!isNarrow && (
                <span className="ml-2 text-sm truncate max-w-[80px]">
                  {selectedRefinerObj?.name || 'No Refiner'}
                </span>
              )}
            </Button>
          </HoverCardTrigger>
          <HoverCardContent
            align="start"
            side="bottom"
            sideOffset={4}
            collisionPadding={8}
            avoidCollisions
            className="w-64 p-2 overflow-y-auto max-h-[60vh]"
          >
            {renderRefinerMenu()}
          </HoverCardContent>
        </HoverCard>
      )}
    </div>
  );
};

export default RefinerSelector;
