
import { GeneratedImage } from '../types';

/**
 * Gets existing batch indexes to avoid duplication
 */
export const getExistingBatchIndexes = (
  batchId: string, 
  images: GeneratedImage[]
): Set<number> => {
  const indexes = new Set<number>();
  
  images.forEach(img => {
    if (img.batchId === batchId && typeof img.batchIndex === 'number') {
      indexes.add(img.batchIndex);
    }
  });
  
  return indexes;
};

/**
 * Finds an existing container ID for a batch
 */
export const findExistingContainerId = (
  batchId: string,
  images: GeneratedImage[]
): number | undefined => {
  const batchImage = images.find(img => img.batchId === batchId && img.containerId !== undefined);
  return batchImage?.containerId;
};

/**
 * Determines which container ID to use for a batch
 */
export const getContainerIdForBatch = (
  batchId?: string, 
  existingContainerId?: number, 
  nextContainerId?: number
): number | undefined => {
  if (batchId && existingContainerId) {
    // If we have a batch ID and a container ID for it, use that
    return existingContainerId;
  } else if (nextContainerId) {
    // If we're creating a new batch, use the next container ID
    return nextContainerId;
  }
  
  // If we can't determine a container ID, return undefined
  return undefined;
};
