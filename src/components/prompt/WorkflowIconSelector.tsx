import React, { useEffect, useRef, useState } from 'react';
import * as LucideIcons from 'lucide-react';
import { Image, Palette } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Popover,
  PopoverTrigger,
  PopoverContent,
} from "@/components/ui/popover";
import { useIsMobile, useWindowSize } from '@/hooks/use-mobile';
import { WorkflowProps } from '@/components/prompt-form/types';

interface WorkflowIconSelectorProps {
  workflows: WorkflowProps[];
  selectedWorkflow: string;
  onWorkflowChange: (workflowId: string) => void;
  hideWorkflowName?: boolean;
}

const WorkflowIconSelector: React.FC<WorkflowIconSelectorProps> = ({
  workflows,
  selectedWorkflow,
  onWorkflowChange,
  hideWorkflowName = false
}) => {
  const isMobile = useIsMobile();
  const { width, height: viewportHeight } = useWindowSize();
  const isNarrow = width < 600;
  const shouldHideName = hideWorkflowName || isNarrow;

  const [isOpen, setIsOpen] = useState(false);
  const [maxContentHeight, setMaxContentHeight] = useState<number | undefined>(undefined);
  const buttonRef = useRef<HTMLButtonElement>(null);

  // Create auto option
  const autoOption = {
    id: 'auto',
    name: 'Auto',
    description: 'Picks the best style engine for your prompt and finish',
    icon: 'Zap'
  };

  // Find selected workflow or auto option
  const selectedWorkflowObj = selectedWorkflow === 'auto' 
    ? autoOption 
    : workflows.find(w => w.id === selectedWorkflow);

  const getWorkflowIcon = (iconName?: string) => {
    if (!iconName) return <Image className="h-5 w-5" />;
    if (iconName === 'Zap') return <Palette className="h-4 w-4" />;
    const IconComponent = (LucideIcons as any)[iconName];
    return IconComponent ? <IconComponent className="h-5 w-5" /> : <Image className="h-5 w-5" />;
  };

  const displayName = selectedWorkflowObj?.name || 'Workflow';
  const displayIcon = getWorkflowIcon(selectedWorkflowObj?.icon);

  const handleSelectWorkflow = (workflowId: string) => {
    onWorkflowChange(workflowId);
    setIsOpen(false);
  };

  const renderMenuContent = () => (
    <div className="space-y-2">
      <h4 className="text-sm font-semibold">Style engine</h4>
      <div className="grid grid-cols-1 gap-1">
        {/* Auto option first */}
        <Button
          key="auto"
          variant={selectedWorkflow === 'auto' ? "secondary" : "ghost"}
          size="sm"
          className="justify-start text-sm h-auto py-2"
          onClick={() => handleSelectWorkflow('auto')}
          type="button"
        >
          <div className="mr-2 flex-shrink-0">
            <Palette className="h-5 w-5" />
          </div>
          <div className="flex flex-col items-start overflow-hidden">
            <span className="truncate w-full text-left text-sm font-medium">Auto</span>
            <span className="text-xs text-muted-foreground w-full text-left whitespace-normal">Picks the best style engine for your prompt and finish</span>
          </div>
        </Button>
        
        {/* Separator */}
        <div className="border-t border-border my-1" />
        
        {/* Regular workflows */}
        {workflows.map((workflow) => (
          <Button
            key={workflow.id}
            variant={workflow.id === selectedWorkflow ? "secondary" : "ghost"}
            size="sm"
            className="justify-start text-sm h-auto py-2"
            onClick={() => handleSelectWorkflow(workflow.id)}
            type="button"
          >
            <div className="mr-2 flex-shrink-0">
              {getWorkflowIcon(workflow.icon)}
            </div>
            <div className="flex flex-col items-start overflow-hidden">
              <span className="truncate w-full text-left text-sm">{workflow.name}</span>
              <span className="text-xs text-muted-foreground w-full text-left whitespace-normal">{workflow.description}</span>
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
            aria-label={`Style engine: ${selectedWorkflow === 'auto' ? 'Auto' : displayName}. Choose`}
          >
            {displayIcon}
            <span className="text-muted-foreground">Style engine:</span>
            <span className="min-w-0 max-w-[9rem] truncate font-medium">{selectedWorkflow === 'auto' ? 'Auto' : displayName}</span>
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
          {renderMenuContent()}
        </PopoverContent>
      </Popover>
    </div>
  );
};

export default WorkflowIconSelector;
