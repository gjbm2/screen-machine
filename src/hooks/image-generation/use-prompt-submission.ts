
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
  setCurrentWorkflow?: (workflowId: string) => void; // Add this to allow forced workflow changes
}

export const usePromptSubmission = ({
  currentWorkflow,
  currentParams,
  currentGlobalParams,
  lastBatchIdUsed,
  setIsFirstRun,
  setLastBatchIdUsed,
  generateImages,
  collapseAllExcept,
  setCurrentWorkflow
}: UsePromptSubmissionProps) => {
  
  const findImageCapableWorkflow = (currentWorkflowId: string, imageFiles?: (File | string)[]) => {
    if (!imageFiles || imageFiles.length === 0) {
      console.log(`No images provided, keeping current workflow: ${currentWorkflowId}`);
      return currentWorkflowId;
    }
    
    console.log(`Checking if current workflow ${currentWorkflowId} supports images...`);
    
    const currentWorkflowObj = typedWorkflows.find(w => w.id === currentWorkflowId);
    
    if (currentWorkflowObj && currentWorkflowObj.input) {
      const inputType = currentWorkflowObj.input;
      console.log(`Current workflow input type:`, inputType);
      
      if (typeof inputType === 'string' && inputType === 'image') {
        console.log(`Current workflow ${currentWorkflowId} already supports images (string), keeping it`);
        return currentWorkflowId;
      } 
      
      if (Array.isArray(inputType) && inputType.includes('image')) {
        console.log(`Current workflow ${currentWorkflowId} already supports images (array), keeping it`);
        return currentWorkflowId;
      }
    }
    
    console.log(`Current workflow ${currentWorkflowId} does not support images, looking for an image-capable workflow...`);
    
    // First look for workflows that accept both image and text
    const imageAndTextWorkflow = typedWorkflows.find(w => {
      if (!w.input) return false;
      
      if (Array.isArray(w.input)) {
        return w.input.includes('image') && w.input.includes('text');
      }
      
      return false;
    });

    if (imageAndTextWorkflow) {
      console.log(`Found image+text capable workflow: ${imageAndTextWorkflow.id}`);
      return imageAndTextWorkflow.id;
    }
    
    // If no combined workflow found, look for image-only workflow
    const imageWorkflow = typedWorkflows.find(w => {
      if (!w.input) return false;
      
      if (typeof w.input === 'string') {
        return w.input === 'image';
      } 
      
      if (Array.isArray(w.input)) {
        return w.input.includes('image');
      }
      
      return false;
    });
    
    if (imageWorkflow) {
      console.log(`Found image-capable workflow: ${imageWorkflow.id}`);
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
      
      console.log(`handleSubmitPrompt called with:`, {
        prompt,
        imageFilesCount: imageFiles?.length || 0,
        workflow,
        currentWorkflow
      });
      
      let effectiveWorkflow = workflow || currentWorkflow;
      
      if (imageFiles && imageFiles.length > 0 && !workflow) {
        const imageCapableWorkflow = findImageCapableWorkflow(effectiveWorkflow, imageFiles);
        
        if (imageCapableWorkflow !== effectiveWorkflow) {
          console.log(`Auto-switching to image-capable workflow: ${imageCapableWorkflow} (from ${effectiveWorkflow})`);
          effectiveWorkflow = imageCapableWorkflow;
          
          // Force workflow update in parent component
          if (setCurrentWorkflow) {
            console.log('Updating parent workflow state via setCurrentWorkflow');
            setCurrentWorkflow(imageCapableWorkflow);
          }
        }
      }
      
      const effectiveWorkflowParams = workflowParams || currentParams;
      const effectiveGlobalParams = globalParams || currentGlobalParams;
      
      let uniqueImageFiles: (File | string)[] = [];
      
      if (imageFiles && imageFiles.length > 0) {
        uniqueImageFiles = imageFiles.filter(f => f !== null && f !== undefined);
        console.log(`Processing ${uniqueImageFiles.length} image files with workflow: ${effectiveWorkflow}`);
      }
      
      console.log("usePromptSubmission: Starting generation with:", {
        workflow: effectiveWorkflow,
        publishDestination,
        imageCount: uniqueImageFiles.length
      });
      
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
    collapseAllExcept,
    setCurrentWorkflow
  ]);
  
  return {
    handleSubmitPrompt
  };
};
