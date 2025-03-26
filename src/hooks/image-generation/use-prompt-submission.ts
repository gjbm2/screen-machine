
import { useCallback, useState } from 'react';

interface UsePromptSubmissionProps {
  currentWorkflow: string;
  currentParams: Record<string, any>;
  currentGlobalParams: Record<string, any>;
  lastBatchIdUsed: string | null;
  setIsFirstRun: React.Dispatch<React.SetStateAction<boolean>>;
  setLastBatchIdUsed: React.Dispatch<React.SetStateAction<string | null>>;
  generateImages: (config: any) => Promise<string | null>;
  collapseAllExcept: (batchId: string) => void;
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
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Function to handle submitting the prompt to generate images
  const handleSubmitPrompt = useCallback(async (
    prompt: string,
    imageFiles?: File[] | string[],
    workflow?: string,
    params?: Record<string, any>,
    globalParams?: Record<string, any>,
    refiner?: string, 
    refinerParams?: Record<string, any>
  ) => {
    try {
      setIsSubmitting(true);
      
      // Don't allow submitting if we're already submitting
      if (isSubmitting) {
        console.warn('Ignoring prompt submission because another one is in progress');
        return null;
      }
      
      // Mark that the app has been used at least once
      setIsFirstRun(false);
      
      const config = {
        prompt,
        imageFiles,
        workflow: workflow || currentWorkflow,
        params: params || currentParams,
        globalParams: globalParams || currentGlobalParams,
        refiner: refiner || 'none',
        refinerParams: refinerParams || {},
      };
      
      // Generate the images
      const batchId = await generateImages(config);
      
      // If we successfully generated a batch, store its ID
      if (batchId) {
        setLastBatchIdUsed(batchId);
        
        // Now collapse all containers except the one we just created
        collapseAllExcept(batchId);
      }
      
      return batchId;
    } catch (error) {
      console.error('Error submitting prompt:', error);
      return null;
    } finally {
      setIsSubmitting(false);
    }
  }, [
    currentWorkflow, 
    currentParams, 
    currentGlobalParams, 
    generateImages, 
    setIsFirstRun, 
    setLastBatchIdUsed, 
    isSubmitting, 
    collapseAllExcept
  ]);

  return {
    handleSubmitPrompt,
    isSubmitting
  };
};
