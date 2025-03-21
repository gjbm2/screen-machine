
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

  // Update the prompt if currentPrompt changes (e.g. when duplicating an existing image)
  useEffect(() => {
    if (currentPrompt && currentPrompt !== prompt) {
      setPrompt(currentPrompt);
    }
  }, [currentPrompt]);

  const handleSubmit = () => {
    if (prompt.trim() === '') {
      toast.error('Please enter a prompt');
      return;
    }

    // Check if the workflow is valid
    if (!selectedWorkflow) {
      toast.error('Please select a workflow');
      return;
    }

    // Collect all images (files and URLs from the preview)
    const allImages: (File | string)[] = [...imageFiles];

    const refinerToUse = selectedRefiner === "none" ? undefined : selectedRefiner;

    // Send the data to the parent component
    onSubmit(
      prompt,
      allImages.length > 0 ? allImages as (File[] | string[]) : undefined,
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

  const handleImageUpload = (files: File[]) => {
    if (files.length === 0) return;

    // Create previews
    const newPreviewUrls = files.map(file => URL.createObjectURL(file));
    
    // Update state
    setImageFiles(prevFiles => [...prevFiles, ...files]);
    setPreviewUrls(prevUrls => [...prevUrls, ...newPreviewUrls]);
  };

  const handleRemoveImage = (index: number) => {
    // Clean up URL object to prevent memory leaks
    URL.revokeObjectURL(previewUrls[index]);
    
    // Remove the image from both arrays
    setImageFiles(prevFiles => prevFiles.filter((_, i) => i !== index));
    setPreviewUrls(prevUrls => prevUrls.filter((_, i) => i !== index));
  };

  const clearAllImages = () => {
    // Clean up all URL objects
    previewUrls.forEach(url => URL.revokeObjectURL(url));
    
    // Clear both arrays
    setImageFiles([]);
    setPreviewUrls([]);
  };

  const toggleAdvancedOptions = () => {
    setIsAdvancedOptionsOpen(!isAdvancedOptionsOpen);
  };

  // Determine if the submit button should be disabled
  const isButtonDisabled = isLoading || prompt.trim() === '';

  return (
    <div className="w-full mb-8">
      <Card className="p-4 relative">
        {/* Prompt Input */}
        <PromptInput 
          prompt={prompt} 
          onPromptChange={handlePromptChange} 
          isLoading={isLoading}
          isFirstRun={isFirstRun}
        />
        
        {/* Image Preview Section */}
        <ImagePreview 
          previewUrls={previewUrls} 
          handleRemoveImage={handleRemoveImage}
          clearAllImages={clearAllImages}
        />
        
        {/* Toolbar (Workflow selector, batch size controls, etc.) */}
        <PromptFormToolbar 
          isLoading={isLoading}
          batchSize={batchSize}
          selectedWorkflow={selectedWorkflow}
          selectedRefiner={selectedRefiner}
          onImageUpload={handleImageUpload}
          onWorkflowChange={handleWorkflowChange}
          onRefinerChange={handleRefinerChange}
          incrementBatchSize={incrementBatchSize}
          decrementBatchSize={decrementBatchSize}
          toggleAdvancedOptions={toggleAdvancedOptions}
          handleSubmit={handleSubmit}
          prompt={prompt}
          isButtonDisabled={isButtonDisabled}
          workflows={workflows}
          isCompact={false}
        />
        
        {/* Advanced Options Panel */}
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
