
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
      
      // KEY CHANGE: Use the user-specified workflow if provided, regardless of images
      let effectiveWorkflow = workflow || currentWorkflow;
      
      // Check if there are uploaded images but no workflow was explicitly provided in this function call
      // In this case, we should only check for image compatibility
      if (!workflow && imageFiles && imageFiles.length > 0) {
        const currentWorkflowObj = typedWorkflows.find(w => w.id === effectiveWorkflow);
        
        // Only change workflow if current one doesn't support images
        if (!currentWorkflowObj?.input?.includes('image')) {
          const imageWorkflow = findImageCapableWorkflow(effectiveWorkflow, imageFiles);
          if (imageWorkflow !== effectiveWorkflow) {
            console.log(`Auto-selected image-capable workflow: ${imageWorkflow} (current workflow didn't support images)`);
            effectiveWorkflow = imageWorkflow;
          }
        }
      }

      const uploadedFiles = (imageFiles || []).filter(f => f instanceof File) as File[];
      const referenceUrls = (imageFiles || []).filter(f => typeof f === 'string') as string[];

      const effectiveWorkflowParams = workflowParams || currentParams;
      const effectiveGlobalParams = globalParams || currentGlobalParams;

      console.log("usePromptSubmission: Starting new prompt submission with publishDestination:", publishDestination);

      // Get the workflow config to check if it's async
      const workflowConfig = typedWorkflows.find(w => w.id === effectiveWorkflow);
      const isAsync = workflowConfig?.async === true;

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
