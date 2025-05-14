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
  
  // Find the highest existing batch index
  let highestIndex = -1;
  existingBatchIndexes.forEach(index => {
    if (index > highestIndex) {
      highestIndex = index;
    }
  });
  
  console.log('[placeholder-utils] Highest existing batch index:', highestIndex);
  
  // Create placeholders with new indexes starting after the highest existing index
  for (let i = 0; i < batchSize; i++) {
    // Start assigning from the next available index after the highest existing one
    const nextIndex = highestIndex + 1 + i;
    const placeholderId = nanoid();
    
    const placeholder: GeneratedImage = {
      id: nanoid(), // Add required id property
      url: '', // Adding empty url to satisfy the GeneratedImage type
      batchId,
      status: 'generating',
      prompt,
      workflow,
      timestamp: Date.now(),
      batchIndex: nextIndex, // Use next available index
      placeholderId, // Add unique ID for tracking
      params,
      refiner,
      refinerParams,
      referenceImageUrl,
      containerId,
      title: `Generating image ${nextIndex + 1} of ${batchSize}...` // Add a title for display
    };
    
    console.log(`[placeholder-utils] Created placeholder with batchIndex ${nextIndex} and placeholderId ${placeholderId}`);
    placeholders.push(placeholder);

    // Emit event so Recent tab can show placeholder immediately
    if (typeof window !== 'undefined') {
      window.dispatchEvent(
        new CustomEvent('recent:placeholder', {
          detail: {
            batchId,
            placeholderId,
            prompt,
          },
        })
      );
    }
  }
  
  console.log('[placeholder-utils] Created', placeholders.length, 'placeholders');
  
  return placeholders;
};
