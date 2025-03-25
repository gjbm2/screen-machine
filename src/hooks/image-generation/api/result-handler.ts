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
  
  // Get all placeholders for this batch
  const batchPlaceholders = prevImages.filter(img => 
    img.batchId === batchId && img.status === 'generating'
  );
  
  console.log('Found placeholders for batch:', batchPlaceholders.map(p => ({
    batchId: p.batchId,
    batchIndex: p.batchIndex,
    placeholderId: p.placeholderId,
    status: p.status
  })));
  
  // Clone the images array to update it
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
  
  // Track which placeholders have been matched
  const matchedPlaceholders = new Set<string>();
  
  // Then update or add new images
  response.images.forEach((responseImage: any, index: number) => {
    console.log('Processing response image:', { index, responseImage });
    
    // First check if the response includes a batch_index
    let batchIndex: number;
    let placeholderId: string | undefined;
    
    if (responseImage.batch_index !== undefined) {
      // Response includes batch_index, use it
      batchIndex = Number(responseImage.batch_index);
      
      // Try to find a placeholder with this batch index
      const matchingPlaceholder = batchPlaceholders.find(p => p.batchIndex === batchIndex);
      if (matchingPlaceholder) {
        placeholderId = matchingPlaceholder.placeholderId;
      }
    } else if (responseImage.placeholder_id) {
      // Response includes placeholderId, find the corresponding placeholder
      const matchingPlaceholder = batchPlaceholders.find(p => p.placeholderId === responseImage.placeholder_id);
      if (matchingPlaceholder) {
        placeholderId = matchingPlaceholder.placeholderId;
        batchIndex = matchingPlaceholder.batchIndex;
      } else {
        // If no placeholder with this ID exists, use index as fallback
        batchIndex = index;
      }
    } else {
      // No identifiers in response, try to match by position
      if (index < batchPlaceholders.length) {
        // Get the next unused placeholder
        const availablePlaceholders = batchPlaceholders.filter(p => 
          !matchedPlaceholders.has(p.placeholderId || '')
        );
        
        if (availablePlaceholders.length > 0) {
          // Use the first available placeholder
          placeholderId = availablePlaceholders[0].placeholderId;
          batchIndex = availablePlaceholders[0].batchIndex;
          
          // Mark this placeholder as matched
          if (placeholderId) {
            matchedPlaceholders.add(placeholderId);
          }
        } else {
          // No available placeholders, use index
          batchIndex = index;
        }
      } else {
        // More response images than placeholders, use index
        batchIndex = index;
      }
    }
    
    console.log(`Using batchIndex ${batchIndex} for image at array position ${index}`);
    
    // Check if we have a matching placeholder
    let existingImageIndex = -1;
    
    if (placeholderId) {
      // Find by placeholderId first (most reliable)
      existingImageIndex = updatedImages.findIndex(
        img => img.batchId === batchId && 
              img.status === 'to_update' && 
              img.placeholderId === placeholderId
      );
    }
    
    if (existingImageIndex === -1) {
      // If not found by placeholderId, try batch index
      existingImageIndex = updatedImages.findIndex(
        img => img.batchId === batchId && 
              img.status === 'to_update' && 
              img.batchIndex === batchIndex
      );
    }
    
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
        // Keep existing batch index to maintain position
        batchIndex: existingImage.batchIndex,
        // Keep existing placeholder ID
        placeholderId: existingImage.placeholderId,
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
      console.log(`Updated image at index ${existingImageIndex} with batchIndex ${existingImage.batchIndex}`);
      
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
      
      // Create a new image entry
      const newImage: GeneratedImage = {
        url: responseImage.url,
        prompt: responseImage.prompt,
        workflow: responseImage.workflow || 'unknown',
        batchId: batchId,
        batchIndex: batchIndex,
        placeholderId: responseImage.placeholder_id, // Store placeholder ID if available
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
