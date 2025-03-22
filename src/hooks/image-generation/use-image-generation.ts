
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
    setNextContainerId
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
    let uniqueImageFiles: (File | string)[] | undefined = undefined;
    
    if (imageFiles && imageFiles.length > 0) {
      // Use a Set for string URLs, and a Map for File objects
      const uniqueUrls = new Set<string>();
      const uniqueFiles = new Map<string, File>();
      
      imageFiles.forEach(item => {
        if (typeof item === 'string') {
          uniqueUrls.add(item);
        } else if (item instanceof File) {
          // Use file name and size as a unique identifier
          const fileKey = `${item.name}_${item.size}`;
          uniqueFiles.set(fileKey, item);
        }
      });
      
      uniqueImageFiles = [
        ...Array.from(uniqueUrls), 
        ...Array.from(uniqueFiles.values())
      ];
    }
    
    const config: ImageGenerationConfig = {
      prompt,
      imageFiles: uniqueImageFiles,
      workflow: currentWorkflow,
      params: currentParams,
      globalParams: currentGlobalParams,
    };
    
    generateImages(config);
  }, [
    currentWorkflow, 
    currentParams, 
    currentGlobalParams, 
    generateImages
  ]);
  
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
