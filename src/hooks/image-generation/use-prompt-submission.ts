
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
    
    // Use the provided parameters or fall back to current settings
    const effectiveWorkflow = workflow || currentWorkflow;
    const effectiveWorkflowParams = workflowParams || currentParams;
    const effectiveGlobalParams = globalParams ? { ...globalParams } : { ...currentGlobalParams };
    
    // Log the parameters being used
    console.log("[usePromptSubmission] Using workflow:", effectiveWorkflow);
    console.log("[usePromptSubmission] Using workflow params:", effectiveWorkflowParams);
    console.log("[usePromptSubmission] Using global params:", effectiveGlobalParams);
    console.log("[usePromptSubmission] Using refiner:", refiner);
    console.log("[usePromptSubmission] Using refiner params:", refinerParams);
    
    // Check for publish destination in workflow params
    const publishDestination = effectiveWorkflowParams.publish_destination;
    if (publishDestination) {
      console.log("[usePromptSubmission] Using publish destination:", publishDestination);
    }
    
    // Create the configuration for image generation
    const config: ImageGenerationConfig = {
      prompt,
      imageFiles: uniqueImageFiles,
      workflow: effectiveWorkflow,
      params: effectiveWorkflowParams,
      globalParams: effectiveGlobalParams,
      batchId: lastBatchIdUsed,
      refiner, 
      refinerParams
    };
    
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
    lastBatchIdUsed,
    setIsFirstRun,
    setLastBatchIdUsed,
    generateImages
  ]);

  return {
    handleSubmitPrompt
  };
};
