
import { useState, useEffect } from 'react';
import { toast } from 'sonner';
import { Workflow } from '@/types/workflows';
import workflowsData from '@/data/workflows.json';
import globalOptionsData from '@/data/global-options.json';
import refinersData from '@/data/refiners.json';

const usePromptForm = () => {
  const [prompt, setPrompt] = useState('');
  const [imageFiles, setImageFiles] = useState<File[]>([]);
  const [previewUrls, setPreviewUrls] = useState<string[]>([]);
  const [selectedWorkflow, setSelectedWorkflow] = useState<string>('text-to-image');
  const [selectedRefiner, setSelectedRefiner] = useState<string>('none');
  const [workflowParams, setWorkflowParams] = useState<Record<string, any>>({});
  const [refinerParams, setRefinerParams] = useState<Record<string, any>>({});
  const [globalParams, setGlobalParams] = useState<Record<string, any>>({});
  const [isAdvancedOptionsOpen, setIsAdvancedOptionsOpen] = useState(false);
  const [isButtonDisabled, setIsButtonDisabled] = useState(false);
  const [batchSize, setBatchSize] = useState(1);
  const workflows = workflowsData as Workflow[];
  const refiners = refinersData;
  
  useEffect(() => {
    const currentWorkflow = workflows.find(w => w.id === selectedWorkflow);
    if (currentWorkflow) {
      const defaultParams: Record<string, any> = {};
      currentWorkflow.params.forEach(param => {
        if (param.default !== undefined) {
          defaultParams[param.id] = param.default;
        }
      });
      setWorkflowParams(defaultParams);
    }
  }, [selectedWorkflow, workflows]);
  
  useEffect(() => {
    const defaultGlobalParams: Record<string, any> = {};
    globalOptionsData.forEach((param: any) => {
      if (param.default !== undefined) {
        defaultGlobalParams[param.id] = param.default;
      }
    });
    setGlobalParams(defaultGlobalParams);
  }, []);
  
  const handleWorkflowChange = (workflowId: string) => {
    setSelectedWorkflow(workflowId);
    
    const currentWorkflow = workflows.find(w => w.id === workflowId);
    if (currentWorkflow) {
      const defaultParams: Record<string, any> = {};
      currentWorkflow.params.forEach(param => {
        if (param.default !== undefined) {
          defaultParams[param.id] = param.default;
        }
      });
      setWorkflowParams(defaultParams);
    }
  };
  
  const handleRefinerChange = (refinerId: string) => {
    setSelectedRefiner(refinerId);
    setRefinerParams({});
  };

  const updateWorkflowParam = (paramId: string, value: any) => {
    setWorkflowParams(prev => ({
      ...prev,
      [paramId]: value
    }));
  };

  const updateRefinerParam = (paramId: string, value: any) => {
    setRefinerParams(prev => ({
      ...prev,
      [paramId]: value
    }));
  };

  const updateGlobalParam = (paramId: string, value: any) => {
    setGlobalParams(prev => ({
      ...prev,
      [paramId]: value
    }));
  };

  const resetWorkflowParams = () => {
    const currentWorkflow = workflows.find(w => w.id === selectedWorkflow);
    if (currentWorkflow) {
      const defaultParams: Record<string, any> = {};
      currentWorkflow.params.forEach(param => {
        if (param.default !== undefined) {
          defaultParams[param.id] = param.default;
        }
      });
      setWorkflowParams(defaultParams);
    }
  };

  const resetRefinerParams = () => {
    setRefinerParams({});
  };
  
  const incrementBatchSize = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (batchSize < 9) {
      setBatchSize(prev => prev + 1);
    }
  };
  
  const decrementBatchSize = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (batchSize > 1) {
      setBatchSize(prev => prev - 1);
    }
  };

  return {
    prompt,
    imageFiles,
    previewUrls,
    selectedWorkflow,
    selectedRefiner,
    workflowParams,
    refinerParams,
    globalParams,
    isAdvancedOptionsOpen,
    isButtonDisabled,
    batchSize,
    workflows,
    refiners,
    setPrompt,
    setImageFiles,
    setPreviewUrls,
    setSelectedWorkflow,
    setSelectedRefiner,
    setWorkflowParams,
    setRefinerParams,
    setGlobalParams,
    setIsAdvancedOptionsOpen,
    setIsButtonDisabled,
    setBatchSize,
    handleWorkflowChange,
    handleRefinerChange,
    updateWorkflowParam,
    updateRefinerParam,
    updateGlobalParam,
    resetWorkflowParams,
    resetRefinerParams,
    incrementBatchSize,
    decrementBatchSize
  };
};

export default usePromptForm;
