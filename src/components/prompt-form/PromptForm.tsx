
import React, { useState, useEffect } from 'react';
import { Card } from '@/components/ui/card';
import { toast } from 'sonner';
import usePromptForm from './usePromptForm';
import PromptInput from '@/components/prompt/PromptInput';
import PromptFormToolbar from './PromptFormToolbar';
import AdvancedOptions from '@/components/AdvancedOptions';
import ImagePreview from './ImagePreview';
import { PromptFormProps } from './types';

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
    if (currentPrompt && currentPrompt !== prompt) {
      setPrompt(currentPrompt);
    }
  }, [currentPrompt]);

  const handleSubmit = () => {
    // Generate even if prompt is empty but images exist
    if (prompt.trim() === '' && imageFiles.length === 0) {
      toast.error('Please enter a prompt or upload an image');
      return;
    }

    if (!selectedWorkflow) {
      toast.error('Please select a workflow');
      return;
    }

    const allImages: (File | string)[] = [...imageFiles];

    const refinerToUse = selectedRefiner === "none" ? undefined : selectedRefiner;

    onSubmit(
      prompt,
      allImages.length > 0 ? (allImages as File[] | string[]) : undefined,
      selectedWorkflow,
      workflowParams,
      globalParams,
      refinerToUse,
      refinerParams
    );
  };

  const handlePromptChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setPrompt(e.target.value);
  };

  const handleClearPrompt = () => {
    setPrompt('');
  };

  const handleImageUpload = (files: File[]) => {
    if (files.length === 0) return;

    const newPreviewUrls = files.map(file => URL.createObjectURL(file));
    
    setImageFiles(prevFiles => [...prevFiles, ...files]);
    setPreviewUrls(prevUrls => [...prevUrls, ...newPreviewUrls]);
  };

  const handleRemoveImage = (index: number) => {
    URL.revokeObjectURL(previewUrls[index]);
    
    setImageFiles(prevFiles => prevFiles.filter((_, i) => i !== index));
    setPreviewUrls(prevUrls => prevUrls.filter((_, i) => i !== index));
  };

  const clearAllImages = () => {
    previewUrls.forEach(url => URL.revokeObjectURL(url));
    
    setImageFiles([]);
    setPreviewUrls([]);
  };

  const toggleAdvancedOptions = () => {
    setIsAdvancedOptionsOpen(!isAdvancedOptionsOpen);
  };

  // Wrap increment and decrement functions to match expected signatures
  const handleIncrementBatchSize = () => {
    incrementBatchSize();
  };

  const handleDecrementBatchSize = () => {
    decrementBatchSize();
  };

  // Button is disabled if both prompt is empty AND no images are uploaded
  const isButtonDisabled = isLoading || (prompt.trim() === '' && imageFiles.length === 0);

  return (
    <div className="w-full mb-8">
      <Card className="p-4 relative">
        <PromptInput 
          prompt={prompt} 
          onPromptChange={handlePromptChange} 
          onClearPrompt={handleClearPrompt}
          onClearAllImages={clearAllImages}
          isLoading={isLoading}
          isFirstRun={isFirstRun}
          onSubmit={handleSubmit}
          uploadedImages={previewUrls}
        />
        
        <ImagePreview 
          previewUrls={previewUrls} 
          handleRemoveImage={handleRemoveImage}
          clearAllImages={clearAllImages}
        />
        
        <PromptFormToolbar 
          isLoading={isLoading}
          batchSize={batchSize}
          selectedWorkflow={selectedWorkflow}
          selectedRefiner={selectedRefiner}
          onImageUpload={handleImageUpload}
          onWorkflowChange={handleWorkflowChange}
          onRefinerChange={handleRefinerChange}
          incrementBatchSize={handleIncrementBatchSize}
          decrementBatchSize={handleDecrementBatchSize}
          toggleAdvancedOptions={toggleAdvancedOptions}
          handleSubmit={handleSubmit}
          prompt={prompt}
          isButtonDisabled={isButtonDisabled}
          workflows={workflows}
          isCompact={false}
          hasUploadedImages={imageFiles.length > 0}
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
