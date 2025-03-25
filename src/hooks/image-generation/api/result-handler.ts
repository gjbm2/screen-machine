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
  
  // CRITICAL FIX: Find all placeholders for this batch to ensure we have correct batch indexes
  const batchPlaceholders = prevImages.filter(img => 
    img.batchId === batchId && img.status === 'generating' && typeof img.batchIndex === 'number'
  );
  
  // Debug log the placeholders we found
  console.log('Found placeholders for batch:', batchPlaceholders.map(p => ({
    batchId: p.batchId,
    batchIndex: p.batchIndex,
    status: p.status
  })));
  
  // Create a map of existing images by batch index to preserve indexes
  const existingImagesByIndex = new Map<number, GeneratedImage>();
  
  // Store the placeholders by their index for easy lookup
  batchPlaceholders.forEach(img => {
    existingImagesByIndex.set(img.batchIndex, img);
  });
  
  // Debug log the existing images map
  console.log('Existing images map keys (batchIndexes):', Array.from(existingImagesByIndex.keys()));
  
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
  
  // CRITICAL FIX: Track which batchIndexes we've used from placeholders
  const usedPlaceholderIndexes = new Set<number>();
  
  // Then update or add new images
  response.images.forEach((responseImage: any, arrayIndex: number) => {
    console.log('Processing response image at array position:', arrayIndex, responseImage);
    
    // CRITICAL FIX: Determine a unique batchIndex for this image
    let batchIndex: number;
    
    // First check if the response includes a proper batch_index
    if (responseImage.batch_index !== undefined) {
      batchIndex = Number(responseImage.batch_index);
      console.log(`Using response-provided batch_index: ${batchIndex}`);
    } 
    // Otherwise, use a placeholder's batchIndex if available
    else if (arrayIndex < batchPlaceholders.length) {
      // Find a placeholder that hasn't been used yet
      let unusedPlaceholder = null;
      for (const placeholder of batchPlaceholders) {
        if (!usedPlaceholderIndexes.has(placeholder.batchIndex)) {
          unusedPlaceholder = placeholder;
          break;
        }
      }
      
      // If we found an unused placeholder, use its batchIndex
      if (unusedPlaceholder) {
        batchIndex = unusedPlaceholder.batchIndex;
        usedPlaceholderIndexes.add(batchIndex);
        console.log(`Using unused placeholder batchIndex: ${batchIndex} for image at array position ${arrayIndex}`);
      } 
      // If all placeholders are used, generate a new unique batchIndex
      else {
        // Find the highest batchIndex and add 1
        const highestIndex = Math.max(
          ...Array.from(usedPlaceholderIndexes),
          ...batchPlaceholders.map(p => p.batchIndex),
          -1
        );
        batchIndex = highestIndex + 1;
        console.log(`All placeholders used. Generated new batchIndex: ${batchIndex} for image at array position ${arrayIndex}`);
      }
    } 
    // Last resort - use array index if no placeholders are available
    else {
      batchIndex = arrayIndex;
      console.log(`No placeholders available. Using array index as batchIndex: ${batchIndex}`);
    }
    
    console.log(`FINAL: Using batchIndex ${batchIndex} for image at array position ${arrayIndex}`);
    
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
      
      // Process refiner value - ensure it's always a simple string
      let refinerValue: string | undefined;
      if (responseImage.refiner) {
        // If response refiner is a string, use it directly
        if (typeof responseImage.refiner === 'string') {
          refinerValue = responseImage.refiner;
        } 
        // If response refiner is an object with value property, extract the value
        else if (typeof responseImage.refiner === 'object' && responseImage.refiner !== null) {
          const refinerObj = responseImage.refiner as Record<string, any>;
          if ('value' in refinerObj) {
            refinerValue = String(refinerObj.value);
          }
        }
      } 
      // If no response refiner, but we have existing refiner
      else if (existingRefiner) {
        // If existing refiner is a string, use it
        if (typeof existingRefiner === 'string') {
          refinerValue = existingRefiner;
        } 
        // If existing refiner is an object with value property, extract the value
        else if (typeof existingRefiner === 'object' && existingRefiner !== null) {
          const refinerObj = existingRefiner as Record<string, any>;
          if ('value' in refinerObj) {
            refinerValue = String(refinerObj.value);
          }
        }
      }
      
      // Preserve or update parameters
      updatedImages[existingImageIndex] = {
        ...existingImage,
        url: responseImage.url,
        status: imageStatus as ImageGenerationStatus,
        prompt: responseImage.prompt || existingImage.prompt,
        workflow: responseImage.workflow || existingImage.workflow,
        timestamp: responseImage.timestamp || Date.now(),
        batchIndex: batchIndex, // Use our determined batchIndex
        title: `${window.imageCounter + 1}. ${responseImage.prompt || existingImage.prompt} (${responseImage.workflow || existingImage.workflow})`,
        // Preserve params from placeholder or use response params
        params: responseImage.params || existingParams,
        // Use processed refiner value
        refiner: refinerValue,
        // Preserve refinerParams from placeholder or use response refiner_params
        refinerParams: responseImage.refiner_params || existingRefinerParams,
        containerId: containerId
      };
      
      // Log what we're preserving for debugging
      console.log(`Updated image at index ${existingImageIndex} with batchIndex ${batchIndex}`);
      
      // Increment the counter
      if (window.imageCounter !== undefined) {
        window.imageCounter += 1;
      }
    } else {
      // Process refiner value for new images too
      let refinerValue: string | undefined;
      if (responseImage.refiner) {
        if (typeof responseImage.refiner === 'string') {
          refinerValue = responseImage.refiner;
        } else if (typeof responseImage.refiner === 'object' && responseImage.refiner !== null) {
          const refinerObj = responseImage.refiner as Record<string, any>;
          if ('value' in refinerObj) {
            refinerValue = String(refinerObj.value);
          }
        }
      }
      
      // Create a new image entry if we don't have a placeholder
      // This happens if we get more images back than we expected
      const newImage: GeneratedImage = {
        url: responseImage.url,
        prompt: responseImage.prompt,
        workflow: responseImage.workflow || 'unknown',
        batchId: batchId,
        batchIndex: batchIndex, // Use our determined batchIndex
        status: imageStatus as ImageGenerationStatus,
        timestamp: responseImage.timestamp || Date.now(),
        title: `${window.imageCounter + 1}. ${responseImage.prompt} (${responseImage.workflow || 'unknown'})`,
        params: responseImage.params || {},
        refiner: refinerValue,
        refinerParams: responseImage.refiner_params || {},
        containerId: containerId
      };
      
      console.log(`Adding new image with batchIndex ${batchIndex}`);
      updatedImages.push(newImage);
      
      // Increment the counter
      if (window.imageCounter !== undefined) {
        window.imageCounter += 1;
      }
    }
  });
  
  // Final validation: Ensure all images in the batch have unique batchIndexes
  const batchImages = updatedImages.filter(img => img.batchId === batchId && img.status !== 'to_update');
  const batchIndexMap = new Map<number, boolean>();
  let hasIndexConflict = false;
  
  // Check for batchIndex conflicts
  batchImages.forEach(img => {
    if (batchIndexMap.has(img.batchIndex)) {
      hasIndexConflict = true;
      console.error(`CRITICAL ERROR: Found duplicate batchIndex ${img.batchIndex} in batch ${batchId}`);
    } else {
      batchIndexMap.set(img.batchIndex, true);
    }
  });
  
  // Log batch images with their batchIndexes
  console.log('Final batch images batchIndexes:', batchImages.map(img => ({
    batchId: img.batchId,
    batchIndex: img.batchIndex,
    status: img.status
  })));
  
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
