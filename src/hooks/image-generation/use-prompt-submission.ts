
import { useState, useCallback } from 'react';
import { ImageGenerationConfig } from './types';

interface UsePromptSubmissionProps {
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
}: UsePromptSubmissionProps) => {
  
  const handleSubmitPrompt = useCallback(async (
    prompt: string, 
    imageFiles?: (File | string)[],
    workflow?: string,
    workflowParams?: Record<string, any>,
    globalParams?: Record<string, any>,
    refiner?: string,
    refinerParams?: Record<string, any>,
    batchId?: string
  ) => {
    try {
      // No longer first run after submitting a prompt
      setIsFirstRun(false);
      
      // Use provided workflow or fall back to current workflow
      const effectiveWorkflow = workflow || currentWorkflow;
      
      // Use provided params or fall back to current params
      const effectiveWorkflowParams = workflowParams || currentParams;
      
      // Use provided global params or fall back to current global params
      const effectiveGlobalParams = globalParams || currentGlobalParams;
      
      // Filter out null and undefined from the image files
      let uniqueImageFiles: (File | string)[] = [];
      
      if (imageFiles && imageFiles.length > 0) {
        // First, filter out null and undefined values
        uniqueImageFiles = imageFiles.filter(f => f !== null && f !== undefined);
      }
      
      // Create the configuration for image generation
      const config: ImageGenerationConfig = {
        prompt,
        imageFiles: uniqueImageFiles,
        workflow: effectiveWorkflow,
        params: effectiveWorkflowParams,
        globalParams: effectiveGlobalParams,
        // Use the provided batchId if available
        batchId: batchId,
        refiner, 
        refinerParams
      };
      
      // Generate images with this config
      const result = await generateImages(config);
      
      // Save the last used batch ID
      if (result) {
        setLastBatchIdUsed(result);
      }
      
      return result;
    } catch (error) {
      console.error('Error submitting prompt:', error);
      throw error;
    }
  }, [
    currentWorkflow, 
    currentParams, 
    currentGlobalParams, 
    setIsFirstRun, 
    setLastBatchIdUsed, 
    generateImages
  ]);
  
  return {
    handleSubmitPrompt
  };
};
