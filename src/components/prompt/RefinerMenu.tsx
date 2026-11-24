// The refiner list (how the prompt is rewritten first), shown in the toolbar's one "Workflow and refiner" button
// (MakeSelector.tsx; owner, 24 Nov).
import React from 'react';
import * as LucideIcons from 'lucide-react';
import { XCircle, Zap } from 'lucide-react';
import { Button } from '@/components/ui/button';
import refinersData from '@/data/refiners.json';

export const AUTO_REFINER = {
  id: 'auto',
  name: 'Auto',
  description: 'Automatically select the best refiner based on your workflow and prompt',
  icon: 'Zap'
};

export const findRefiner = (id: string) => (id === 'auto' ? AUTO_REFINER : refinersData.find(r => r.id === id));

export const refinerIcon = (iconName?: string, className = 'h-5 w-5') => {
  if (!iconName) return <XCircle className={className} />;
  if (iconName === 'Zap') return <Zap className={className} />;
  const IconComponent = (LucideIcons as any)[iconName];
  return IconComponent ? <IconComponent className={className} /> : <XCircle className={className} />;
};

export const RefinerMenu: React.FC<{
  selectedRefiner: string;
  onPick: (refinerId: string) => void;
  /** Only these (after Auto) make sense here (Film: with its own helper, or none). */
  only?: string[];
}> = ({ selectedRefiner, onPick, only }) => {
  const getRefinerIcon = (iconName?: string) => refinerIcon(iconName);
  const handleSelectRefiner = onPick;

  return (
    <div className="space-y-2">
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
        {refinersData.filter((refiner) => !(refiner as { hidden?: boolean }).hidden && (!only || only.includes(refiner.id))).map((refiner) => (
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
};

export default RefinerMenu;
