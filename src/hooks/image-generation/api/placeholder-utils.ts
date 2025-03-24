
import { nanoid } from '@/lib/utils';
import { GeneratedImage } from '../types';

/**
 * Create placeholder images for a batch
 */
export const createPlaceholderBatch = (
  prompt: string,
  workflow: string,
  batchId: string,
  batchSize: number,
  prevImages: GeneratedImage[],
  params: Record<string, any> = {},
  refiner?: string,
  refinerParams?: Record<string, any>,
  referenceImageUrl?: string,
  containerId?: number
): GeneratedImage[] => {
  // Check if there are existing placeholders for this batch
  const existingPlaceholders = prevImages.filter(img => img.batchId === batchId);
  
  if (existingPlaceholders.length > 0) {
    console.log(`[placeholder-utils] Using ${existingPlaceholders.length} existing placeholders for batch ${batchId}`);
    return []; // Return empty array to avoid duplicating placeholders
  }
  
  console.log(`[placeholder-utils] Creating ${batchSize} placeholders for batch ${batchId}`);
  
  const placeholders: GeneratedImage[] = [];
  
  for (let i = 0; i < batchSize; i++) {
    placeholders.push({
      id: nanoid(),
      url: '',
      prompt,
      workflow,
      batchId,
      batchIndex: i,
      loading: true,
      error: false,
      timestamp: Date.now(),
      containerId,
      params, // Store workflow params
      refiner, // Store refiner
      refinerParams, // Store refiner params
      referenceImageUrl,
      title: generateTitle(prompt, workflow, i)
    });
  }
  
  return placeholders;
};

/**
 * Generate a title for a placeholder image
 */
const generateTitle = (prompt: string, workflow: string, index: number): string => {
  const shortPrompt = prompt.length > 30 ? prompt.substring(0, 30) + '...' : prompt;
  return `${shortPrompt} (${workflow})`;
};
