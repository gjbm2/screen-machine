
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

  // Refiner state and handlers
  const [selectedRefiner, setSelectedRefiner] = React.useState('none');
  const [refinerParams, setRefinerParams] = React.useState<Record<string, any>>({});

  // Handle refiner change
  const handleRefinerChange = useCallback((refinerId: string) => {
    console.log('Refiner change:', refinerId);
    setSelectedRefiner(refinerId);
  }, []);

  // Handle refiner param change
  const handleRefinerParamChange = useCallback((paramId: string, value: any) => {
    console.log('Refiner param change:', paramId, value);
    setRefinerParams(prev => ({
      ...prev,
      [paramId]: value
    }));
  }, []);

  // Handle panel open/close
  const handleOpenChange = useCallback((open: boolean) => {
    console.log('Advanced options container - handleOpenChange:', open);
    
    // Use a very slight delay to ensure any closing animations complete first
    // This helps prevent event handling conflicts during transitions
    setTimeout(() => {
      onOpenChange(open);
      
      // When closing, ensure we restore any body styles that might be preventing interaction
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
      onWorkflowChange={onWorkflowChange}
      params={currentParams}
      onParamChange={handleParamChange}
      globalParams={currentGlobalParams}
      onGlobalParamChange={handleGlobalParamChange}
      selectedRefiner={selectedRefiner}
      onRefinerChange={handleRefinerChange}
      refinerParams={refinerParams}
      onRefinerParamChange={handleRefinerParamChange}
      isOpen={isOpen}
      onOpenChange={handleOpenChange}
    />
  );
};

export default AdvancedOptionsContainer;
