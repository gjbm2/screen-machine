import { useState } from 'react';
import { useImageState } from './use-image-state';
import { useImageContainer } from './use-image-container';
import { useImageActions } from './use-image-actions';
import { useImageGenerationApi } from './use-image-generation-api';
import { useImageGenerationLoading } from './use-image-generation-loading';
import { useUploadedImages } from './use-uploaded-images';
import { usePromptSubmission } from './use-prompt-submission';
import { useContainerOrderEffect } from './use-container-order-effect';
import typedWorkflows from '@/data/typedWorkflows';

// Declare global window type to include our custom property
declare global {
  interface Window {
    externalImageUrls?: string[];
    imageCounter?: number; // Add a global counter for image numbering
  }
}

export const useImageGeneration = (addConsoleLog: (log: any) => void) => {
  // Get the default workflow ID
  const getDefaultWorkflowId = (): string => {
    // First try to find a workflow with default=true
    const defaultWorkflow = typedWorkflows.find(w => w.default === true);
    if (defaultWorkflow) {
      console.log("useImageGeneration: Using default workflow:", defaultWorkflow.id, defaultWorkflow.name);
      return defaultWorkflow.id;
    }
    
    // Fall back to the first workflow or 'text-to-image' if no workflows exist
    const fallbackId = typedWorkflows.length > 0 ? typedWorkflows[0].id : 'text-to-image';
    console.log("useImageGeneration: Using fallback workflow:", fallbackId);
    return fallbackId;
  };

  // State for current prompts and workflows using the default workflow ID
  const [currentPrompt, setCurrentPrompt] = useState<string>('');
  const [currentWorkflow, setCurrentWorkflow] = useState<string>(getDefaultWorkflowId());
  const [currentParams, setCurrentParams] = useState<Record<string, any>>({});
  const [currentGlobalParams, setCurrentGlobalParams] = useState<Record<string, any>>({});

  // Initialize global image counter if it doesn't exist
  useState(() => {
    if (typeof window.imageCounter === 'undefined') {
      window.imageCounter = 0;
    }
  });

  // Use our custom hooks
  const { generatedImages, setGeneratedImages } = useImageState();
  
  const { 
    imageContainerOrder, 
    setImageContainerOrder,
    expandedContainers,
    setExpandedContainers,
    nextContainerId,
    setNextContainerId,
    handleReorderContainers: containerReorder,
    handleDeleteContainer: internalHandleDeleteContainer,
    handleAddNewContainer,
    collapseAllExcept
  } = useImageContainer();

  const {
    isFirstRun,
    setIsFirstRun,
    fullscreenRefreshTrigger,
    lastBatchIdUsed,
    setLastBatchIdUsed,
    activeGenerations,
    setActiveGenerations,
    handleGenerationComplete
  } = useImageGenerationLoading();

  const { uploadedImageUrls, setUploadedImageUrls } = useUploadedImages();

  // Apply the container order effect
  useContainerOrderEffect({
    generatedImages,
    setImageContainerOrder
  });

  const {
    lastBatchId,
    generateImages,
  } = useImageGenerationApi(
    addConsoleLog,
    setGeneratedImages,
    setImageContainerOrder,
    nextContainerId,
    setNextContainerId,
    handleGenerationComplete
  );

  const { handleSubmitPrompt } = usePromptSubmission({
    currentWorkflow,
    currentParams,
    currentGlobalParams,
    lastBatchIdUsed,
    setIsFirstRun,
    setLastBatchIdUsed,
    generateImages,
    collapseAllExcept
  });
  
  const {
    imageUrl,
    handleUseGeneratedAsInput,
    handleCreateAgain,
    handleDownloadImage,
    handleDeleteImage,
    handleReorderContainers,
  } = useImageActions(
    setGeneratedImages,
    imageContainerOrder,
    setImageContainerOrder,
    setCurrentPrompt,
    setCurrentWorkflow,
    setUploadedImageUrls,
    handleSubmitPrompt,
    generatedImages
  );

  // Wrapper for handleDeleteContainer to match the expected signature
  const handleDeleteContainer = (batchId: string) => {
    internalHandleDeleteContainer(batchId, setGeneratedImages);
  };

  return {
    generatedImages,
    activeGenerations,
    imageUrl,
    currentPrompt,
    uploadedImageUrls,
    currentWorkflow,
    currentParams,
    currentGlobalParams,
    imageContainerOrder,
    expandedContainers,
    lastBatchId,
    isFirstRun,
    fullscreenRefreshTrigger,
    setCurrentPrompt,
    setUploadedImageUrls,
    setCurrentWorkflow,
    setCurrentParams,
    setCurrentGlobalParams,
    setImageContainerOrder,
    setExpandedContainers,
    handleSubmitPrompt,
    handleUseGeneratedAsInput,
    handleCreateAgain,
    handleDownloadImage,
    handleDeleteImage,
    handleReorderContainers,
    handleDeleteContainer
  };
};
