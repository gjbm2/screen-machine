
import { GeneratedImage } from '../types';
import { ImageGenerationStatus } from '@/types/workflows';
import { generateImageTitle } from './title-util';

/**
 * Creates placeholder images for a batch of generations
 */
export const createPlaceholderImages = (
  prompt: string,
  workflow: string,
  batchId: string,
  existingBatchIndexes: Set<number>,
  params?: Record<string, any>,
  refiner?: string,
  refinerParams?: Record<string, any>,
  referenceImageUrl?: string,
  containerId?: number
): GeneratedImage => {
  const nextIndex = existingBatchIndexes.size;
  
  // Create a placeholder entry with a generated title - this is our first and only call to generateImageTitle
  const placeholderImage: GeneratedImage = {
    url: '', 
    prompt,
    workflow,
    timestamp: Date.now(),
    batchId,
    batchIndex: nextIndex,
    status: 'generating' as ImageGenerationStatus,
    params,
    refiner,
    refinerParams,
    title: generateImageTitle(prompt, workflow)
  };
  
  // Store reference image URL if there is one
  if (referenceImageUrl) {
    placeholderImage.referenceImageUrl = referenceImageUrl;
    console.log('[image-processor] Adding reference images to placeholder:', referenceImageUrl);
  }
  
  // Add containerId if this is a new batch
  if (containerId) {
    placeholderImage.containerId = containerId;
  }
  
  return placeholderImage;
};

/**
 * Process API response and update images
 */
export const processGenerationResponse = (
  response: any,
  batchId: string,
  prompt: string,
  workflow: string,
  prevImages: GeneratedImage[],
  referenceImageUrl?: string,
  containerId?: number
): GeneratedImage[] => {
  if (!response || !response.images) {
    throw new Error('No images were returned');
  }
  
  const images = response.images;
  const newImages = [...prevImages];
  
  images.forEach((img: any, index: number) => {
    // Find the placeholder for this image
    const placeholderIndex = newImages.findIndex(
      pi => pi.batchId === batchId && pi.batchIndex === index && pi.status === 'generating'
    );
    
    if (placeholderIndex >= 0) {
      // Update the placeholder with actual data, preserving the existing title
      newImages[placeholderIndex] = {
        ...newImages[placeholderIndex],
        url: img.url,
        status: 'completed' as ImageGenerationStatus,
        timestamp: Date.now(),
      };
      
      // Only if there's no title already, generate one (this should never happen if placeholder was created properly)
      if (!newImages[placeholderIndex].title) {
        console.log("[image-processor] No title found for image, generating one");
        newImages[placeholderIndex].title = generateImageTitle(prompt, workflow);
      }
      
      // Log the updated image for debugging
      if (newImages[placeholderIndex].referenceImageUrl) {
        console.log("[image-processor] Updated image with reference image URL:", newImages[placeholderIndex].referenceImageUrl);
      }
    } else {
      // No placeholder found, this is an additional image
      // In this case we need to generate a new title since we don't have a placeholder
      const newImage: GeneratedImage = {
        url: img.url,
        prompt,
        workflow,
        timestamp: Date.now(),
        batchId,
        batchIndex: index,
        status: 'completed' as ImageGenerationStatus,
        params: {},
        title: generateImageTitle(prompt, workflow)
      };
      
      // If there's a reference image, make sure to include it
      if (referenceImageUrl) {
        newImage.referenceImageUrl = referenceImageUrl;
        console.log('[image-processor] Adding reference images to new image:', referenceImageUrl);
      }
      
      // Add containerId if this is a new batch
      if (containerId) {
        newImage.containerId = containerId;
      }
      
      newImages.push(newImage);
    }
  });
  
  return newImages;
};

/**
 * Update all images in a batch to error state
 */
export const markBatchAsError = (
  batchId: string,
  prevImages: GeneratedImage[]
): GeneratedImage[] => {
  return prevImages.map(img => {
    if (img.batchId === batchId && img.status === 'generating') {
      return {
        ...img,
        status: 'error' as ImageGenerationStatus,
        timestamp: Date.now()
      };
    }
    return img;
  });
};
