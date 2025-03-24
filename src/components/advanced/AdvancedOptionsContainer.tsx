
import React, { useCallback } from 'react';
import AdvancedOptions from '@/components/AdvancedOptions';
import { Workflow } from '@/types/workflows';

interface AdvancedOptionsContainerProps {
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
  workflows: Workflow[];
  selectedWorkflow: string;
  currentParams: Record<string, any>;
  currentGlobalParams: Record<string, any>;
  onWorkflowChange: (workflow: string) => void;
  onParamsChange: (params: Record<string, any>) => void;
  onGlobalParamChange: (paramId: string, value: any) => void;
}

const AdvancedOptionsContainer: React.FC<AdvancedOptionsContainerProps> = ({
  isOpen,
  onOpenChange,
  workflows,
  selectedWorkflow,
  currentParams,
  currentGlobalParams,
  onWorkflowChange,
  onParamsChange,
  onGlobalParamChange
}) => {
  // Handler for global param changes
  const handleGlobalParamChange = useCallback((paramId: string, value: any) => {
    console.log('Global param change:', paramId, value);
    onGlobalParamChange(paramId, value);
  }, [onGlobalParamChange]);

  // Create a wrapper function to adapt the parameter format
  const handleParamChange = useCallback((paramId: string, value: any) => {
    console.log('Param change:', paramId, value);
    
    // Update the parameter in the current params object
    const updatedParams = {
      ...currentParams,
      [paramId]: value
    };
    
    // Call the original onParamsChange with the updated object
    onParamsChange(updatedParams);
  }, [currentParams, onParamsChange]);

  return (
    <AdvancedOptions
      workflows={workflows}
      selectedWorkflow={selectedWorkflow}
      onWorkflowChange={onWorkflowChange}
      params={currentParams}
      onParamChange={handleParamChange}
      globalParams={currentGlobalParams}
      onGlobalParamChange={handleGlobalParamChange}
      selectedRefiner={'none'}
      onRefinerChange={() => {}}
      refinerParams={{}}
      onRefinerParamChange={() => {}}
      isOpen={isOpen}
      onOpenChange={onOpenChange}
    />
  );
};

export default AdvancedOptionsContainer;
