import React, { useState, useEffect, useRef } from 'react';
import { Card } from '@/components/ui/card';
import { toast } from 'sonner';
import usePromptForm from './usePromptForm';
import PromptInput from '@/components/prompt/PromptInput';
import PromptFormToolbar from './PromptFormToolbar';
import AdvancedOptions from '@/components/AdvancedOptions';
import { PromptFormProps } from './types';
import useExternalImageUrls from '@/hooks/use-external-images';
import ImagePreviewSection from './ImagePreviewSection';

const PromptForm: React.FC<PromptFormProps> = ({
  onSubmit,
  isLoading = false,
  currentPrompt = '',
  isFirstRun = true,
}) => {
  const [imageFiles, setImageFiles] = useState<File[]>([]);
  const [previewUrls, setPreviewUrls] = useState<string[]>([]);
  const [prompt, setPrompt] = useState(currentPrompt || '');
  const [isAdvancedOptionsOpen, setIsAdvancedOptionsOpen] = useState(false);
  const [localLoading, setLocalLoading] = useState(false);
  const lastReceivedPrompt = useRef(currentPrompt);

  const {
    selectedWorkflow,
    selectedRefiner,
    batchSize,
    workflowParams,
    globalParams,
    refinerParams,
    workflows,
    refiners,
    incrementBatchSize,
    decrementBatchSize,
    handleWorkflowChange,
    handleRefinerChange,
    resetWorkflowParams,
    resetRefinerParams,
    updateWorkflowParam,
    updateRefinerParam,
    updateGlobalParam,
  } = usePromptForm();

  useEffect(() => {
    if (currentPrompt && currentPrompt !== lastReceivedPrompt.current) {
      console.log('PromptForm: Updating prompt from prop:', currentPrompt);
      setPrompt(currentPrompt);
      lastReceivedPrompt.current = currentPrompt;
    }
  }, [currentPrompt]);

  useExternalImageUrls(setPreviewUrls);

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
    
    const updatedGlobalParams = {
      ...globalParams,
      batch_size: batchSize
    };

    console.log(`Submitting with batch size: ${batchSize}`, updatedGlobalParams);

    onSubmit(
      prompt,
      allImages.length > 0 ? (allImages as File[] | string[]) : undefined,
      selectedWorkflow,
      workflowParams,
      updatedGlobalParams,
      refinerToUse,
      refinerParams
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
    setIsAdvancedOptionsOpen(!isAdvancedOptionsOpen);
  };

  const handleDecrementBatchSize = () => {
    decrementBatchSize();
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
          batchSize={batchSize}
          selectedWorkflow={selectedWorkflow}
          selectedRefiner={selectedRefiner}
          onImageUpload={handleImageUpload}
          onWorkflowChange={handleWorkflowChange}
          onRefinerChange={handleRefinerChange}
          incrementBatchSize={incrementBatchSize}
          decrementBatchSize={handleDecrementBatchSize}
          toggleAdvancedOptions={toggleAdvancedOptions}
          handleSubmit={handleSubmit}
          prompt={prompt}
          isButtonDisabled={isButtonDisabled}
          workflows={workflows}
          isCompact={false}
          hasUploadedImages={previewUrls.length > 0}
        />
        
        {isAdvancedOptionsOpen && (
          <AdvancedOptions
            workflows={workflows}
            selectedWorkflow={selectedWorkflow}
            onWorkflowChange={handleWorkflowChange}
            params={workflowParams}
            onParamChange={updateWorkflowParam}
            globalParams={globalParams}
            onGlobalParamChange={updateGlobalParam}
            selectedRefiner={selectedRefiner}
            onRefinerChange={handleRefinerChange}
            refinerParams={refinerParams}
            onRefinerParamChange={updateRefinerParam}
            isOpen={isAdvancedOptionsOpen}
            onOpenChange={setIsAdvancedOptionsOpen}
          />
        )}
      </Card>
    </div>
  );
};

export default PromptForm;
