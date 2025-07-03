import { useState, useCallback } from 'react';
import { ImageGenerationConfig } from './types';
import typedWorkflows from '@/data/typedWorkflows';

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
      
      // Use the user-specified workflow if provided, otherwise use current workflow
      let effectiveWorkflow = workflow || currentWorkflow;
      
      // Let the backend handle all workflow selection logic
      console.log(`[usePromptSubmission] Using workflow: ${effectiveWorkflow} - letting backend handle selection logic`);

      const uploadedFiles = (imageFiles || []).filter(f => f instanceof File) as File[];
      const referenceUrls = (imageFiles || []).filter(f => typeof f === 'string') as string[];

      const effectiveWorkflowParams = workflowParams || currentParams;
      const effectiveGlobalParams = globalParams || currentGlobalParams;

      console.log("usePromptSubmission: Starting new prompt submission with publishDestination:", publishDestination);

      // Skip async check for "auto" workflow since we don't know which workflow will be selected
      let isAsync = false;
      if (effectiveWorkflow !== 'auto') {
        const workflowConfig = typedWorkflows.find(w => w.id === effectiveWorkflow);
        isAsync = workflowConfig?.async === true;
      }

      if (isAsync) {
        console.log(`[usePromptSubmission] Async workflow detected (${effectiveWorkflow}), handling differently`);
      }

      const config: ImageGenerationConfig = {
        prompt,
        imageFiles: uploadedFiles,
        referenceUrls, 
        workflow: effectiveWorkflow,
        params: effectiveWorkflowParams,
        globalParams: effectiveGlobalParams,
        batchId,
        refiner, 
        refinerParams,
        isAsync
      };

      const result = await generateImages(config);

      // Only collapse other containers if this is NOT an async workflow
      if (result && collapseAllExcept && !batchId && !isAsync) {
        collapseAllExcept(result);
      }

      if (result && !isAsync) {
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
