
import { GeneratedImage } from '../types';
import { getExistingBatchIndexes } from './batch-utils';
import { nanoid } from '@/lib/utils';

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
  
  for (let i = 0; i < batchSize; i++) {
    // If this index already exists in the batch, skip it
    if (existingBatchIndexes.has(i)) {
      console.log('[placeholder-utils] Skipping existing batch index', i);
      continue;
    }
    
    // Generate a unique placeholder ID for tracking
    const placeholderId = nanoid();
    
    const placeholder: GeneratedImage = {
      url: '', // Adding empty url to satisfy the GeneratedImage type
      batchId,
      status: 'generating',
      prompt,
      workflow,
      timestamp: Date.now(),
      batchIndex: i, // Use sequential batch index
      placeholderId, // Add unique ID for tracking
      params,
      refiner,
      refinerParams,
      referenceImageUrl,
      containerId
    };
    
    console.log(`[placeholder-utils] Created placeholder with batchIndex ${i} and placeholderId ${placeholderId}`);
    placeholders.push(placeholder);
  }
  
  console.log('[placeholder-utils] Created', placeholders.length, 'placeholders');
  
  return placeholders;
};
