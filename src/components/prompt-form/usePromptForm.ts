
import { useState, useEffect } from 'react';
import typedWorkflows from '@/data/typedWorkflows';
import refinersData from '@/data/refiners.json';
import { getPublishDestinations } from '@/services/PublishService';

const usePromptForm = () => {
  const [selectedWorkflow, setSelectedWorkflow] = useState('text-to-image');
  const [selectedRefiner, setSelectedRefiner] = useState('none');
  const [selectedPublish, setSelectedPublish] = useState('none');
  const [workflowParams, setWorkflowParams] = useState<Record<string, any>>({});
  const [globalParams, setGlobalParams] = useState<Record<string, any>>({
    batch_size: 1, // Default batch size is now 1
  });
  const [refinerParams, setRefinerParams] = useState<Record<string, any>>({});

  // Initialize workflow parameters based on the selected workflow
  useEffect(() => {
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
  }, [selectedWorkflow]);

  const handleWorkflowChange = (workflowId: string) => {
    console.log(`usePromptForm: Setting workflow to ${workflowId}`);
    setSelectedWorkflow(workflowId);
    resetWorkflowParams();
  };

  const handleRefinerChange = (refinerId: string) => {
    console.log(`usePromptForm: Setting refiner to ${refinerId}`);
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
