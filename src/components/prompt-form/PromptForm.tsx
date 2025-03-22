
import React, { useState, useEffect } from 'react';
import { Card } from '@/components/ui/card';
import { toast } from 'sonner';
import usePromptForm from './usePromptForm';
import PromptInput from '@/components/prompt/PromptInput';
import PromptFormToolbar from './PromptFormToolbar';
import AdvancedOptions from '@/components/AdvancedOptions';
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
  const [localLoading, setLocalLoading] = useState(false);

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

  // Update prompt when currentPrompt prop changes
  useEffect(() => {
    if (currentPrompt && currentPrompt !== prompt) {
      console.log('PromptForm: Updating prompt from prop:', currentPrompt);
      setPrompt(currentPrompt);
    }
  }, [currentPrompt]);

  const handleSubmit = () => {
    if (prompt.trim() === '' && imageFiles.length === 0) {
      toast.error('Please enter a prompt or upload an image');
      return;
    }

    if (!selectedWorkflow) {
      toast.error('Please select a workflow');
      return;
    }

    setLocalLoading(true);

    // Combine local image files with any external URLs that need to be fetched
    const allImages: (File | string)[] = [...imageFiles];

    // If we have preview URLs that aren't from local files, include them too
    const externalUrls = previewUrls.filter(url => 
      !imageFiles.some(file => url === URL.createObjectURL(file))
    );
    
    // Add external URLs to the images array
    if (externalUrls.length > 0) {
      allImages.push(...externalUrls);
    }

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

    setTimeout(() => {
      setLocalLoading(false);
    }, 1000);
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

  // Handle external image URLs added from "Use as input"
  useEffect(() => {
    const urlsFromProps = window.externalImageUrls || [];
    if (urlsFromProps && urlsFromProps.length > 0) {
      console.log('PromptForm: External image URLs detected:', urlsFromProps);
      // Clear the global variable after using it
      window.externalImageUrls = [];
      
      // Add these URLs to our preview URLs if they're not already there
      setPreviewUrls(prev => {
        const newUrls = [...prev];
        for (const url of urlsFromProps) {
          if (!newUrls.includes(url)) {
            newUrls.push(url);
          }
        }
        return newUrls;
      });
    }
  }, []);

  const handleRemoveImage = (index: number) => {
    if (index < 0 || index >= previewUrls.length) return;
    
    // If this URL is from a File, revoke it
    const url = previewUrls[index];
    const isFileUrl = imageFiles.some(file => URL.createObjectURL(file) === url);
    if (isFileUrl) {
      URL.revokeObjectURL(url);
    }
    
    // Filter out the file if it exists
    setImageFiles(prevFiles => prevFiles.filter((_, i) => 
      URL.createObjectURL(prevFiles[i]) !== url
    ));
    
    // Remove from preview URLs
    setPreviewUrls(prevUrls => prevUrls.filter((_, i) => i !== index));
  };

  const clearAllImages = () => {
    // Revoke any object URLs we created
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

  const handleIncrementBatchSize = () => {
    incrementBatchSize();
  };

  const handleDecrementBatchSize = () => {
    decrementBatchSize();
  };

  const isButtonDisabled = localLoading || (prompt.trim() === '' && imageFiles.length === 0);

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
          incrementBatchSize={handleIncrementBatchSize}
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
