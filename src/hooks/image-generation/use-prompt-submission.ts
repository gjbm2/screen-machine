import { useState, useCallback } from 'react';
import { ImageGenerationConfig } from './types';
import { findImageCapableWorkflow } from '@/utils/workflow-utils';

interface UsePromptSubmissionProps {
  currentWorkflow: string;
  currentParams: Record<string, any>;
  currentGlobalParams: Record<string, any>;
  lastBatchIdUsed: string | null;
  setIsFirstRun: React.Dispatch<React.SetStateAction<boolean>>;
  setLastBatchIdUsed: React.Dispatch<React.SetStateAction<string | null>>;
  generateImages: (config: ImageGenerationConfig) => Promise<string | null>;
  collapseAllExcept?: (batchId: string) => void;
}

export const usePromptSubmission = ({
  currentWorkflow,
  currentParams,
  currentGlobalParams,
  lastBatchIdUsed,
  setIsFirstRun,
  setLastBatchIdUsed,
  generateImages,
  collapseAllExcept
}: UsePromptSubmissionProps) => {
  
  const handleSubmitPrompt = useCallback(async (
    prompt: string, 
    imageFiles?: (File | string)[],
    workflow?: string,
    workflowParams?: Record<string, any>,
    globalParams?: Record<string, any>,
    refiner?: string,
    refinerParams?: Record<string, any>,
    publishDestination?: string,
    batchId?: string
  ) => {
    try {
      setIsFirstRun(false);
      
      let effectiveWorkflow = workflow || currentWorkflow;
      
      // Use the utility function to find image capable workflow
      if (imageFiles && imageFiles.length > 0) {
        effectiveWorkflow = findImageCapableWorkflow(effectiveWorkflow, true);
        console.log(`Auto-selected image-capable workflow: ${effectiveWorkflow}`);
      }
      
      const effectiveWorkflowParams = workflowParams || currentParams;
      const effectiveGlobalParams = globalParams || currentGlobalParams;
      
      let uniqueImageFiles: (File | string)[] = [];
      
      if (imageFiles && imageFiles.length > 0) {
        uniqueImageFiles = imageFiles.filter(f => f !== null && f !== undefined);
      }
      
      console.log("usePromptSubmission: Starting new prompt submission with publishDestination:", publishDestination);
      
      const config: ImageGenerationConfig = {
        prompt,
        imageFiles: uniqueImageFiles,
        workflow: effectiveWorkflow,
        params: effectiveWorkflowParams,
        globalParams: effectiveGlobalParams,
        batchId,
        refiner, 
        refinerParams
      };
      
      const result = await generateImages(config);
      
      if (result && collapseAllExcept && !batchId) {
        collapseAllExcept(result);
      }
      
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
    generateImages,
    collapseAllExcept
  ]);
  
  return {
    handleSubmitPrompt
  };
};
