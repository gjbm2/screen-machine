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
  
  // Log the initial selections for debugging
  console.log("usePromptForm: Initializing with workflow ID:", defaultWorkflowId);
  console.log("usePromptForm: Initializing with refiner ID:", defaultRefinerId);
  console.log("usePromptForm: Initial values provided:", initialValues);
  
  const [selectedWorkflow, setSelectedWorkflow] = useState(defaultWorkflowId);
  const [selectedRefiner, setSelectedRefiner] = useState(defaultRefinerId);
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
    console.log("usePromptForm: Current selected refiner:", selectedRefiner);
  }, [selectedWorkflow, selectedRefiner]);
  
  // Initialize workflow parameters based on the selected workflow once on mount
  useEffect(() => {
    // Skip parameter initialization for "auto" workflow since we don't know which workflow will be selected
    if (selectedWorkflow === 'auto') {
      console.log("usePromptForm: Skipping parameter initialization for auto workflow");
      return;
    }
    
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
