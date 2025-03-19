
import React from 'react';
import { Workflow } from '@/types/workflows';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { Image, Code, FileText, Video, Music, LayoutDashboard, Zap } from 'lucide-react';

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
        return <Image className="h-5 w-5" />;
      case 'image-to-image':
        return <Image className="h-5 w-5" />;
      case 'artistic-style-transfer':
        return <Zap className="h-5 w-5" />;
      default:
        return <LayoutDashboard className="h-5 w-5" />;
    }
  };

  // Get the current workflow name
  const currentWorkflow = workflows.find(w => w.id === selectedWorkflow);

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-1.5">
        {workflows.map((workflow) => (
          <TooltipProvider key={workflow.id}>
            <Tooltip delayDuration={300}>
              <TooltipTrigger asChild>
                <Button
                  type="button"
                  variant={selectedWorkflow === workflow.id ? "default" : "outline"}
                  size="icon"
                  className={selectedWorkflow === workflow.id 
                    ? "bg-purple-500 hover:bg-purple-600" 
                    : "hover:bg-purple-500/10 text-purple-700"}
                  onClick={() => onWorkflowChange(workflow.id)}
                >
                  {getWorkflowIcon(workflow.id)}
                </Button>
              </TooltipTrigger>
              <TooltipContent side="bottom">
                <p>{workflow.name}</p>
                <p className="text-xs text-muted-foreground">{workflow.description}</p>
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
        ))}
      </div>
    </div>
  );
};

export default WorkflowIconSelector;
