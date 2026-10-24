import React, { useRef, useState, useEffect } from 'react';
import * as LucideIcons from 'lucide-react';
import { XCircle, Wand2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
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
    description: 'Picks the best finish for your prompt and style engine',
    icon: 'Zap'
  };

  // Find selected refiner or auto option
  const selectedRefinerObj = selectedRefiner === 'auto' 
    ? autoOption 
    : refinersData.find(r => r.id === selectedRefiner);

  const getRefinerIcon = (iconName?: string) => {
    if (!iconName) return <XCircle className="h-5 w-5" />;
    if (iconName === 'Zap') return <Wand2 className="h-4 w-4" />;
    const IconComponent = (LucideIcons as any)[iconName];
    return IconComponent ? <IconComponent className="h-5 w-5" /> : <XCircle className="h-5 w-5" />;
  };

  const handleSelectRefiner = (refinerId: string) => {
    onRefinerChange(refinerId);
    setIsOpen(false);
  };

  const renderRefinerMenu = () => (
    <div className="space-y-2">
      <h4 className="text-sm font-semibold">Finish</h4>
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
            <Wand2 className="h-5 w-5" />
          </div>
          <div className="flex flex-col items-start overflow-hidden">
            <span className="truncate w-full text-left text-sm font-medium">Auto</span>
            <span className="text-xs text-muted-foreground w-full text-left whitespace-normal">Picks the best finish for your prompt and style engine</span>
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

  // One tap-opened popover at every width (a hover card ignores touch); the chip says what it is in words.
  return (
    <div className="flex items-center">
      <Popover open={isOpen} onOpenChange={setIsOpen}>
        <PopoverTrigger asChild>
          <Button
            ref={buttonRef}
            variant="outline"
            className={`h-9 shrink-0 gap-1.5 rounded-full border-input bg-card px-3 text-sm font-normal [&_svg]:size-4 [&_svg]:shrink-0 ${isOpen ? 'bg-accent' : ''}`}
            type="button"
            aria-label={`Finish: ${selectedRefinerObj?.name || 'None'}. Choose`}
          >
            {getRefinerIcon(selectedRefinerObj?.icon)}
            <span className="text-muted-foreground">Finish:</span>
            <span className="min-w-0 max-w-[9rem] truncate font-medium">{selectedRefinerObj?.name || 'None'}</span>
          </Button>
        </PopoverTrigger>
        <PopoverContent
          align="start"
          side="bottom"
          sideOffset={6}
          collisionPadding={8}
          avoidCollisions
          className="w-72 p-2 overflow-y-auto max-h-[70vh]"
          style={{ maxHeight: maxContentHeight }}
        >
          {renderRefinerMenu()}
        </PopoverContent>
      </Popover>
    </div>
  );
};

export default RefinerSelector;
