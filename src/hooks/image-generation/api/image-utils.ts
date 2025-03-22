
import { nanoid } from '@/lib/utils';
import { GeneratedImage } from '../types';
import { ImageGenerationStatus } from '@/types/workflows';

/**
 * Handles processing of uploaded image files and URLs
 */
export const processUploadedFiles = (imageFiles?: File[] | string[]) => {
  const uploadedFiles: File[] = [];
  const uploadedImageUrls: string[] = [];
  
  if (imageFiles && imageFiles.length > 0) {
    imageFiles.forEach(file => {
      if (typeof file === 'string') {
        uploadedImageUrls.push(file);
      } else if (file instanceof File) {
        uploadedFiles.push(file);
      }
    });
  }
  
  return { uploadedFiles, uploadedImageUrls };
};

/**
 * Creates a placeholder image object while the image is being generated
 */
export const createPlaceholderImage = (
  prompt: string,
  workflow: string,
  batchId: string,
  batchIndex: number,
  params?: Record<string, any>,
  refiner?: string,
  refinerParams?: Record<string, any>,
  referenceImageUrl?: string,
  containerId?: number,
  title?: string
): GeneratedImage => {
  return {
    url: '', // Will be filled in when generation completes
    prompt,
    workflow,
    timestamp: Date.now(),
    batchId,
    batchIndex,
    status: 'generating' as ImageGenerationStatus,
    params,
    refiner,
    refinerParams,
    referenceImageUrl,
    containerId,
    title
  };
};

/**
 * Updates a placeholder image with the completed generation result
 */
export const updateImageWithResult = (
  placeholderImage: GeneratedImage,
  url: string
): GeneratedImage => {
  return {
    ...placeholderImage,
    url,
    status: 'completed' as ImageGenerationStatus,
  };
};

/**
 * Updates a placeholder image to show an error state
 */
export const updateImageWithError = (
  placeholderImage: GeneratedImage
): GeneratedImage => {
  return {
    ...placeholderImage,
    status: 'error' as ImageGenerationStatus,
  };
};
