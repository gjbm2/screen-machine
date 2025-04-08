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
    
    const currentWorkflowObj = workflows.find(w => w.id === currentWorkflowId);
    if (currentWorkflowObj && currentWorkflowObj.input && 
        (currentWorkflowObj.input.includes('image') || 
         (Array.isArray(currentWorkflowObj.input) && currentWorkflowObj.input.includes('image')))) {
      console.log(`Current workflow ${currentWorkflowId} already supports images, keeping it`);
      return currentWorkflowId;
    }
    
    const imageWorkflow = workflows.find(w => 
      w.input && 
      (typeof w.input === 'string' ? 
        w.input === 'image' || w.input.includes('image') : 
        Array.isArray(w.input) && w.input.includes('image'))
    );
    
    if (imageWorkflow) {
      console.log(`Switching to image-capable workflow: ${imageWorkflow.id}`);
      return imageWorkflow.id;
    }
    
    console.log(`No image-capable workflow found, keeping current: ${currentWorkflowId}`);
    return currentWorkflowId;
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
      
      if (imageFiles && imageFiles.length > 0 && !workflow) {
        const imageCapableWorkflow = findImageCapableWorkflow(effectiveWorkflow, imageFiles);
        effectiveWorkflow = imageCapableWorkflow;
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
