import { useState, useEffect } from 'react';
import typedWorkflows from '@/data/typedWorkflows';
import refinersData from '@/data/refiners.json';
import apiService from '@/utils/api';
import { usePublishDestinations } from '@/hooks/usePublishDestinations';

export interface PromptFormInitialValues {
  selectedWorkflow?: string;
  selectedRefiner?: string;
  selectedPublish?: string;
  workflowParams?: Record<string, any>;
  globalParams?: Record<string, any>;
  refinerParams?: Record<string, any>;
}

const usePromptForm = (initialValues: PromptFormInitialValues = {}) => {
  // Find the default workflow ID for initialization - but we'll default to "auto" now
  const getDefaultWorkflowId = (): string => {
    // If initialValues provides a specific workflow, use it
    if (initialValues.selectedWorkflow) {
      return initialValues.selectedWorkflow;
    }
    
    // Default to "auto" to let the backend resolve the best workflow
    return 'auto';
  };
  
  // Get default refiner - default to "auto" to let the backend resolve
  const getDefaultRefinerId = (): string => {
    // If initialValues provides a specific refiner, use it
    if (initialValues.selectedRefiner) {
      return initialValues.selectedRefiner;
    }
    
    // Default to "auto" to let the backend resolve the best refiner
    return 'auto';
  };
  
  // Use initialValues if provided, otherwise use defaults
  const defaultWorkflowId = getDefaultWorkflowId();
  const defaultRefinerId = getDefaultRefinerId();
  
  const [selectedWorkflow, setSelectedWorkflow] = useState(defaultWorkflowId);
  const [selectedRefiner, setSelectedRefiner] = useState(defaultRefinerId);
  const [selectedPublish, setSelectedPublish] = useState(initialValues.selectedPublish || 'none');
  const [workflowParams, setWorkflowParams] = useState<Record<string, any>>(initialValues.workflowParams || {});
  const [globalParams, setGlobalParams] = useState<Record<string, any>>(initialValues.globalParams || {
    batch_size: 1, // Default batch size is now 1
  });
  const [refinerParams, setRefinerParams] = useState<Record<string, any>>(initialValues.refinerParams || {});
  
  // Use shared hook for publish destinations
  const { destinations: publishDestinations } = usePublishDestinations();
  
  // Log initialization only once on mount
  useEffect(() => {
    console.log("usePromptForm: Initializing with workflow ID:", defaultWorkflowId);
    console.log("usePromptForm: Initializing with refiner ID:", defaultRefinerId);
    console.log("usePromptForm: Initial values provided:", initialValues);
  }, []); // Empty dependency array ensures this only runs once on mount

  // Log current state for debugging - only when values actually change
  useEffect(() => {
    console.log("usePromptForm: Current selected workflow:", selectedWorkflow);
    console.log("usePromptForm: Current selected refiner:", selectedRefiner);
  }, [selectedWorkflow, selectedRefiner]);

  // Initialize workflow parameters when workflow changes
  useEffect(() => {
    if (selectedWorkflow === 'auto') {
      console.log("usePromptForm: Skipping parameter initialization for auto workflow");
      return;
    }
    
    const workflow = typedWorkflows.find(w => w.id === selectedWorkflow);
    if (workflow && workflow.parameters) {
      const initialParams: Record<string, any> = {};
      workflow.parameters.forEach(param => {
        if (param.default !== undefined) {
          initialParams[param.id] = param.default;
        }
      });
      setWorkflowParams(initialParams);
    }
  }, [selectedWorkflow]);

  const handleWorkflowChange = (workflowId: string) => {
    console.log(`usePromptForm: User changed workflow to ${workflowId}`);
    setSelectedWorkflow(workflowId);
    resetWorkflowParams();
  };

  const handleRefinerChange = (refinerId: string) => {
    console.log(`usePromptForm: User changed refiner to ${refinerId}`);
    setSelectedRefiner(refinerId);
    resetRefinerParams();
  };

  const handlePublishChange = (publishId: string) => {
    setSelectedPublish(publishId);
    console.log(`Selected publish destination changed to: ${publishId}`);
  };

  const resetWorkflowParams = () => {
    setWorkflowParams({});
  };

  const resetRefinerParams = () => {
    setRefinerParams({});
  };

  const updateWorkflowParam = (paramId: string, value: any) => {
    setWorkflowParams((prev) => ({
      ...prev,
      [paramId]: value,
    }));
  };

  const updateRefinerParam = (paramId: string, value: any) => {
    setRefinerParams((prev) => ({
      ...prev,
      [paramId]: value,
    }));
  };

  const updateGlobalParam = (paramId: string, value: any) => {
    setGlobalParams((prev) => ({
      ...prev,
      [paramId]: value,
    }));
  };

  // Method to update from advanced panel
  const updateFromAdvancedPanel = (updates: Partial<PromptFormInitialValues>) => {
    // Update workflow params
    if (updates.workflowParams) {
      setWorkflowParams(updates.workflowParams);
    }
    
    // Update refiner params
    if (updates.refinerParams) {
      setRefinerParams(updates.refinerParams);
    }
    
    // Update global params
    if (updates.globalParams) {
      setGlobalParams(updates.globalParams);
    }
    
    // Update publish destination
    if (updates.selectedPublish) {
      setSelectedPublish(updates.selectedPublish);
    }
  };

  // Reset user change flags (useful when loading a saved state)
  const resetUserChangeFlags = () => {
    // Nothing to reset anymore, we've removed the change tracking flags
  };

  return {
    selectedWorkflow,
    selectedRefiner,
    selectedPublish,
    workflowParams,
    globalParams,
    refinerParams,
    workflows: typedWorkflows,
    refiners: refinersData,
    publishDestinations,
    handleWorkflowChange,
    handleRefinerChange,
    handlePublishChange,
    resetWorkflowParams,
    resetRefinerParams,
    updateWorkflowParam,
    updateRefinerParam,
    updateGlobalParam,
    // Expose setters for external state management
    setSelectedWorkflow,
    setSelectedRefiner,
    setSelectedPublish,
    setWorkflowParams,
    setRefinerParams,
    setGlobalParams,
    updateFromAdvancedPanel,
    resetUserChangeFlags,
  };
};

export default usePromptForm;


