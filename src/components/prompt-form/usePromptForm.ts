
import { useState, useEffect } from 'react';
import typedWorkflows from '@/data/typedWorkflows';
import refinersData from '@/data/refiners.json';
import { getPublishDestinations } from '@/services/PublishService';

const usePromptForm = (initialValues = {}) => {
  // Use initialValues if provided, otherwise use defaults
  const [selectedWorkflow, setSelectedWorkflow] = useState(initialValues.selectedWorkflow || 'text-to-image');
  const [selectedRefiner, setSelectedRefiner] = useState(initialValues.selectedRefiner || 'none');
  const [selectedPublish, setSelectedPublish] = useState(initialValues.selectedPublish || 'none');
  const [workflowParams, setWorkflowParams] = useState<Record<string, any>>(initialValues.workflowParams || {});
  const [globalParams, setGlobalParams] = useState<Record<string, any>>(initialValues.globalParams || {
    batch_size: 1, // Default batch size is now 1
  });
  const [refinerParams, setRefinerParams] = useState<Record<string, any>>(initialValues.refinerParams || {});
  
  // Flag to prevent external state from overriding local changes
  const [isLocalChange, setIsLocalChange] = useState(false);

  // Initialize workflow parameters based on the selected workflow
  useEffect(() => {
    if (isLocalChange) {
      // Don't reinitialize params during local changes
      setIsLocalChange(false);
      return;
    }
    
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
  }, [selectedWorkflow, isLocalChange]);

  const handleWorkflowChange = (workflowId: string) => {
    console.log(`usePromptForm: Setting workflow to ${workflowId}`);
    setIsLocalChange(true); // Mark as local change
    setSelectedWorkflow(workflowId);
    resetWorkflowParams();
  };

  const handleRefinerChange = (refinerId: string) => {
    console.log(`usePromptForm: Setting refiner to ${refinerId}`);
    setIsLocalChange(true); // Mark as local change
    setSelectedRefiner(refinerId);
    resetRefinerParams();
  };

  const handlePublishChange = (publishId: string) => {
    setIsLocalChange(true); // Mark as local change
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
    // Expose setters for external state management
    setSelectedWorkflow,
    setSelectedRefiner,
    setWorkflowParams,
    setRefinerParams,
    setGlobalParams,
  };
};

export default usePromptForm;
