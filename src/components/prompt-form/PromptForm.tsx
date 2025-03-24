
import React, { useState, useEffect, useRef } from 'react';
import { Card } from '@/components/ui/card';
import { toast } from 'sonner';
import usePromptForm from './usePromptForm';
import PromptInput from '@/components/prompt/PromptInput';
import PromptFormToolbar from './PromptFormToolbar';
import { PromptFormProps, WorkflowProps } from './types';
import useExternalImageUrls from '@/hooks/use-external-images';
import { Workflow } from '@/types/workflows';

const PromptForm: React.FC<PromptFormProps> = ({
  onSubmit,
  isLoading = false,
  currentPrompt = '',
  isFirstRun = true,
  onOpenAdvancedOptions,
  selectedWorkflow: externalSelectedWorkflow,
  selectedRefiner: externalSelectedRefiner,
  workflowParams: externalWorkflowParams,
  refinerParams: externalRefinerParams,
  globalParams: externalGlobalParams,
}) => {
  const [imageFiles, setImageFiles] = useState<File[]>([]);
  const [previewUrls, setPreviewUrls] = useState<string[]>([]);
  const [prompt, setPrompt] = useState(currentPrompt || '');
  const [isAdvancedOptionsOpen, setIsAdvancedOptionsOpen] = useState(false);
  const [localLoading, setLocalLoading] = useState(false);
  const lastReceivedPrompt = useRef(currentPrompt);
  
  // Track if there's been a local selection change to prevent external override
  const [hasLocalSelectionChanged, setHasLocalSelectionChanged] = useState(false);
  
  // Initialize usePromptForm with external values
  const initialValues = {
    selectedWorkflow: externalSelectedWorkflow,
    selectedRefiner: externalSelectedRefiner,
    workflowParams: externalWorkflowParams,
    refinerParams: externalRefinerParams,
    globalParams: externalGlobalParams
  };

  const {
    selectedWorkflow,
    selectedRefiner,
    selectedPublish,
    workflowParams,
    globalParams,
    refinerParams,
    workflows,
    refiners,
    publishDestinations,
    handleWorkflowChange,
    handleRefinerChange,
    handlePublishChange,
    resetWorkflowParams,
    resetRefinerParams,
    updateWorkflowParam,
    updateRefinerParam,
    updateGlobalParam,
    setSelectedWorkflow,
    setSelectedRefiner,
    setWorkflowParams,
    setRefinerParams,
    setGlobalParams,
  } = usePromptForm(initialValues);

  // Update prompt when external prompt changes
  useEffect(() => {
    if (currentPrompt && currentPrompt !== lastReceivedPrompt.current) {
      console.log('PromptForm: Updating prompt from prop:', currentPrompt);
      setPrompt(currentPrompt);
      lastReceivedPrompt.current = currentPrompt;
    }
  }, [currentPrompt]);

  // Only sync with external workflow if we haven't made local changes
  useEffect(() => {
    if (!hasLocalSelectionChanged && externalSelectedWorkflow && externalSelectedWorkflow !== selectedWorkflow) {
      console.log('PromptForm: Syncing workflow from external state:', externalSelectedWorkflow);
      setSelectedWorkflow(externalSelectedWorkflow);
    }
  }, [externalSelectedWorkflow, selectedWorkflow, setSelectedWorkflow, hasLocalSelectionChanged]);

  // Only sync with external refiner if we haven't made local changes
  useEffect(() => {
    if (!hasLocalSelectionChanged && externalSelectedRefiner && externalSelectedRefiner !== selectedRefiner) {
      console.log('PromptForm: Syncing refiner from external state:', externalSelectedRefiner);
      setSelectedRefiner(externalSelectedRefiner);
    }
  }, [externalSelectedRefiner, selectedRefiner, setSelectedRefiner, hasLocalSelectionChanged]);

  // Sync with external params regardless of selection state
  useEffect(() => {
    if (externalWorkflowParams && JSON.stringify(externalWorkflowParams) !== JSON.stringify(workflowParams)) {
      console.log('PromptForm: Updating workflow params from external state:', externalWorkflowParams);
      setWorkflowParams(externalWorkflowParams);
    }
  }, [externalWorkflowParams, workflowParams, setWorkflowParams]);

  useEffect(() => {
    if (externalRefinerParams && JSON.stringify(externalRefinerParams) !== JSON.stringify(refinerParams)) {
      console.log('PromptForm: Updating refiner params from external state:', externalRefinerParams);
      setRefinerParams(externalRefinerParams);
    }
  }, [externalRefinerParams, refinerParams, setRefinerParams]);

  useEffect(() => {
    if (externalGlobalParams && JSON.stringify(externalGlobalParams) !== JSON.stringify(globalParams)) {
      console.log('PromptForm: Updating global params from external state:', externalGlobalParams);
      setGlobalParams(externalGlobalParams);
    }
  }, [externalGlobalParams, globalParams, setGlobalParams]);

  useExternalImageUrls(setPreviewUrls);

  // Custom workflow change handler that marks local changes
  const handleLocalWorkflowChange = (workflowId: string) => {
    console.log('PromptForm: Local workflow change to:', workflowId);
    setHasLocalSelectionChanged(true);
    handleWorkflowChange(workflowId);
  };

  // Custom refiner change handler that marks local changes
  const handleLocalRefinerChange = (refinerId: string) => {
    console.log('PromptForm: Local refiner change to:', refinerId);
    setHasLocalSelectionChanged(true);
    handleRefinerChange(refinerId);
  };

  const handleSubmit = () => {
    if (prompt.trim() === '' && imageFiles.length === 0 && previewUrls.length === 0) {
      toast.error('Please enter a prompt or upload an image');
      return;
    }

    if (!selectedWorkflow) {
      toast.error('Please select a workflow');
      return;
    }

    setLocalLoading(true);

    const allImages: (File | string)[] = [...imageFiles];

    const externalUrls = previewUrls.filter(url => 
      !imageFiles.some(file => url === URL.createObjectURL(file))
    );
    
    if (externalUrls.length > 0) {
      const uniqueUrls = new Set([...allImages, ...externalUrls]);
      allImages.length = 0;
      allImages.push(...uniqueUrls);
    }

    const refinerToUse = selectedRefiner === "none" ? undefined : selectedRefiner;
    const publishToUse = selectedPublish === "none" ? undefined : selectedPublish;
    
    const currentGlobalParams = {
      ...globalParams
    };

    console.log('PromptForm: Submitting generation with publish destination:', publishToUse);
    console.log('PromptForm: Full global params:', currentGlobalParams);
    console.log('PromptForm: Selected refiner:', refinerToUse);
    console.log('PromptForm: Refiner params:', refinerParams);

    // After submitting, allow external state to sync again
    setHasLocalSelectionChanged(false);

    onSubmit(
      prompt,
      allImages.length > 0 ? (allImages as File[] | string[]) : undefined,
      selectedWorkflow,
      workflowParams,
      currentGlobalParams,
      refinerToUse,
      refinerParams,
      publishToUse
    );

    setTimeout(() => {
      setLocalLoading(false);
    }, 1000);
  };

  const handlePromptChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setPrompt(e.target.value);
    lastReceivedPrompt.current = e.target.value;
  };

  const handleClearPrompt = () => {
    setPrompt('');
    lastReceivedPrompt.current = '';
  };

  const handleImageUpload = (files: File[]) => {
    if (files.length === 0) return;

    const newPreviewUrls = files.map(file => URL.createObjectURL(file));
    
    setImageFiles(prevFiles => [...prevFiles, ...files]);
    setPreviewUrls(prevUrls => {
      const existingUrls = new Set(prevUrls);
      const uniqueNewUrls = newPreviewUrls.filter(url => !existingUrls.has(url));
      
      return [...prevUrls, ...uniqueNewUrls];
    });
  };

  const handleRemoveImage = (index: number) => {
    if (index < 0 || index >= previewUrls.length) return;
    
    const url = previewUrls[index];
    const isFileUrl = imageFiles.some(file => URL.createObjectURL(file) === url);
    if (isFileUrl) {
      URL.revokeObjectURL(url);
    }
    
    setImageFiles(prevFiles => prevFiles.filter((_, i) => 
      URL.createObjectURL(prevFiles[i]) !== url
    ));
    
    setPreviewUrls(prevUrls => prevUrls.filter((_, i) => i !== index));
  };

  const clearAllImages = () => {
    previewUrls.forEach(url => {
      const isFileUrl = imageFiles.some(file => URL.createObjectURL(file) === url);
      if (isFileUrl) {
        URL.revokeObjectURL(url);
      }
    });
    
    setImageFiles([]);
    setPreviewUrls([]);
  };

  const toggleAdvancedOptions = () => {
    if (onOpenAdvancedOptions) {
      onOpenAdvancedOptions();
    } else {
      setIsAdvancedOptionsOpen(!isAdvancedOptionsOpen);
    }
  };

  const isButtonDisabled = localLoading || ((prompt.trim() === '' && imageFiles.length === 0 && previewUrls.length === 0));

  return (
    <div className="w-full mb-8">
      <Card className="p-4 relative">
        <PromptInput 
          prompt={prompt} 
          onPromptChange={handlePromptChange} 
          onClearPrompt={handleClearPrompt}
          onClearAllImages={clearAllImages}
          onRemoveImage={handleRemoveImage}
          isLoading={localLoading}
          isFirstRun={isFirstRun}
          onSubmit={handleSubmit}
          uploadedImages={previewUrls}
        />
        
        <PromptFormToolbar 
          isLoading={localLoading}
          selectedWorkflow={selectedWorkflow}
          selectedRefiner={selectedRefiner}
          selectedPublish={selectedPublish}
          onImageUpload={handleImageUpload}
          onWorkflowChange={handleLocalWorkflowChange}
          onRefinerChange={handleLocalRefinerChange}
          onPublishChange={handlePublishChange}
          toggleAdvancedOptions={toggleAdvancedOptions}
          handleSubmit={handleSubmit}
          prompt={prompt}
          isButtonDisabled={isButtonDisabled}
          workflows={workflows as unknown as WorkflowProps[]} // Cast to satisfy TypeScript
          isCompact={false}
          hasUploadedImages={previewUrls.length > 0}
        />
      </Card>
    </div>
  );
};

export default PromptForm;
