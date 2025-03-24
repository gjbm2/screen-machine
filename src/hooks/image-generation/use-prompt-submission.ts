
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
    
    // CRITICAL: Prioritize the explicitly passed batch_size from UI
    // We get a fresh value every time from the UI which should take precedence
    const userSelectedBatchSize = globalParams?.batch_size;
    
    // Create a fresh globalParams object with the correct batch_size
    const effectiveGlobalParams = {
      ...currentGlobalParams, // Base with current settings
      ...(globalParams || {}),  // Override with any provided params
      // Explicitly set batch_size using the UI value if available
      ...(userSelectedBatchSize !== undefined ? { batch_size: userSelectedBatchSize } : {})
    };
    
    // IMPORTANT DEBUG: Log the batch size being used - this helps trace the value
    console.log("[usePromptSubmission] UI-provided batch size:", userSelectedBatchSize);
    console.log("[usePromptSubmission] Using batch size:", effectiveGlobalParams.batch_size);
    console.log("[usePromptSubmission] Full effective global params:", effectiveGlobalParams);
    
    const effectiveWorkflow = workflow || currentWorkflow;
    const effectiveWorkflowParams = workflowParams || currentParams;
    
    // Create the configuration for image generation
    const config: ImageGenerationConfig = {
      prompt,
      imageFiles: uniqueImageFiles,
      workflow: effectiveWorkflow,
      params: effectiveWorkflowParams,
      globalParams: effectiveGlobalParams, // Use the properly merged globalParams
      batchId: lastBatchIdUsed,
      refiner, 
      refinerParams
    };
    
    console.log("[usePromptSubmission] Final global params being sent:", config.globalParams);
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
    lastBatchIdUsed,
    setIsFirstRun,
    setLastBatchIdUsed,
    generateImages
  ]);

  return {
    handleSubmitPrompt
  };
};
