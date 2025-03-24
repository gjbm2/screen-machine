
import { nanoid } from '@/lib/utils';
import { toast } from 'sonner';
import apiService from '@/utils/api';
import { GeneratedImage } from '../types';
import { ApiResponse, GenerateImagePayload } from './types';
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
  isVerboseDebug?: boolean;
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
    nextContainerId,
    isVerboseDebug = false
  } = config;

  const {
    addConsoleLog,
    setGeneratedImages,
    setImageContainerOrder,
    setNextContainerId,
    setActiveGenerations
  } = actions;

  // Verbose logging
  if (isVerboseDebug) {
    console.info("[VERBOSE] ⬇️ START IMAGE GENERATION PROCESS ⬇️");
    console.info("[VERBOSE] Received config:", {
      prompt,
      workflow,
      batchId: batchId || "new batch",
      globalParams,
      batch_size: globalParams?.batch_size
    });
  }

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
    
    if (isVerboseDebug) {
      console.info(`[VERBOSE] Reusing existing batch ID: ${currentBatchId}${existingContainerId ? `, containerId: ${existingContainerId}` : ''}`);
    } else {
      addConsoleLog({
        type: 'info',
        message: `Reusing existing batch with ID: ${currentBatchId}${existingContainerId ? `, containerId: ${existingContainerId}` : ''}`
      });
    }
  } else {
    if (isVerboseDebug) {
      console.info(`[VERBOSE] Starting new batch with ID: ${currentBatchId}`);
    } else {
      addConsoleLog({
        type: 'info',
        message: `Starting new batch with ID: ${currentBatchId}`
      });
    }
    
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
      if (isVerboseDebug) {
        console.info(`[VERBOSE] Using ${uploadedFiles.length + uploadedImageUrls.length} reference images:`, {
          fileCount: uploadedFiles.length,
          urlCount: uploadedImageUrls.length,
          imageUrls: uploadedImageUrls
        });
      } else {
        addConsoleLog({
          type: 'info',
          message: `Using ${uploadedFiles.length + uploadedImageUrls.length} reference images`,
          details: {
            fileCount: uploadedFiles.length,
            urlCount: uploadedImageUrls.length,
            imageUrls: uploadedImageUrls
          }
        });
        console.log(`[image-generator] Using reference images: ${uploadedImageUrls.join(', ')}`);
      }
    }

    // CRITICAL: Directly use the batch size from globalParams, don't override or use defaults
    const batchSize = globalParams?.batch_size || 1;
    
    // Log the batch size to verify we're using the right value
    if (isVerboseDebug) {
      console.info(`[VERBOSE] Using batch size: ${batchSize}`);
      console.info(`[VERBOSE] Original globalParams:`, globalParams);
      
      if (globalParams?.batch_size !== undefined) {
        console.info(`[VERBOSE] Verified globalParams.batch_size = ${globalParams.batch_size}`);
      } else {
        console.info(`[VERBOSE] WARNING: globalParams.batch_size is undefined, using default: 1`);
      }
    } else {
      console.log(`[image-generator] Received batch_size: ${batchSize}`);
      if (globalParams?.batch_size !== undefined) {
        console.log(`[image-generator] Original globalParams.batch_size: ${globalParams.batch_size}`);
      } else {
        console.log(`[image-generator] WARNING: globalParams.batch_size is undefined, using default: 1`);
      }
    }
    
    // Log full global params for debugging
    if (isVerboseDebug) {
      console.info(`[VERBOSE] Full globalParams for API:`, globalParams);
    } else {
      console.log(`[image-generator] Full received globalParams:`, globalParams);
    }
    
    if (isVerboseDebug) {
      console.info(`[VERBOSE] Generating ${batchSize} image(s) with prompt: "${prompt}"`);
      console.info(`[VERBOSE] Generation details:`, {
        workflow,
        params,
        globalParams,
        batchSize,
        hasReferenceImage: uploadedFiles.length > 0 || uploadedImageUrls.length > 0,
        referenceImageUrls: uploadedImageUrls.length > 0 ? uploadedImageUrls : undefined,
        refiner: refiner || undefined,
        refinerParams: refinerParams || undefined
      });
    } else {
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
          refinerParams: refinerParams || undefined
        }
      });
      
      console.log(`[image-generator] Creating placeholders for batch of ${batchSize} images with prompt: "${prompt}"`);
      if (refiner) {
        console.log(`[image-generator] Using refiner: ${refiner}`);
      }
    }

    // Prepare reference image URL string - make sure it's not empty
    const referenceImageUrl = uploadedImageUrls.length > 0 ? uploadedImageUrls.join(',') : undefined;
    
    // Additional debug log for reference images
    if (referenceImageUrl) {
      if (isVerboseDebug) {
        console.info("[VERBOSE] Reference images being used for generation:", referenceImageUrl);
      } else {
        console.log("[image-generator] Reference images being used for generation:", referenceImageUrl);
      }
    }
    
    if (isVerboseDebug) {
      console.info(`[VERBOSE] Creating ${batchSize} placeholder(s) for batch ${currentBatchId}`);
    } else {
      console.log(`[image-generator] Creating ${batchSize} placeholder(s) for batch ${currentBatchId}`);
    }
    
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
        // Make the API call - CRITICAL: Pass globalParams directly without modifying its batch_size
        const payload: GenerateImagePayload = {
          prompt,
          workflow,
          params,
          global_params: {
            ...globalParams,
          },
          refiner,
          refiner_params: refinerParams,
          imageFiles: uploadedFiles,
          batch_id: currentBatchId
        };
        
        // Enhanced logging to debug batch size issues
        if (isVerboseDebug) {
          console.info("[VERBOSE] API payload batch_size:", payload.global_params.batch_size);
          console.info("[VERBOSE] Full API payload:", payload);
        } else {
          console.log("[image-generator] API payload batch_size:", payload.global_params.batch_size);
          console.log("[image-generator] Full API payload:", payload);
        }
        
        const response = await apiService.generateImage(payload);
        
        if (isVerboseDebug) {
          console.info("[VERBOSE] 🎉 API response received:", response);
          console.info("[VERBOSE] Generated images count:", response.images?.length || 0);
        }
        
        addConsoleLog({
          type: 'success',
          message: `Generated ${response.images?.length || 0} images successfully`,
          details: { 
            batchId: currentBatchId,
            hasReferenceImages: uploadedImageUrls.length > 0,
            referenceImageUrls: uploadedImageUrls.length > 0 ? uploadedImageUrls : undefined,
            refiner: refiner || undefined
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
        
        if (isVerboseDebug) {
          console.info("[VERBOSE] ❌ Image generation failed:", error.message || "Unknown error");
        }
        
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
        if (isVerboseDebug) {
          console.info("[VERBOSE] ⬆️ END IMAGE GENERATION PROCESS ⬆️");
        }
      }
    }, 100); // Minimal delay to unblock the UI

    return currentBatchId;
  } catch (error: any) {
    console.error('Error setting up image generation:', error);
    
    if (isVerboseDebug) {
      console.info("[VERBOSE] ❌ Error setting up image generation:", error.message || "Unknown error");
    }
    
    addConsoleLog({
      type: 'error',
      message: `Failed to set up image generation: ${error.message || 'Unknown error'}`,
      details: error
    });
    
    // Remove from active generations
    setActiveGenerations(prev => prev.filter(id => id !== currentBatchId));
    
    toast.error(`Failed to generate image: ${error.message || 'Unknown error'}`);
    
    if (isVerboseDebug) {
      console.info("[VERBOSE] ⬆️ END IMAGE GENERATION PROCESS ⬆️");
    }
    
    return null;
  }
};
