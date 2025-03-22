
import { GeneratedImage } from '../types';

/**
 * Processes the results of a generation and updates the images array
 */
export const processGenerationResults = (
  response: any,
  batchId: string,
  prevImages: GeneratedImage[]
): GeneratedImage[] => {
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
        status: 'to_update'
      };
    }
  });
  
  // Then update or add new images
  response.images.forEach((responseImage: any, index: number) => {
    // Get the batch index from the response or use the array index
    const batchIndex = responseImage.batch_index ?? index;
    
    // Check if this image already exists
    const existingImageIndex = updatedImages.findIndex(
      img => img.batchId === batchId 
        && img.status === 'to_update'
        && img.batchIndex === batchIndex
    );
    
    if (existingImageIndex !== -1) {
      // Update the existing placeholder
      updatedImages[existingImageIndex] = {
        ...updatedImages[existingImageIndex],
        url: responseImage.url,
        status: 'completed',
        prompt: responseImage.prompt || updatedImages[existingImageIndex].prompt,
        workflow: responseImage.workflow || updatedImages[existingImageIndex].workflow,
        timestamp: responseImage.timestamp || Date.now(),
        batchIndex: batchIndex,
        title: `${window.imageCounter + 1}. ${responseImage.prompt || updatedImages[existingImageIndex].prompt} (${responseImage.workflow || updatedImages[existingImageIndex].workflow})`,
        params: responseImage.params || updatedImages[existingImageIndex].params,
        refiner: responseImage.refiner || updatedImages[existingImageIndex].refiner,
        refinerParams: responseImage.refiner_params || updatedImages[existingImageIndex].refinerParams,
        containerId: containerId
      };
      
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
        status: 'completed',
        timestamp: responseImage.timestamp || Date.now(),
        title: `${window.imageCounter + 1}. ${responseImage.prompt} (${responseImage.workflow || 'unknown'})`,
        params: responseImage.params,
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
