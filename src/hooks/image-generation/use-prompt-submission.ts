
import { useCallback } from 'react';
import { ImageGenerationConfig } from './types';

export interface PromptSubmissionProps {
  currentWorkflow: string;
  currentParams: Record<string, any>;
  currentGlobalParams: Record<string, any>;
  lastBatchIdUsed: string | null;
  setIsFirstRun: React.Dispatch<React.SetStateAction<boolean>>;
  setLastBatchIdUsed: React.Dispatch<React.SetStateAction<string | null>>;
  generateImages: (config: ImageGenerationConfig) => Promise<string | null>;
}

export const usePromptSubmission = ({
  currentWorkflow,
  currentParams,
  currentGlobalParams,
  lastBatchIdUsed,
  setIsFirstRun,
  setLastBatchIdUsed,
  generateImages
}: PromptSubmissionProps) => {
  
  const handleSubmitPrompt = useCallback(async (
    prompt: string, 
    imageFiles?: File[] | string[],
    workflow?: string,
    workflowParams?: Record<string, any>,
    globalParams?: Record<string, any>,
    refiner?: string,
    refinerParams?: Record<string, any>
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
        const allUrls = [...urlStrings];
        uniqueImageFiles = [...new Set(allUrls)];
      }
    }
    
    // We need to ensure we're using the exact latest provided parameters every time
    // This is crucial for ensuring the batch size and other params are current
    const effectiveGlobalParams = globalParams || currentGlobalParams;
    
    // Create the configuration for image generation, prioritizing newly provided params
    const config: ImageGenerationConfig = {
      prompt,
      imageFiles: uniqueImageFiles,
      workflow: workflow || currentWorkflow,
      params: workflowParams || currentParams,
      // Use the most current global params
      globalParams: effectiveGlobalParams,
      batchId: lastBatchIdUsed,
      refiner, // Add explicit refiner support
      refinerParams // Add explicit refiner params support
    };
    
    console.log("[usePromptSubmission] Using global params:", config.globalParams);
    console.log("[usePromptSubmission] Using workflow:", config.workflow);
    console.log("[usePromptSubmission] Using refiner:", config.refiner);
    
    // Call generateImages and store the returned batchId
    try {
      // Generate images and possibly get a batch ID
      const result = await generateImages(config);
      
      // Only update lastBatchIdUsed if we got a valid string back
      if (result !== null && typeof result === 'string') {
        setLastBatchIdUsed(result);
      }
    } catch (error) {
      console.error("Error during image generation:", error);
    }
  }, [
    currentWorkflow, 
    currentParams, 
    currentGlobalParams, 
    generateImages,
    lastBatchIdUsed,
    setIsFirstRun,
    setLastBatchIdUsed
  ]);

  return {
    handleSubmitPrompt
  };
};
