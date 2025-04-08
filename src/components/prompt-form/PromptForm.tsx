import React, { useState, useEffect, useRef } from 'react';
import { Card } from '@/components/ui/card';
import { toast } from 'sonner';
import usePromptForm from './usePromptForm';
import PromptInput from '@/components/prompt/PromptInput';
import PromptFormToolbar from './PromptFormToolbar';
import { PromptFormProps, WorkflowProps } from './types';
import useExternalImageUrls from '@/hooks/use-external-images';
import typedWorkflows from '@/data/typedWorkflows';

const PromptForm: React.FC<PromptFormProps> = ({
  onSubmit,
  isLoading = false,
  currentPrompt = '',
  isFirstRun = true,
  onOpenAdvancedOptions,
  selectedWorkflow: externalSelectedWorkflow,
  selectedRefiner: externalSelectedRefiner,
  selectedPublish: externalSelectedPublish,
  workflowParams: externalWorkflowParams,
  refinerParams: externalRefinerParams,
  globalParams: externalGlobalParams,
  onWorkflowChange: externalWorkflowChange,
  onRefinerChange: externalRefinerChange,
  onPublishChange: externalPublishChange,
}) => {
  const [imageFiles, setImageFiles] = useState<File[]>([]);
  const [previewUrls, setPreviewUrls] = useState<string[]>([]);
  const [prompt, setPrompt] = useState(currentPrompt || '');
  const [isAdvancedOptionsOpen, setIsAdvancedOptionsOpen] = useState(false);
  const [localLoading, setLocalLoading] = useState(false);
  const lastReceivedPrompt = useRef(currentPrompt);
  const isInitialMount = useRef(true);

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
    updateFromAdvancedPanel,
    resetUserChangeFlags,
    setSelectedWorkflow,
    setSelectedRefiner,
    setSelectedPublish,
  } = usePromptForm();

  useEffect(() => {
    if (currentPrompt && currentPrompt !== lastReceivedPrompt.current) {
      setPrompt(currentPrompt);
      lastReceivedPrompt.current = currentPrompt;
    }
  }, [currentPrompt]);

  useEffect(() => {
    if (externalSelectedWorkflow && externalSelectedWorkflow !== selectedWorkflow) {
      setSelectedWorkflow(externalSelectedWorkflow);
    }
  }, [externalSelectedWorkflow, selectedWorkflow, setSelectedWorkflow]);

  useEffect(() => {
    if (externalSelectedRefiner && externalSelectedRefiner !== selectedRefiner) {
      setSelectedRefiner(externalSelectedRefiner);
    }
  }, [externalSelectedRefiner, selectedRefiner, setSelectedRefiner]);

  useEffect(() => {
    if (externalSelectedPublish && externalSelectedPublish !== selectedPublish) {
      setSelectedPublish(externalSelectedPublish);
    }
  }, [externalSelectedPublish, selectedPublish, setSelectedPublish]);

  useEffect(() => {
    if (!isInitialMount.current) {
      updateFromAdvancedPanel({
        workflowParams: externalWorkflowParams,
        refinerParams: externalRefinerParams,
        globalParams: externalGlobalParams
      });
    } else {
      const initialValues = {
        selectedWorkflow: externalSelectedWorkflow,
        selectedRefiner: externalSelectedRefiner,
        workflowParams: externalWorkflowParams,
        refinerParams: externalRefinerParams,
        globalParams: externalGlobalParams
      };
      
      updateFromAdvancedPanel(initialValues);
      
      resetUserChangeFlags();
      
      isInitialMount.current = false;
    }
  }, [
    externalWorkflowParams, 
    externalRefinerParams, 
    externalGlobalParams,
    updateFromAdvancedPanel
  ]);

  useExternalImageUrls(setPreviewUrls);

  const findImageCapableWorkflow = (currentWorkflowId: string) => {
    console.log(`Checking if current workflow ${currentWorkflowId} supports images...`);
    
    const currentWorkflowObj = typedWorkflows.find(w => w.id === currentWorkflowId);
    
    if (currentWorkflowObj && currentWorkflowObj.input) {
      const inputType = currentWorkflowObj.input;
      
      if (typeof inputType === 'string' && inputType === 'image') {
        console.log(`Current workflow ${currentWorkflowId} already supports images`);
        return currentWorkflowId;
      } 
      
      if (Array.isArray(inputType) && inputType.includes('image')) {
        console.log(`Current workflow ${currentWorkflowId} already supports images`);
        return currentWorkflowId;
      }
    }
    
    console.log(`Current workflow ${currentWorkflowId} does not support images, looking for one that does...`);
    
    // Look for workflows that accept both image and text
    const imageAndTextWorkflow = typedWorkflows.find(w => {
      if (!w.input) return false;
      
      if (Array.isArray(w.input)) {
        return w.input.includes('image') && w.input.includes('text');
      }
      
      return false;
    });

    if (imageAndTextWorkflow) {
      console.log(`Found image+text capable workflow: ${imageAndTextWorkflow.id}`);
      return imageAndTextWorkflow.id;
    }
    
    // If no combined workflow found, look for image-only workflow
    const imageWorkflow = typedWorkflows.find(w => {
      if (!w.input) return false;
      
      if (typeof w.input === 'string') {
        return w.input === 'image';
      } 
      
      if (Array.isArray(w.input)) {
        return w.input.includes('image');
      }
      
      return false;
    });
    
    if (imageWorkflow) {
      console.log(`Found image-capable workflow: ${imageWorkflow.id}`);
      return imageWorkflow.id;
    }
    
    console.log(`No image-capable workflow found, keeping current: ${currentWorkflowId}`);
    return currentWorkflowId;
  };

  const handleLocalWorkflowChange = (workflowId: string) => {
    handleWorkflowChange(workflowId);
    
    if (externalWorkflowChange) {
      externalWorkflowChange(workflowId);
    }
  };

  const handleLocalRefinerChange = (refinerId: string) => {
    handleRefinerChange(refinerId);
    
    if (externalRefinerChange) {
      externalRefinerChange(refinerId);
    }
  };

  const handleLocalPublishChange = (publishId: string) => {
    handlePublishChange(publishId);
    
    if (externalPublishChange) {
      externalPublishChange(publishId);
    }
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

    if (files.length > 0 && selectedWorkflow) {
      const imageWorkflow = findImageCapableWorkflow(selectedWorkflow);
      
      if (imageWorkflow !== selectedWorkflow) {
        console.log(`Switching to image-capable workflow: ${imageWorkflow}`);
        handleLocalWorkflowChange(imageWorkflow);
        toast.info(`Switched to image-compatible workflow: ${
          typedWorkflows.find(w => w.id === imageWorkflow)?.name || imageWorkflow
        }`);
      }
    }
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
          onPublishChange={handleLocalPublishChange}
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
