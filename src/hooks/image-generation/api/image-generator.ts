import { nanoid } from '@/lib/utils';
import { toast } from 'sonner';
import apiService from '@/utils/api';
import { GeneratedImage } from '../types';
import { processUploadedFiles } from './reference-image-utils';
import { createPlaceholderBatch } from './placeholder-utils';
import { processGenerationResults, markBatchAsError } from './result-handler';
import { findExistingContainerId, getContainerIdForBatch } from './batch-utils';
import { generateImageTitle } from './title-util';

export interface ImageGenerationParams {
  prompt: string;
  imageFiles?: (File | string)[];
  referenceUrls?: string[];
  workflow?: string;
  params?: Record<string, any>;
  globalParams?: Record<string, any>;
  refiner?: string;
  refinerParams?: Record<string, any>;
  batchId?: string;
  nextContainerId?: number;
  isAsync?: boolean;
}

export interface ImageGenerationActions {
  addConsoleLog: (log: any) => void;
  setGeneratedImages: React.Dispatch<React.SetStateAction<GeneratedImage[]>>;
  setImageContainerOrder: React.Dispatch<React.SetStateAction<string[]>>;
  setNextContainerId: React.Dispatch<React.SetStateAction<number>>;
  setActiveGenerations: React.Dispatch<React.SetStateAction<string[]>>;
}

/**
 * Handles the entire image generation process
 */
