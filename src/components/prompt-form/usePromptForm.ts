import { useState, useEffect } from 'react';
import typedWorkflows from '@/data/typedWorkflows';
import refinersData from '@/data/refiners.json';
import apiService from '@/utils/api';

interface PromptFormInitialValues {
  selectedWorkflow?: string;
  selectedRefiner?: string;
  selectedPublish?: string;
  workflowParams?: Record<string, any>;
  globalParams?: Record<string, any>;
  refinerParams?: Record<string, any>;
}

const usePromptForm = (initialValues: PromptFormInitialValues = {}) => {
  // Find the default workflow ID for initialization
  const getDefaultWorkflowId = (): string => {
    // First try to find a workflow with default=true
    const defaultWorkflow = typedWorkflows.find(w => w.default === true);
    if (defaultWorkflow) {
      console.log("Found default workflow:", defaultWorkflow.id, defaultWorkflow.name);
      return defaultWorkflow.id;
    }
    
    // Fall back to the first workflow or 'text-to-image' if no workflows exist
    const fallbackId = typedWorkflows.length > 0 ? typedWorkflows[0].id : 'sdxl-scale.json';
    console.log("Using fallback workflow:", fallbackId);
    return fallbackId;
  };
  
  // Use initialValues if provided, otherwise use defaults
  const defaultWorkflowId = getDefaultWorkflowId();
  
  // Log the initial workflow selection for debugging
  console.log("usePromptForm: Initializing with workflow ID:", defaultWorkflowId);
  console.log("usePromptForm: Initial values provided:", initialValues);
  
  const [selectedWorkflow, setSelectedWorkflow] = useState(initialValues.selectedWorkflow || defaultWorkflowId);
  const [selectedRefiner, setSelectedRefiner] = useState(initialValues.selectedRefiner || 'none');
  const [selectedPublish, setSelectedPublish] = useState(initialValues.selectedPublish || 'none');
  const [workflowParams, setWorkflowParams] = useState<Record<string, any>>(initialValues.workflowParams || {});
  const [globalParams, setGlobalParams] = useState<Record<string, any>>(initialValues.globalParams || {
    batch_size: 1, // Default batch size is now 1
  });
  const [refinerParams, setRefinerParams] = useState<Record<string, any>>(initialValues.refinerParams || {});
  const [publishDestinations, setPublishDestinations] = useState<any[]>([]);
  
  // Verify that selectedWorkflow is set correctly
  useEffect(() => {
    console.log("usePromptForm: Current selected workflow:", selectedWorkflow);
    
    // If somehow selectedWorkflow is empty or invalid, reset it to default
    if (!selectedWorkflow || !typedWorkflows.some(w => w.id === selectedWorkflow)) {
      console.log("usePromptForm: Selected workflow is invalid, resetting to default");
      setSelectedWorkflow(defaultWorkflowId);
    }
  }, [selectedWorkflow]);
  
  // Initialize workflow parameters based on the selected workflow once on mount
  useEffect(() => {
    // Log the current selected workflow for debugging
    console.log("usePromptForm: Initializing parameters for workflow:", selectedWorkflow);
    
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
      
      // Set workflow parameters with default values, but only for params that don't already exist
      setWorkflowParams(prevParams => {
        const mergedParams = { ...prevParams };
        
        // Only add default params for keys that don't exist in current params
        Object.keys(defaultParams).forEach(key => {
          if (mergedParams[key] === undefined) {
            mergedParams[key] = defaultParams[key];
          }
        });
        
        return mergedParams;
      });
    }
  }, [selectedWorkflow]);

  useEffect(() => {
    const fetchDestinations = async () => {
      try {
        const destinations = await apiService.getPublishDestinations();
        setPublishDestinations(destinations);
      } catch (error) {
        console.error('Error fetching publish destinations:', error);
      }
    };
    fetchDestinations();
  }, []);

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
