
import { useState, useCallback } from 'react';
import { nanoid } from '@/lib/utils';
import { generateImage } from './api/image-generator';
import { ImageGenerationConfig } from './types';

export const useImageGenerationApi = (
  addConsoleLog: (log: any) => void,
  setGeneratedImages: React.Dispatch<React.SetStateAction<any[]>>,
  setImageContainerOrder: React.Dispatch<React.SetStateAction<string[]>>,
  nextContainerId: number,
  setNextContainerId: React.Dispatch<React.SetStateAction<number>>,
  onGenerationComplete?: () => void
) => {
  const [activeGenerations, setActiveGenerations] = useState<string[]>([]);
  const [lastBatchId, setLastBatchId] = useState<string | null>(null);

  // Function to generate new images based on config
  const generateImages = useCallback(async (config: ImageGenerationConfig): Promise<string | null> => {
    try {
      // Always use provided batch ID or generate a new one
      const batchId = config.batchId || nanoid();
      setLastBatchId(batchId);
      
      // Add to active generations
      setActiveGenerations(prev => [...prev, batchId]);
      
      // Always add this batch ID to the container order if it's a new batch
      // (which it will be except when using "Go Again" functionality)
      if (!config.batchId) {
        // Add the new batch to the beginning of the order
        setImageContainerOrder(prev => [batchId, ...prev]);
        
        // When starting a new generation without an existing batchId,
        // we need to ensure this container is expanded and all others
        // are collapsed. This is now handled in useImageDisplayState's useEffect
        // but we still need to make sure the order is correct
      }
      
      // Log this generation to the console
      addConsoleLog({
        type: 'info',
        message: `Starting image generation for prompt: "${config.prompt}"`,
        data: {
          batchId,
          workflow: config.workflow,
          params: config.params,
          globalParams: config.globalParams,
          refiner: config.refiner,
          refinerParams: config.refinerParams,
          isReusingBatch: !!config.batchId
        }
      });
      
      // Create the payload for the image generator
      const generationConfig = {
        prompt: config.prompt,
        imageFiles: config.imageFiles,
        workflow: config.workflow,
        params: config.params,
        globalParams: config.globalParams,
        refiner: config.refiner,
        refinerParams: config.refinerParams,
        batchId: batchId,
        nextContainerId
      };
      
      // Set up the actions needed by the image generator
      const generationActions = {
        addConsoleLog,
        setGeneratedImages,
        setImageContainerOrder,
        setNextContainerId,
        setActiveGenerations
      };
      
      // Actually generate the image
      const result = await generateImage(generationConfig, generationActions);
      
      // Always call the completion callback whether successful or not
      if (onGenerationComplete) {
        onGenerationComplete();
      }
      
      // Return the batch ID for reference
      return batchId;
      
    } catch (error) {
      console.error('Error generating image:', error);
      addConsoleLog({
        type: 'error',
        message: 'Error generating image',
        data: error
      });
      
      // Always call the completion callback even on error
      if (onGenerationComplete) {
        onGenerationComplete();
      }
      
      // Return null on error
      return null;
    }
  }, [
    addConsoleLog, 
    setGeneratedImages, 
    setImageContainerOrder, 
    nextContainerId, 
    setNextContainerId,
    onGenerationComplete,
    setActiveGenerations
  ]);

  return {
    activeGenerations,
    lastBatchId,
    generateImages,
  };
};
