import { GeneratedImage } from '../types';

/**
 * Finds an existing container ID for a batch
 */
export const findExistingContainerId = (
  batchId: string, 
  images: GeneratedImage[]
): number | undefined => {
  // Look for any existing image with this batch ID that has a containerId
  const existingImage = images.find(
    img => img.batchId === batchId && img.containerId
  );
  
  return existingImage?.containerId;
};

/**
 * Determines if we need to create a new container ID
 * or use an existing one
 */
export const getContainerIdForBatch = (
  batchId: string | undefined,
  existingContainerId: number | undefined,
  nextContainerId: number | undefined
): number | undefined => {
  // If we have an existing container ID, use it
  if (existingContainerId) {
    return existingContainerId;
  }
  
  // If we have a next container ID and we're not reusing a batch, use the next one
  if (nextContainerId && !batchId) {
    return nextContainerId;
  }
  
  // Otherwise, don't specify a container ID
  return undefined;
};

/**
 * Gets existing batch indexes to avoid duplicates
 */
export const getExistingBatchIndexes = (
  batchId: string,
  images: GeneratedImage[]
): Set<number> => {
  const existingBatchIndexes = new Set<number>();
  
  images.forEach(img => {
    if (img.batchId === batchId && typeof img.batchIndex === 'number') {
      existingBatchIndexes.add(img.batchIndex);
    }
  });
  
  return existingBatchIndexes;
};
