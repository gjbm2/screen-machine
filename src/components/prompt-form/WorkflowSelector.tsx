
import React from 'react';
import { Button } from '@/components/ui/button';
import { Check, ChevronDown, Zap } from 'lucide-react';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';

interface WorkflowSelectorProps {
  workflows: any[];
  selectedWorkflow: string;
  onWorkflowChange: (workflowId: string) => void;
  isCompact?: boolean;
}

const WorkflowSelector: React.FC<WorkflowSelectorProps> = ({
  workflows,
  selectedWorkflow,
  onWorkflowChange,
  isCompact = false
}) => {
  const selectedWorkflowObj = workflows.find(w => w.id === selectedWorkflow);
  
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="outline"
          className="h-8 border border-input hover:bg-primary/10"
        >
          <Zap className="h-4 w-4 mr-2" />
          {!isCompact && (
            <span className="truncate max-w-[120px]">
              {selectedWorkflowObj?.name || 'Select Workflow'}
            </span>
          )}
          <ChevronDown className="h-3 w-3 ml-2 opacity-50" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-56">
        <DropdownMenuRadioGroup
          value={selectedWorkflow}
          onValueChange={onWorkflowChange}
        >
          {workflows.map((workflow) => (
            <DropdownMenuRadioItem
              key={workflow.id}
              value={workflow.id}
              className="cursor-pointer"
            >
              <div className="flex items-center justify-between w-full">
                <span>{workflow.name}</span>
                {workflow.id === selectedWorkflow && (
                  <Check className="h-4 w-4 ml-2" />
                )}
              </div>
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
};

export default WorkflowSelector;
