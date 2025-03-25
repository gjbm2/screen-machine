
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
  imageFiles?: File[] | string[];
  workflow?: string;
  params?: Record<string, any>;
  globalParams?: Record<string, any>;
  refiner?: string;
  refinerParams?: Record<string, any>;
  batchId?: string;
  nextContainerId?: number;
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
    workflow = 'flux1',
    params = {},
    globalParams = {},
    refiner,
    refinerParams,
    batchId,
    nextContainerId
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
    
    // Only add to the container order if this is a new batch
    setImageContainerOrder(prev => [currentBatchId, ...prev]);
    
    // Only increment container ID for new batches
    setNextContainerId(prevId => prevId + 1);
  }

  // Keep track of this generation
  setActiveGenerations(prev => [...prev, currentBatchId]);
  
  try {
    const { uploadedFiles, uploadedImageUrls } = processUploadedFiles(imageFiles);
    
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
        containerId: existingContainerId
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
    
    // Create placeholders for the batch
    setGeneratedImages(prevImages => {
      const newPlaceholders = createPlaceholderBatch(
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
      
      return [...prevImages, ...newPlaceholders];
    });

    // Use setTimeout to allow the UI to update before starting the API call
    setTimeout(async () => {
      try {
        // Make the API call with all parameters
        const payload = {
          prompt,
          workflow,
          params,
          global_params: globalParams,
          refiner,
          refiner_params: refinerParams,
          imageFiles: uploadedFiles,
          batch_id: currentBatchId
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
            publishDestination: params.publish_destination || undefined
          }
        });
        
        // Update the images with the actual URLs
        setGeneratedImages(prevImages => {
          const updatedImages = processGenerationResults(response, currentBatchId, prevImages);
          return updatedImages;
        });

        // Success message
        if (response.images?.length > 0) {
          toast.success(`Generated ${response.images.length} image${response.images.length > 1 ? 's' : ''} successfully`);
        }
      } catch (error: any) {
        console.error('Image generation error:', error);
        
        addConsoleLog({
          type: 'error',
          message: `Failed to generate image: ${error.message || 'Unknown error'}`,
          details: error
        });
        
        // Update image placeholders to show error
        setGeneratedImages(prevImages => {
          return markBatchAsError(currentBatchId, prevImages);
        });
        
        toast.error(`Failed to generate image: ${error.message || 'Unknown error'}`);
      } finally {
        // Remove from active generations
        setActiveGenerations(prev => prev.filter(id => id !== currentBatchId));
      }
    }, 100); // Minimal delay to unblock the UI

    return currentBatchId;
  } catch (error: any) {
    console.error('Error setting up image generation:', error);
    
    addConsoleLog({
      type: 'error',
      message: `Failed to set up image generation: ${error.message || 'Unknown error'}`,
      details: error
    });
    
    // Remove from active generations
    setActiveGenerations(prev => prev.filter(id => id !== currentBatchId));
    
    toast.error(`Failed to generate image: ${error.message || 'Unknown error'}`);
    return null;
  }
};
