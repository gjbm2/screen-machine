
import { GeneratedImage } from '../types';
import { ImageGenerationStatus } from '@/types/workflows';

/**
 * Processes the results of a generation and updates the images array
 */
export const processGenerationResults = (
  response: any,
  batchId: string,
  prevImages: GeneratedImage[]
): GeneratedImage[] => {
  // Add debug log to see what response we're getting
  console.log('Processing generation results:', { 
    responseSuccess: response?.success, 
    hasImages: !!response?.images,
    imageCount: response?.images?.length,
    batchId
  });
  
  if (!response || !response.success) {
    console.error('Received unsuccessful response:', response);
    return markBatchAsError(batchId, prevImages);
  }
  
  // Ensure we have images
  if (!response.images || response.images.length === 0) {
    console.warn('Response contained no images:', response);
    return markBatchAsError(batchId, prevImages);
  }
  
  // Extract the containerId from the placeholder images for this batch
  let containerId: number | undefined;
  const placeholder = prevImages.find(img => img.batchId === batchId && img.containerId);
  if (placeholder) {
    containerId = placeholder.containerId;
  }
  
  // Create a map of existing images by batch index to preserve any we may have
  const existingImagesByIndex = new Map<number, GeneratedImage>();
  prevImages.forEach(img => {
    if (img.batchId === batchId && typeof img.batchIndex === 'number') {
      existingImagesByIndex.set(img.batchIndex, img);
    }
  });
  
  // Process all images in the response
  const updatedImages = [...prevImages];
  
  // First mark existing placeholders as "to be updated"
  updatedImages.forEach((img, index) => {
    if (img.batchId === batchId && img.status === 'generating') {
      updatedImages[index] = {
        ...img,
        status: 'to_update' as ImageGenerationStatus
      };
    }
  });
  
  // Then update or add new images
  response.images.forEach((responseImage: any, index: number) => {
    console.log('Processing response image:', { index, responseImage });
    
    // Get the batch index from the response or use the array index
    const batchIndex = responseImage.batch_index ?? index;
    
    // Check if this image already exists
    const existingImageIndex = updatedImages.findIndex(
      img => img.batchId === batchId 
        && img.status === 'to_update'
        && img.batchIndex === batchIndex
    );
    
    // Check if we have a status field, if not assume 'completed'
    const imageStatus = responseImage.status || 'completed';
    console.log('Image status:', imageStatus);
    
    if (existingImageIndex !== -1) {
      // Update the existing placeholder
      const existingImage = updatedImages[existingImageIndex];
      const existingParams = existingImage.params || {};
      const existingRefiner = existingImage.refiner;
      const existingRefinerParams = existingImage.refinerParams || {};
      
      // Preserve or update parameters
      updatedImages[existingImageIndex] = {
        ...existingImage,
        url: responseImage.url,
        status: imageStatus as ImageGenerationStatus,
        prompt: responseImage.prompt || existingImage.prompt,
        workflow: responseImage.workflow || existingImage.workflow,
        timestamp: responseImage.timestamp || Date.now(),
        batchIndex: batchIndex,
        title: `${window.imageCounter + 1}. ${responseImage.prompt || existingImage.prompt} (${responseImage.workflow || existingImage.workflow})`,
        // Preserve params from placeholder or use response params
        params: responseImage.params || existingParams,
        // Preserve refiner from placeholder or use response refiner
        refiner: responseImage.refiner || existingRefiner,
        // Preserve refinerParams from placeholder or use response refiner_params
        refinerParams: responseImage.refiner_params || existingRefinerParams,
        containerId: containerId
      };
      
      // Log what we're preserving for debugging
      console.log('Preserving/updating params:', updatedImages[existingImageIndex].params);
      console.log('Preserving/updating refiner:', updatedImages[existingImageIndex].refiner);
      console.log('Preserving/updating refinerParams:', updatedImages[existingImageIndex].refinerParams);
      
      // Increment the counter
      if (window.imageCounter !== undefined) {
        window.imageCounter += 1;
      }
    } else {
      // Create a new image entry if we don't have a placeholder
      // This happens if we get more images back than we expected
      const newImage: GeneratedImage = {
        url: responseImage.url,
        prompt: responseImage.prompt,
        workflow: responseImage.workflow || 'unknown',
        batchId: batchId,
        batchIndex: batchIndex,
        status: imageStatus as ImageGenerationStatus,
        timestamp: responseImage.timestamp || Date.now(),
        title: `${window.imageCounter + 1}. ${responseImage.prompt} (${responseImage.workflow || 'unknown'})`,
        params: responseImage.params || {},
        refiner: responseImage.refiner,
        refinerParams: responseImage.refiner_params,
        containerId: containerId
      };
      
      updatedImages.push(newImage);
      
      // Increment the counter
      if (window.imageCounter !== undefined) {
        window.imageCounter += 1;
      }
    }
  });
  
  // Clean up any "to_update" images that didn't get updated
  return updatedImages.filter(img => img.status !== 'to_update');
};

/**
 * Marks all images in a batch as failed/error
 */
export const markBatchAsError = (
  batchId: string,
  prevImages: GeneratedImage[]
): GeneratedImage[] => {
  console.log(`Marking batch ${batchId} as error`);
  return prevImages.map(img => {
    if (img.batchId === batchId && img.status === 'generating') {
      return {
        ...img,
        status: 'error',
        timestamp: Date.now()
      };
    }
    return img;
  });
};
