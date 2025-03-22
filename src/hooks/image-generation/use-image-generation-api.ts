
import { useState, useCallback } from 'react';
import { nanoid } from '@/lib/utils';
import { generateImage, checkImageStatus } from './api/image-generator';
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
      
      // Actually generate the image
      const result = await generateImage(
        config.prompt,
        config.workflow,
        config.params,
        config.globalParams,
        config.imageFiles,
        batchId,
        addConsoleLog
      );
      
      if (result) {
        // Handle successful generation
        updateGeneratedImage(batchId, result.imageUrl, config.prompt, config.workflow);
        
        // Call the onGenerationComplete callback if provided
        if (onGenerationComplete) {
          onGenerationComplete();
        }
      } else {
        // Handle failed generation
        markGenerationAsFailed(batchId);
        
        // Also trigger refresh callback on error
        if (onGenerationComplete) {
          onGenerationComplete();
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
        onGenerationComplete();
      }
    }
  }, [
    addConsoleLog, 
    setGeneratedImages, 
    setImageContainerOrder, 
    nextContainerId, 
    setNextContainerId,
    onGenerationComplete
  ]);

  // Helper to update an image from generating to completed
  const updateGeneratedImage = useCallback((batchId: string, imageUrl: string, prompt: string, workflow: string) => {
    setGeneratedImages(prev => {
      return prev.map(image => {
        if (image.batchId === batchId) {
          return {
            ...image,
            url: imageUrl,
            prompt,
            workflow,
            status: 'completed',
            batchIndex: 0
          };
        }
        return image;
      });
    });
    
    // Remove this batch from active generations
    setActiveGenerations(prev => prev.filter(id => id !== batchId));
  }, [setGeneratedImages]);
  
  // Helper to mark a generation as failed
  const markGenerationAsFailed = useCallback((batchId: string) => {
    setGeneratedImages(prev => {
      return prev.map(image => {
        if (image.batchId === batchId) {
          return {
            ...image,
            status: 'failed'
          };
        }
        return image;
      });
    });
    
    // Remove this batch from active generations
    setActiveGenerations(prev => prev.filter(id => id !== batchId));
  }, [setGeneratedImages]);

  return {
    activeGenerations,
    lastBatchId,
    generateImages,
  };
};
