
import { GeneratedImage } from '../types';
import { getExistingBatchIndexes } from './batch-utils';

/**
 * Creates a batch of placeholder images for a generation
 */
export const createPlaceholderBatch = (
  prompt: string,
  workflow: string,
  batchId: string,
  batchSize: number,
  existingImages: GeneratedImage[],
  params?: Record<string, any>,
  refiner?: string | undefined,
  refinerParams?: Record<string, any>,
  referenceImageUrl?: string,
  containerId?: number
): GeneratedImage[] => {
  // Find existing batch indexes to avoid duplicates
  const existingBatchIndexes = getExistingBatchIndexes(batchId, existingImages);
  
  // Create the specified number of placeholder images
  const placeholders: GeneratedImage[] = [];
  
  console.log('[placeholder-utils] Creating', batchSize, 'placeholders for batch', batchId, 'with containerId', containerId);
  
  for (let i = 0; i < batchSize; i++) {
    // If this index already exists in the batch, skip it
    if (existingBatchIndexes.has(i)) {
      console.log('[placeholder-utils] Skipping existing batch index', i);
      continue;
    }
    
    const placeholder: GeneratedImage = {
      batchId,
      status: 'generating',
      prompt,
      workflow,
      timestamp: Date.now(),
      batchIndex: i,
      params,
      refiner,
      refinerParams,
      referenceImageUrl,
      containerId
    };
    
    placeholders.push(placeholder);
  }
  
  console.log('[placeholder-utils] Created', placeholders.length, 'placeholders');
  
  return placeholders;
};
