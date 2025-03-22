
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
  onGenerationComplete?: (batchId: string) => void
) => {
  const [activeGenerations, setActiveGenerations] = useState<string[]>([]);
  const [lastBatchId, setLastBatchId] = useState<string | null>(null);

  // Function to generate new images based on config
  const generateImages = useCallback(async (config: ImageGenerationConfig) => {
    try {
      // Generate a unique batch ID for this generation
      const batchId = nanoid();
      setLastBatchId(batchId);
      
      // Add to active generations
      setActiveGenerations(prev => [...prev, batchId]);
      
      // Add this batch ID to the container order (at the beginning)
      setImageContainerOrder(prev => [batchId, ...prev]);
      
      // Add a placeholder for the generating image
      setGeneratedImages(prev => [
        {
          batchId,
          status: 'generating',
          prompt: config.prompt,
          workflow: config.workflow,
          timestamp: Date.now(),
          params: config.params,
          containerId: nextContainerId,
          referenceImageUrl: config.imageFiles && config.imageFiles.length > 0 && typeof config.imageFiles[0] === 'string' 
            ? config.imageFiles[0] 
            : undefined
        },
        ...prev
      ]);
      
      // Increment the container ID for the next batch
      setNextContainerId(prev => prev + 1);
      
      // Log this generation to the console
      addConsoleLog({
        type: 'info',
        message: `Starting image generation for prompt: "${config.prompt}"`,
        data: {
          batchId,
          workflow: config.workflow,
          params: config.params,
          globalParams: config.globalParams,
        }
      });
      
      // Create the payload for the image generator
      const generationConfig = {
        prompt: config.prompt,
        imageFiles: config.imageFiles,
        workflow: config.workflow,
        params: config.params,
        globalParams: config.globalParams,
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
      
      if (result) {
        // Call the onGenerationComplete callback if provided, passing the batchId
        if (onGenerationComplete) {
          onGenerationComplete(batchId);
        }
      } else {
        // Also trigger refresh callback on error
        if (onGenerationComplete) {
          onGenerationComplete(batchId);
        }
      }
    } catch (error) {
      console.error('Error generating image:', error);
      addConsoleLog({
        type: 'error',
        message: 'Error generating image',
        data: error
      });
      
      // If we have a completion callback, call it even on error
      if (onGenerationComplete) {
        onGenerationComplete(lastBatchId || '');
      }
    }
  }, [
    addConsoleLog, 
    setGeneratedImages, 
    setImageContainerOrder, 
    nextContainerId, 
    setNextContainerId,
    onGenerationComplete,
    setActiveGenerations,
    lastBatchId
  ]);

  return {
    activeGenerations,
    lastBatchId,
    generateImages,
  };
};
