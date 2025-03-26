
import React, { useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { useIsMobile, useWindowSize } from '@/hooks/use-mobile';
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card";
import { Image, Wand, PaintBucket } from 'lucide-react';
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
  const { width } = useWindowSize();
  const isNarrow = width < 600;
  const shouldHideName = hideWorkflowName || isNarrow;
  
  // Enhanced logging for debugging
  useEffect(() => {
    console.log("WorkflowIconSelector: Received props");
    console.log("- selectedWorkflow:", selectedWorkflow);
    console.log("- workflows:", workflows.map(w => `${w.id} (${w.name})`).join(", "));
    console.log("- shouldHideName:", shouldHideName);
  }, [selectedWorkflow, workflows, shouldHideName]);
  
  // Find the selected workflow by ID
  const selectedWorkflowObj = workflows.find(w => w.id === selectedWorkflow);
  
  // Log the current selection for debugging
  useEffect(() => {
    console.log("WorkflowIconSelector: Current workflow:", selectedWorkflow);
    console.log("WorkflowIconSelector: Found workflow object:", selectedWorkflowObj);
    
    if (!selectedWorkflowObj) {
      console.warn("WorkflowIconSelector: No workflow found with ID:", selectedWorkflow);
      console.log("Available workflows:", workflows.map(w => `${w.id} (${w.name})`).join(", "));
    }
  }, [selectedWorkflow, selectedWorkflowObj, workflows]);
  
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
  
  // Default to "Text to Image" if no workflow is found
  const displayName = selectedWorkflowObj?.name || 'Workflow';
  const displayIcon = selectedWorkflowObj ? getWorkflowIcon(selectedWorkflow) : <Image className="h-5 w-5" />;
  
  return (
    <div className="flex items-center h-[48px]">
      <HoverCard openDelay={0} closeDelay={100}>
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
            {displayIcon}
            {!shouldHideName && (
              <span className="ml-2 text-sm truncate max-w-[90px]">{displayName}</span>
            )}
          </Button>
        </HoverCardTrigger>
        <HoverCardContent className="w-64 p-2" align="start">
          <div className="space-y-2">
            <h4 className="text-sm font-semibold">Workflows</h4>
            <div className="grid grid-cols-1 gap-1">
              {workflows.map((workflow) => (
                <Button
                  key={workflow.id}
                  variant={workflow.id === selectedWorkflow ? "secondary" : "ghost"}
                  size="sm"
                  className="justify-start text-sm h-auto py-2"
                  onClick={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    onWorkflowChange(workflow.id);
                  }}
                  type="button"
                >
                  <div className="mr-2 flex-shrink-0">
                    {getWorkflowIcon(workflow.id)}
                  </div>
                  <div className="flex flex-col items-start overflow-hidden">
                    <span className="truncate w-full text-left">{workflow.name}</span>
                    <span className="text-xs text-muted-foreground truncate w-full text-left">{workflow.description}</span>
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
