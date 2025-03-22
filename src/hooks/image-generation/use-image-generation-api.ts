
import { useState, useCallback } from 'react';
import { GeneratedImage } from './types';
import { ImageGenerationConfig } from './types';
import { generateImage } from './api/image-generator';

export const useImageGenerationApi = (
  addConsoleLog: (log: any) => void,
  setGeneratedImages: React.Dispatch<React.SetStateAction<GeneratedImage[]>>,
  setImageContainerOrder: React.Dispatch<React.SetStateAction<string[]>>,
  nextContainerId: number,
  setNextContainerId: React.Dispatch<React.SetStateAction<number>>
) => {
  const [activeGenerations, setActiveGenerations] = useState<string[]>([]);
  const [lastBatchId, setLastBatchId] = useState<string | null>(null);

  const generateImages = useCallback(async (config: ImageGenerationConfig) => {
    // Create a consistent batch ID
    const batchId = await generateImage(
      {
        ...config,
        nextContainerId
      },
      {
        addConsoleLog,
        setGeneratedImages,
        setImageContainerOrder,
        setNextContainerId,
        setActiveGenerations
      }
    );
    
    // Save the last batch ID if generation was successful
    if (batchId) {
      setLastBatchId(batchId);
    }
    
    return batchId;
  }, [
    addConsoleLog, 
    setGeneratedImages, 
    setImageContainerOrder, 
    nextContainerId,
    setNextContainerId
  ]);

  return {
    activeGenerations,
    lastBatchId,
    generateImages,
  };
};

export default useImageGenerationApi;
