
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
  console.log('[placeholder-utils] With params:', params);
  console.log('[placeholder-utils] With refiner:', refiner);
  console.log('[placeholder-utils] With refinerParams:', refinerParams);
  
  // CRITICAL FIX: Ensure each placeholder gets a unique batchIndex
  // Track available indexes for this batch
  let nextAvailableIndex = 0;
  const usedIndexes = new Set<number>(existingBatchIndexes);
  
  for (let i = 0; i < batchSize; i++) {
    // Find a unique batchIndex
    while (usedIndexes.has(nextAvailableIndex)) {
      nextAvailableIndex++;
    }
    
    // Use this unique index
    const uniqueBatchIndex = nextAvailableIndex;
    usedIndexes.add(uniqueBatchIndex);
    nextAvailableIndex++;
    
    console.log(`[placeholder-utils] Creating placeholder with batchIndex=${uniqueBatchIndex}`);
    
    const placeholder: GeneratedImage = {
      url: '', // Adding empty url to satisfy the GeneratedImage type
      batchId,
      status: 'generating',
      prompt,
      workflow,
      timestamp: Date.now(),
      batchIndex: uniqueBatchIndex, // Use our unique index
      params,
      refiner,
      refinerParams,
      referenceImageUrl,
      containerId
    };
    
    placeholders.push(placeholder);
  }
  
  // Log the generated placeholders for debugging
  console.log('[placeholder-utils] Created', placeholders.length, 'placeholders with batchIndexes:', 
    placeholders.map(p => p.batchIndex));
  
  return placeholders;
};