export const generateImage = async (
  config: ImageGenerationParams,
  actions: ImageGenerationActions
): Promise<string | null> => {
  const {
    prompt,
    imageFiles,
    referenceUrls,
    workflow = 'flux1',
    params = {},
    globalParams = {},
    refiner,
    refinerParams,
    batchId,
    nextContainerId,
    isAsync
  } = config;

  const {
    addConsoleLog,
    setGeneratedImages,
    setImageContainerOrder,
    setNextContainerId,
    setActiveGenerations
  } = actions;

  // Log all incoming parameters for debugging
  console.log('[image-generator] Generating with parameters:');
  console.log('- prompt:', prompt);
  console.log('- workflow:', workflow);
  console.log('- params:', params);
  console.log('- globalParams:', globalParams);
  console.log('- refiner:', refiner);
  console.log('- refinerParams:', refinerParams);
  console.log('- publish destination:', params.publish_destination);
  console.log('- batchId:', batchId);
  console.log('- isAsync:', isAsync);

  // Create or use the provided batch ID
  const currentBatchId = batchId || nanoid();
  
  // Find if there's an existing containerId for this batch
  let existingContainerId: number | undefined;
  
  if (batchId) {
    // If we're reusing a batch ID, find out if it already has a containerId
    setGeneratedImages(prevImages => {
      existingContainerId = findExistingContainerId(batchId, prevImages);
      return prevImages;
    });
    
    addConsoleLog({
      type: 'info',
      message: `Reusing existing batch with ID: ${currentBatchId}${existingContainerId ? `, containerId: ${existingContainerId}` : ''}`
    });
  } else {
    addConsoleLog({
      type: 'info',
      message: `Starting new batch with ID: ${currentBatchId}`
    });
    
    // Only add to the container order if this is a new batch and NOT async
    if (!isAsync) {
      setImageContainerOrder(prev => [currentBatchId, ...prev]);
      
      // Only increment container ID for new batches
      setNextContainerId(prevId => prevId + 1);
    } else {
      addConsoleLog({
        type: 'info',
        message: `Async workflow detected: Not adding to container order for batch ${currentBatchId}`
      });
    }
  }

  // Keep track of this generation
  setActiveGenerations(prev => [...prev, currentBatchId]);
  
  // IMPORTANT: For async workflows, show toast immediately rather than waiting for API response
  if (isAsync) {
    toast.success("Async generation started, you'll be notified when it completes");
  }
  
  try {
    const mergedImageInputs = [
      ...(config.imageFiles || []),
      ...(config.referenceUrls || [])
    ];

    const { uploadedFiles, uploadedImageUrls } = processUploadedFiles(mergedImageInputs);
    
    // Generate title for this image generation
    const imageTitle = generateImageTitle(prompt, workflow);
    
    // Log information about reference images for debugging
    if (uploadedFiles.length > 0 || uploadedImageUrls.length > 0) {
      addConsoleLog({
        type: 'info',
        message: `Using ${uploadedFiles.length + uploadedImageUrls.length} reference images`,
        details: {
          fileCount: uploadedFiles.length,
          urlCount: uploadedImageUrls.length,
          imageUrls: uploadedImageUrls
        }
      });
    }

    // Get batch size from globalParams, with fallback to 1
    const batchSize = globalParams.batch_size || 1;
    
    addConsoleLog({
      type: 'info',
      message: `Generating ${batchSize} image(s) with prompt: "${prompt}"`,
      details: {
        workflow,
        params,
        globalParams,
        batchSize,
        hasReferenceImage: uploadedFiles.length > 0 || uploadedImageUrls.length > 0,
        referenceImageUrls: uploadedImageUrls.length > 0 ? uploadedImageUrls : undefined,
        refiner: refiner || undefined,
        refinerParams: refinerParams || undefined,
        publishDestination: params.publish_destination || undefined,
        batchId: currentBatchId,
        containerId: existingContainerId,
        isAsync
      }
    });
    
    // Log publish destination if present
    if (params.publish_destination) {
      console.log(`[image-generator] Using publish destination: ${params.publish_destination}`);
    }

    // Prepare reference image URL string - make sure it's not empty
    const referenceImageUrl = uploadedImageUrls.length > 0 ? uploadedImageUrls.join(',') : undefined;
    
    // Determine which container ID to use
    const containerIdToUse = getContainerIdForBatch(batchId, existingContainerId, nextContainerId);
    
    // Skip creating placeholders for async workflows
    let placeholders: GeneratedImage[] = [];
    
    if (!isAsync) {
      setGeneratedImages(prevImages => {
        // Create placeholders with unique IDs
        placeholders = createPlaceholderBatch(
          prompt,
          workflow,
          currentBatchId,
          batchSize,
          prevImages,
          params,
          refiner,
          refinerParams,
          referenceImageUrl,
          containerIdToUse
        );
        
        return [...prevImages, ...placeholders];
      });
    } else {
      addConsoleLog({
        type: 'info',
        message: `Async workflow: Skipping placeholder creation for batch ${currentBatchId}`
      });
    }

    // Use setTimeout to allow the UI to update before starting the API call
    setTimeout(async () => {
      try {
        // Prepare placeholder IDs to send to API
        const placeholderIds = placeholders.map(p => ({
          batch_index: p.batchIndex,
          placeholder_id: p.placeholderId
        }));
        
        // Make the API call with all parameters
        const payload = {
          prompt,
          workflow,
          params,
          global_params: globalParams,
          refiner,
          refiner_params: refinerParams,
          imageFiles: uploadedFiles,
          referenceUrls: uploadedImageUrls,
          batch_id: currentBatchId,
          placeholders: placeholderIds // Send placeholder IDs to API
        };
        
        // Enhanced logging to debug
        console.log("[image-generator] Sending API payload:", payload);

        const response = await apiService.generateImage(payload);
        
        addConsoleLog({
          type: 'success',
          message: `Generated ${response.images?.length || 0} images successfully`,
          details: { 
            batchId: currentBatchId,
            hasReferenceImages: uploadedImageUrls.length > 0,
            referenceImageUrls: uploadedImageUrls.length > 0 ? uploadedImageUrls : undefined,
            refiner: refiner || undefined,
            refinerParams: refinerParams || undefined,
            publishDestination: params.publish_destination || undefined,
            isAsync
          }
        });
        
        // For non-async workflows, update the images with the actual URLs
        if (!isAsync) {
          setGeneratedImages(prevImages => {
            const updatedImages = processGenerationResults(response, currentBatchId, prevImages);
            return updatedImages;
          });
        } else {
          // For async workflows, we'll handle the results through WebSocket messages
          addConsoleLog({
            type: 'info',
            message: `Async generation started for batch ${currentBatchId}. Results will be delivered via WebSocket.`
          });
        }
        
        // Remove from active generations
        setActiveGenerations(prev => prev.filter(id => id !== currentBatchId));
        
        return currentBatchId;
      } catch (error) {
        console.error('[image-generator] Error generating image:', error);
        
        // Log the error
        addConsoleLog({
          type: 'error',
          message: 'Error generating image',
          details: error
        });
        
        // Mark the batch as error if it exists
        if (!isAsync) {
          setGeneratedImages(prevImages => {
            return markBatchAsError(currentBatchId, prevImages);
          });
        }
        
        // Remove from active generations
        setActiveGenerations(prev => prev.filter(id => id !== currentBatchId));
        
        // Show error toast
        toast.error('Failed to generate image. Check console for details.');
        
        return null;
      }
    }, 0);

    return currentBatchId;
  } catch (error) {
    console.error('[image-generator] Error in generateImage:', error);
    
    // Log the error
    addConsoleLog({
      type: 'error',
      message: 'Error in generateImage',
      details: error
    });
    
    // Remove from active generations
    setActiveGenerations(prev => prev.filter(id => id !== currentBatchId));
    
    return null;
  }
};
