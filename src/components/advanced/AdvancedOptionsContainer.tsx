import React, { useCallback } from 'react';
import AdvancedOptions from '@/components/AdvancedOptions';
import { Workflow } from '@/types/workflows';

interface AdvancedOptionsContainerProps {
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
  workflows: Workflow[];
  selectedWorkflow: string;
  currentParams: Record<string, any>;
  onWorkflowChange: (workflow: string) => void;
  onParamsChange: (params: Record<string, any>) => void;
  selectedRefiner?: string;
  onRefinerChange?: (refiner: string) => void;
}

// The params also carry where the picture is published (publish_destination); the workflow's own settings are the rest
const keepPublish = (params: Record<string, any>) =>
  params.publish_destination !== undefined ? { publish_destination: params.publish_destination } : {};

const AdvancedOptionsContainer: React.FC<AdvancedOptionsContainerProps> = ({
  isOpen,
  onOpenChange,
  workflows,
  selectedWorkflow,
  currentParams,
  onWorkflowChange,
  onParamsChange,
  selectedRefiner = 'auto',
  onRefinerChange = () => {},
}) => {
  const handleParamChange = useCallback((paramId: string, value: any) => {
    onParamsChange({ ...currentParams, [paramId]: value });
  }, [currentParams, onParamsChange]);

  // A new workflow starts from its own defaults: one workflow's size or steps mean nothing to another
  const handleWorkflowChange = useCallback((workflowId: string) => {
    if (workflowId !== selectedWorkflow) onParamsChange(keepPublish(currentParams));
    onWorkflowChange(workflowId);
  }, [currentParams, onParamsChange, onWorkflowChange, selectedWorkflow]);

  const handleOpenChange = useCallback((open: boolean) => {
    // A very slight delay lets the closing animation finish before the page takes events again
    setTimeout(() => {
      onOpenChange(open);
      if (!open) {
        document.body.style.pointerEvents = '';
        document.body.style.cursor = '';
      }
    }, 10);
  }, [onOpenChange]);

  return (
    <AdvancedOptions
      workflows={workflows}
      selectedWorkflow={selectedWorkflow}
      onWorkflowChange={handleWorkflowChange}
      params={currentParams}
      onParamChange={handleParamChange}
      onResetParams={() => onParamsChange(keepPublish(currentParams))}
      selectedRefiner={selectedRefiner}
      onRefinerChange={onRefinerChange}
      isOpen={isOpen}
      onOpenChange={handleOpenChange}
    />
  );
};

export default AdvancedOptionsContainer;
