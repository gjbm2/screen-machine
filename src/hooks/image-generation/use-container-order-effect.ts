
import { useEffect } from 'react';
import { GeneratedImage } from './types';

export interface ContainerOrderEffectProps {
  generatedImages: GeneratedImage[];
  setImageContainerOrder: React.Dispatch<React.SetStateAction<string[]>>;
}

export const useContainerOrderEffect = ({
  generatedImages,
  setImageContainerOrder
}: ContainerOrderEffectProps) => {
  // Ensure generating images are always first in the order
  useEffect(() => {
    // Find any containers with 'generating' status
    const generatingBatchIds = generatedImages
      .filter(img => img.status === 'generating' || img.loading)
      .map(img => img.batchId);
    
    // Get unique batch IDs that are generating
    const uniqueGeneratingBatchIds = [...new Set(generatingBatchIds)];
    
    if (uniqueGeneratingBatchIds.length > 0) {
      // Reorder to ensure generating batches are first
      setImageContainerOrder(prev => {
        // Filter out the generating batch IDs
        const orderedContainers = prev.filter(id => !uniqueGeneratingBatchIds.includes(id));
        // Put generating batch IDs at the beginning
        return [...uniqueGeneratingBatchIds, ...orderedContainers];
      });
    }
  }, [generatedImages, setImageContainerOrder]);
};
