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
  
  const findImageCapableWorkflow = (currentWorkflowId: string, imageFiles?: (File | string)[]) => {
    if (!imageFiles || imageFiles.length === 0) {
      return currentWorkflowId;
    }
    
    const workflows = typedWorkflows;
    const currentIndex = workflows.findIndex(w => w.id === currentWorkflowId);
    
    if (currentIndex === -1) {
      const firstImageWorkflow = workflows.find(w => w.input && w.input.includes('image'));
      return firstImageWorkflow ? firstImageWorkflow.id : currentWorkflowId;
    }
    
    const currentWorkflow = workflows[currentIndex];
    if (currentWorkflow.input && currentWorkflow.input.includes('image')) {
      return currentWorkflowId;
    }
    
    let nextImageWorkflowId = currentWorkflowId;
    
    for (let i = currentIndex + 1; i < workflows.length; i++) {
      if (workflows[i].input && workflows[i].input.includes('image')) {
        nextImageWorkflowId = workflows[i].id;
        break;
      }
    }
    
    if (nextImageWorkflowId === currentWorkflowId) {
      for (let i = 0; i < currentIndex; i++) {
        if (workflows[i].input && workflows[i].input.includes('image')) {
          nextImageWorkflowId = workflows[i].id;
          break;
        }
      }
    }
    
    return nextImageWorkflowId;
  };
  
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
      if (imageFiles && imageFiles.length > 0) {
        effectiveWorkflow = findImageCapableWorkflow(effectiveWorkflow, imageFiles);
        console.log(`Auto-selected image-capable workflow: ${effectiveWorkflow}`);
      }

      // NEW: Split image files into files and reference URLs
      const uploadedFiles = (imageFiles || []).filter(f => f instanceof File) as File[];
      const referenceUrls = (imageFiles || []).filter(f => typeof f === 'string') as string[];

      const effectiveWorkflowParams = workflowParams || currentParams;
      const effectiveGlobalParams = globalParams || currentGlobalParams;

      console.log("usePromptSubmission: Starting new prompt submission with publishDestination:", publishDestination);

      const workflowConfig = typedWorkflows.find(w => w.id === effectiveWorkflow);
      const isAsync = workflowConfig?.async === true;

      // NEW: optionally skip placeholder creation if async
      if (isAsync) {
        console.log(`[usePromptSubmission] Async workflow detected (${effectiveWorkflow}), skipping placeholder handling`);
      }

      const config: ImageGenerationConfig = {
        prompt,
        imageFiles: uploadedFiles,
        referenceUrls, // NEW: pass reference image URLs separately
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
