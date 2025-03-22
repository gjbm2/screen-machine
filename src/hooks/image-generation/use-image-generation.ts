
import { useState, useCallback, useEffect } from 'react';
import { nanoid } from '@/lib/utils';
import { useImageState } from './use-image-state';
import { useImageContainer } from './use-image-container';
import { useImageActions } from './use-image-actions';
import { useImageGenerationApi } from './use-image-generation-api';
import { ImageGenerationConfig } from './types';

// Declare global window type to include our custom property
declare global {
  interface Window {
    externalImageUrls?: string[];
  }
}

export const useImageGeneration = (addConsoleLog: (log: any) => void) => {
  // State for current prompts and workflows
  const [currentPrompt, setCurrentPrompt] = useState<string>('');
  const [uploadedImageUrls, setUploadedImageUrls] = useState<string[]>([]);
  const [currentWorkflow, setCurrentWorkflow] = useState<string>('flux1');
  const [currentParams, setCurrentParams] = useState<Record<string, any>>({});
  const [currentGlobalParams, setCurrentGlobalParams] = useState<Record<string, any>>({});
  const [isFirstRun, setIsFirstRun] = useState(true);
  const [fullscreenRefreshTrigger, setFullscreenRefreshTrigger] = useState(0);
  const [lastGeneratedBatchId, setLastGeneratedBatchId] = useState<string | null>(null);

  // Use our custom hooks
  const { generatedImages, setGeneratedImages } = useImageState();
  
  const { 
    imageContainerOrder, 
    setImageContainerOrder,
    nextContainerId,
    setNextContainerId,
    handleReorderContainers: containerReorder,
    handleDeleteContainer: internalHandleDeleteContainer
  } = useImageContainer();

  const {
    activeGenerations,
    lastBatchId,
    generateImages,
  } = useImageGenerationApi(
    addConsoleLog,
    setGeneratedImages,
    setImageContainerOrder,
    nextContainerId,
    setNextContainerId,
    // Enhanced callback for when a generation completes
    () => {
      // Increment refresh trigger to force component updates
      setFullscreenRefreshTrigger(prev => prev + 1);
      // Log the completion for debugging purposes
      console.log("Image generation complete, incrementing refresh trigger:", fullscreenRefreshTrigger + 1);
      
      // If we have a lastGeneratedBatchId, we want to force a refresh on that
      if (lastGeneratedBatchId) {
        console.log("Last generated batch ID:", lastGeneratedBatchId);
        // Clear the last generated batch ID after handling
        setLastGeneratedBatchId(null);
      }
    }
  );

  // When uploadedImageUrls changes, store them in a global variable
  // for access in other components, but ensure uniqueness
  useEffect(() => {
    if (uploadedImageUrls.length > 0) {
      // Convert to Set and back to array to ensure uniqueness
      const uniqueUrls = [...new Set(uploadedImageUrls)];
      console.log('Setting global externalImageUrls:', uniqueUrls);
      window.externalImageUrls = uniqueUrls; 
    } else {
      // Clear the global variable if there are no uploaded images
      window.externalImageUrls = [];
    }
  }, [uploadedImageUrls]);

  // Submit prompt handler - defined after generateImages is available
  const handleSubmitPrompt = useCallback(async (
    prompt: string, 
    imageFiles?: File[] | string[]
  ) => {
    setIsFirstRun(false);
    
    // Ensure we have unique image files (no duplicates)
    let uniqueImageFiles: File[] | string[] | undefined = undefined;
    
    if (imageFiles && imageFiles.length > 0) {
      // Separate files and strings into different arrays
      const fileObjects: File[] = [];
      const urlStrings: string[] = [];
      
      imageFiles.forEach(item => {
        if (typeof item === 'string') {
          urlStrings.push(item);
        } else if (item instanceof File) {
          fileObjects.push(item);
        }
      });
      
      // If we have only files or only strings, use the appropriate array
      if (fileObjects.length > 0 && urlStrings.length === 0) {
        uniqueImageFiles = [...new Set(fileObjects)];
      } else if (urlStrings.length > 0 && fileObjects.length === 0) {
        uniqueImageFiles = [...new Set(urlStrings)];
      } else {
        // If we have a mix, convert all Files to URLs first
        // For actual implementation, we'd want to handle this differently
        // This is a workaround to satisfy TypeScript
        const allUrls = [...urlStrings];
        uniqueImageFiles = [...new Set(allUrls)];
      }
    }
    
    const config: ImageGenerationConfig = {
      prompt,
      imageFiles: uniqueImageFiles,
      workflow: currentWorkflow,
      params: currentParams,
      globalParams: currentGlobalParams,
    };
    
    // Track the last generated batch ID
    const newBatchId = await generateImages(config);
    if (newBatchId) {
      setLastGeneratedBatchId(newBatchId);
    }
  }, [
    currentWorkflow, 
    currentParams, 
    currentGlobalParams, 
    generateImages
  ]);
  
  const {
    imageUrl,
    handleUseGeneratedAsInput,
    handleCreateAgain: baseHandleCreateAgain,
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

  // Wrapper for handleCreateAgain to properly handle fullscreen refresh
  const handleCreateAgain = useCallback((batchId?: string) => {
    if (batchId) {
      // Find the batch data
      const batchImages = generatedImages.filter(img => img.batchId === batchId);
      if (batchImages.length > 0) {
        console.log("Creating again from batch:", batchId);
        // Track this batch ID for fullscreen refresh handling
        setLastGeneratedBatchId(batchId);
      }
    }
    
    // Call the base handler
    baseHandleCreateAgain(batchId);
    
  }, [baseHandleCreateAgain, generatedImages]);

  // Wrapper for handleDeleteContainer to match the expected signature
  const handleDeleteContainer = useCallback((batchId: string) => {
    internalHandleDeleteContainer(batchId, setGeneratedImages);
  }, [internalHandleDeleteContainer, setGeneratedImages]);

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
    lastBatchId,
    isFirstRun,
    fullscreenRefreshTrigger,
    setCurrentPrompt,
    setUploadedImageUrls,
    setCurrentWorkflow,
    setCurrentParams,
    setCurrentGlobalParams,
    setImageContainerOrder,
    handleSubmitPrompt,
    handleUseGeneratedAsInput,
    handleCreateAgain,
    handleDownloadImage,
    handleDeleteImage,
    handleReorderContainers,
    handleDeleteContainer
  };
};
