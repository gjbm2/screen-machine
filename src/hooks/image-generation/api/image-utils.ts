
import { GeneratedImage } from '../types';
import { ImageGenerationStatus } from '@/types/workflows';

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
    refinerParams
  };
  
  // Store reference image URLs if there are any
  if (referenceImageUrl) {
    placeholderImage.referenceImageUrl = referenceImageUrl;
    console.log('Storing reference images in placeholder:', placeholderImage.referenceImageUrl);
  }
  
  // Add containerId if this is a new batch
  if (nextContainerId) {
    placeholderImage.containerId = nextContainerId;
  }
  
  return placeholderImage;
};

/**
 * Updates a placeholder image with actual generation results
 */
export const updateImageWithResult = (
  placeholder: GeneratedImage,
  imageUrl: string
): GeneratedImage => {
  return {
    ...placeholder,
    url: imageUrl,
    status: 'completed' as ImageGenerationStatus,
    timestamp: Date.now(),
  };
};

/**
 * Updates a placeholder image to show an error state
 */
export const updateImageWithError = (
  placeholder: GeneratedImage
): GeneratedImage => {
  return {
    ...placeholder,
    status: 'error' as ImageGenerationStatus,
    timestamp: Date.now()
  };
};

/**
 * Processes uploaded files and returns arrays of files and URLs
 */
export const processUploadedFiles = (
  imageFiles?: File[] | string[]
): { uploadedFiles: File[], uploadedImageUrls: string[] } => {
  let uploadedFiles: File[] = [];
  let uploadedImageUrls: string[] = [];
  
  if (imageFiles && imageFiles.length > 0) {
    for (const file of imageFiles) {
      if (typeof file === 'string') {
        uploadedImageUrls.push(file);
      } else {
        uploadedFiles.push(file);
      }
    }
  }
  
  return { uploadedFiles, uploadedImageUrls };
};
