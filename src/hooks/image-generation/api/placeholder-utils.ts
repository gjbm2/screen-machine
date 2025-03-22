
import { GeneratedImage } from '../types';
import { ImageGenerationStatus } from '@/types/workflows';
import { generateImageTitle } from './title-util';

/**
 * Creates a placeholder image entry for the generation process
 */
export const createPlaceholderImage = (
  prompt: string,
  workflow: string,
  currentBatchId: string,
  nextIndex: number,
  params?: Record<string, any>,
  refiner?: string,
  refinerParams?: Record<string, any>,
  referenceImageUrl?: string,
  nextContainerId?: number
): GeneratedImage => {
  const placeholderImage: GeneratedImage = {
    url: '', 
    prompt,
    workflow,
    timestamp: Date.now(),
    batchId: currentBatchId,
    batchIndex: nextIndex,
    status: 'generating' as ImageGenerationStatus,
    params,
    refiner,
    refinerParams,
    title: generateImageTitle(prompt, workflow)
  };
  
  // Store reference image URLs if there are any
  if (referenceImageUrl) {
    placeholderImage.referenceImageUrl = referenceImageUrl;
    console.log('[placeholder-utils] Storing reference images in placeholder:', referenceImageUrl);
  }
  
  // Add containerId if this is a new batch
  if (nextContainerId) {
    placeholderImage.containerId = nextContainerId;
  }
  
  return placeholderImage;
};

/**
 * Creates multiple placeholder images for a batch
 */
export const createPlaceholderBatch = (
  prompt: string,
  workflow: string,
  currentBatchId: string,
  batchSize: number,
  images: GeneratedImage[],
  params?: Record<string, any>,
  refiner?: string,
  refinerParams?: Record<string, any>,
  referenceImageUrl?: string,
  containerId?: number
): GeneratedImage[] => {
  // Get existing indexes to avoid duplicates
  const existingBatchIndexes = new Set<number>();
  
  images.forEach(img => {
    if (img.batchId === currentBatchId && typeof img.batchIndex === 'number') {
      existingBatchIndexes.add(img.batchIndex);
    }
  });
  
  // Create a placeholder for each image in the batch
  const newPlaceholders: GeneratedImage[] = [];
  
  for (let i = 0; i < batchSize; i++) {
    const nextIndex = existingBatchIndexes.size + i;
    
    const placeholderImage = createPlaceholderImage(
      prompt,
      workflow,
      currentBatchId,
      nextIndex,
      params,
      refiner,
      refinerParams,
      referenceImageUrl,
      containerId
    );
    
    newPlaceholders.push(placeholderImage);
  }
  
  return newPlaceholders;
};
