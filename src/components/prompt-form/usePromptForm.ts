
import { useState, useEffect } from 'react';
import typedWorkflows from '@/data/typedWorkflows';
import refinersData from '@/data/refiners.json';
import { getPublishDestinations } from '@/services/PublishService';

interface PromptFormInitialValues {
  selectedWorkflow?: string;
  selectedRefiner?: string;
  selectedPublish?: string;
  workflowParams?: Record<string, any>;
  globalParams?: Record<string, any>;
  refinerParams?: Record<string, any>;
}

const usePromptForm = (initialValues: PromptFormInitialValues = {}) => {
  // Use initialValues if provided, otherwise use defaults
  const [selectedWorkflow, setSelectedWorkflow] = useState(initialValues.selectedWorkflow || 'text-to-image');
  const [selectedRefiner, setSelectedRefiner] = useState(initialValues.selectedRefiner || 'none');
  const [selectedPublish, setSelectedPublish] = useState(initialValues.selectedPublish || 'none');
  const [workflowParams, setWorkflowParams] = useState<Record<string, any>>(initialValues.workflowParams || {});
  const [globalParams, setGlobalParams] = useState<Record<string, any>>(initialValues.globalParams || {
    batch_size: 1, // Default batch size is now 1
  });
  const [refinerParams, setRefinerParams] = useState<Record<string, any>>(initialValues.refinerParams || {});
  
  // Flag to track if changes are made locally by the user
  const [hasUserChangedWorkflow, setHasUserChangedWorkflow] = useState(false);
  const [hasUserChangedRefiner, setHasUserChangedRefiner] = useState(false);

  // Initialize workflow parameters based on the selected workflow only once
  useEffect(() => {
    // Only initialize params if this is the first time or if coming from advanced panel without user changes
    if (!hasUserChangedWorkflow) {
      // Find the selected workflow
      const workflow = typedWorkflows.find(w => w.id === selectedWorkflow);
      
      if (workflow && workflow.params) {
        // Create an object with default parameter values
        const defaultParams: Record<string, any> = {};
        
        workflow.params.forEach(param => {
          if (param.default !== undefined) {
            defaultParams[param.id] = param.default;
          }
        });
        
        // Set workflow parameters with default values
        setWorkflowParams(defaultParams);
      }
    }
  }, [selectedWorkflow, hasUserChangedWorkflow]);

  const handleWorkflowChange = (workflowId: string) => {
    console.log(`usePromptForm: User changed workflow to ${workflowId}`);
    setSelectedWorkflow(workflowId);
    setHasUserChangedWorkflow(true);
    resetWorkflowParams();
  };

  const handleRefinerChange = (refinerId: string) => {
    console.log(`usePromptForm: User changed refiner to ${refinerId}`);
    setSelectedRefiner(refinerId);
    setHasUserChangedRefiner(true);
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

  // Method to update from advanced panel that respects user choices
  const updateFromAdvancedPanel = (updates: Partial<PromptFormInitialValues>) => {
    // Only update workflow if user hasn't changed it from prompt form
    if (updates.selectedWorkflow && !hasUserChangedWorkflow) {
      setSelectedWorkflow(updates.selectedWorkflow);
    }
    
    // Only update refiner if user hasn't changed it from prompt form
    if (updates.selectedRefiner && !hasUserChangedRefiner) {
      setSelectedRefiner(updates.selectedRefiner);
    }
    
    // Always update params
    if (updates.workflowParams) {
      setWorkflowParams(updates.workflowParams);
    }
    
    if (updates.refinerParams) {
      setRefinerParams(updates.refinerParams);
    }
    
    if (updates.globalParams) {
      setGlobalParams(updates.globalParams);
    }
    
    if (updates.selectedPublish) {
      setSelectedPublish(updates.selectedPublish);
    }
  };

  // Reset user change flags (useful when loading a saved state)
  const resetUserChangeFlags = () => {
    setHasUserChangedWorkflow(false);
    setHasUserChangedRefiner(false);
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
    publishDestinations: getPublishDestinations(),
    handleWorkflowChange,
    handleRefinerChange,
    handlePublishChange,
    resetWorkflowParams,
    resetRefinerParams,
    updateWorkflowParam,
    updateRefinerParam,
    updateGlobalParam,
    // Expose setters for external state management with ability to respect user choices
    setSelectedWorkflow,
    setSelectedRefiner,
    setWorkflowParams,
    setRefinerParams,
    setGlobalParams,
    updateFromAdvancedPanel,
    resetUserChangeFlags,
  };
};

export default usePromptForm;
