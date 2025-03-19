
import React from 'react';
import { Workflow } from '@/types/workflows';
import { Button } from '@/components/ui/button';
import { useIsMobile, useWindowSize } from '@/hooks/use-mobile';
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card";
import { Icons } from '@/components/ui/icons';
import { Image, Wand, PaintBucket } from 'lucide-react';

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
  hideWorkflowName = false
}) => {
  const isMobile = useIsMobile();
  const { width } = useWindowSize();
  const isNarrow = width < 600;
  const shouldHideName = hideWorkflowName || isNarrow;
  
  const selectedWorkflowObj = workflows.find(w => w.id === selectedWorkflow);
  
  const getWorkflowIcon = (workflowId: string) => {
    switch (workflowId) {
      case 'text-to-image':
        return <Image className="h-5 w-5" />;
      case 'image-to-image':
        return <PaintBucket className="h-5 w-5" />;
      case 'artistic-style-transfer':
        return <Wand className="h-5 w-5" />;
      default:
        return <Image className="h-5 w-5" />;
    }
  };
  
  return (
    <div className="flex items-center h-[48px]">
      <HoverCard>
        <HoverCardTrigger asChild>
          <Button
            variant="outline"
            className="h-[36px] border border-input hover:bg-purple-500/10 text-purple-700"
            onClick={() => {}}
          >
            {getWorkflowIcon(selectedWorkflow)}
            {!shouldHideName && (
              <span className="ml-2 text-sm">{selectedWorkflowObj?.name || 'Workflow'}</span>
            )}
          </Button>
        </HoverCardTrigger>
        <HoverCardContent className="w-64 p-2">
          <div className="space-y-2">
            <h4 className="text-sm font-semibold">Workflows</h4>
            <div className="grid grid-cols-1 gap-1">
              {workflows.map((workflow) => (
                <Button
                  key={workflow.id}
                  variant={workflow.id === selectedWorkflow ? "secondary" : "ghost"}
                  size="sm"
                  className="justify-start text-sm h-9"
                  onClick={() => onWorkflowChange(workflow.id)}
                >
                  <div className="mr-2">
                    {getWorkflowIcon(workflow.id)}
                  </div>
                  <div className="flex flex-col items-start">
                    <span>{workflow.name}</span>
                    <span className="text-xs text-muted-foreground">{workflow.description}</span>
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

export default WorkflowIconSelector;
