
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
  
  // CRITICAL FIX: Ensure each placeholder gets a unique and sequential batchIndex
  for (let i = 0; i < batchSize; i++) {
    // IMPORTANT: Each image must have a unique batchIndex
    // If this index already exists in the batch, find the next available index
    let batchIndex = i;
    while (existingBatchIndexes.has(batchIndex)) {
      batchIndex++;
      console.log('[placeholder-utils] Adjusting batchIndex to avoid conflict, now using:', batchIndex);
    }
    
    // Mark this index as used to prevent duplicates
    existingBatchIndexes.add(batchIndex);
    
    console.log('[placeholder-utils] Creating placeholder with batchIndex:', batchIndex);
    
    const placeholder: GeneratedImage = {
      url: '', // Adding empty url to satisfy the GeneratedImage type
      batchId,
      status: 'generating',
      prompt,
      workflow,
      timestamp: Date.now(),
      batchIndex: batchIndex, // Use the unique batchIndex
      params,
      refiner,
      refinerParams,
      referenceImageUrl,
      containerId
    };
    
    placeholders.push(placeholder);
  }
  
  console.log('[placeholder-utils] Created', placeholders.length, 'placeholders with batchIndexes:', 
    placeholders.map(p => p.batchIndex));
  
  return placeholders;
};
