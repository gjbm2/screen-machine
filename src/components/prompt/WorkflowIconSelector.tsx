
import React from 'react';
import { Workflow } from '@/types/workflows';
import { Button } from '@/components/ui/button';
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
import { Image, Code, FileText, Video, Music, LayoutDashboard, Zap, Layers } from 'lucide-react';

interface WorkflowIconSelectorProps {
  workflows: Workflow[];
  selectedWorkflow: string;
  onWorkflowChange: (workflowId: string) => void;
  hideWorkflowName?: boolean;
}

const WorkflowIconSelector: React.FC<WorkflowIconSelectorProps> = ({
  workflows,
  selectedWorkflow,
  onWorkflowChange,
  hideWorkflowName = false,
}) => {
  // Get the icon based on workflow ID
  const getWorkflowIcon = (workflowId: string) => {
    switch (workflowId) {
      case 'text-to-image':
        return <Image className="h-4 w-4 mr-2" />;
      case 'image-to-image':
        return <Image className="h-4 w-4 mr-2" />;
      case 'artistic-style-transfer':
        return <Zap className="h-4 w-4 mr-2" />;
      default:
        return <LayoutDashboard className="h-4 w-4 mr-2" />;
    }
  };

  // Get the current workflow name
  const currentWorkflow = workflows.find(w => w.id === selectedWorkflow);

  return (
    <TooltipProvider>
      <Tooltip delayDuration={300}>
        <DropdownMenu>
          <TooltipTrigger asChild>
            <DropdownMenuTrigger asChild>
              <Button
                variant="outline"
                size="icon"
                className="hover:bg-purple-500/10 text-purple-700"
              >
                <Layers className="h-5 w-5" />
              </Button>
            </DropdownMenuTrigger>
          </TooltipTrigger>
          <TooltipContent side="bottom">
            <p>Workflow: {currentWorkflow?.name}</p>
            <p className="text-xs text-muted-foreground">Select a workflow</p>
          </TooltipContent>
          <DropdownMenuContent align="end" alignOffset={-5} sideOffset={5} className="bg-background/90 backdrop-blur-sm">
            {workflows.map((workflow) => (
              <DropdownMenuItem
                key={workflow.id}
                onClick={() => onWorkflowChange(workflow.id)}
                className="cursor-pointer"
              >
                {getWorkflowIcon(workflow.id)}
                <div>
                  <p>{workflow.name}</p>
                  <p className="text-xs text-muted-foreground">{workflow.description}</p>
                </div>
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      </Tooltip>
    </TooltipProvider>
  );
};

export default WorkflowIconSelector;
