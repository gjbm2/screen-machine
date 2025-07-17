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
  console.log('- imageFiles:', imageFiles?.length, imageFiles?.map(f => typeof f === 'string' ? `string(${f.substring(0, 50)}...)` : `file(${f.name})`));
  console.log('- referenceUrls:', referenceUrls?.length, referenceUrls?.map(url => url.substring(0, 50) + '...'));

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
        
        // Dispatch placeholder event so Recent tab can show container immediately
        // We'll dispatch a single event with all placeholders rather than one per placeholder
        if (placeholders.length > 0) {
          // Add a console log to track batch placeholder events
          console.log('[image-generator] Dispatching batch-placeholders event:', {
            batchId: currentBatchId,
            count: placeholders.length,
            placeholders: placeholders.map(p => p.placeholderId)
          });
          
          // We only need to dispatch once for the batch, not per placeholder
          window.dispatchEvent(
            new CustomEvent('recent:batch-placeholders', {
              detail: {
                batchId: currentBatchId,
                count: placeholders.length,
                prompt: prompt,
                placeholders: placeholders.map(p => ({
                  placeholderId: p.placeholderId,
                  batchIndex: p.batchIndex
                })),
                workflow,
                params,
                globalParams,
                collapsed: true // Indicate that this container should start collapsed
              }
            })
          );
          
          // Store placeholder IDs in localStorage to survive polling cycles
          try {
            const existingPlaceholders = JSON.parse(localStorage.getItem('activePlaceholders') || '[]');
            const updatedPlaceholders = [
              ...existingPlaceholders,
              ...placeholders.map(p => ({ 
                id: p.placeholderId, 
                batchId: currentBatchId, 
                timestamp: Date.now() 
              }))
            ];
            localStorage.setItem('activePlaceholders', JSON.stringify(updatedPlaceholders));
            console.log('[image-generator] Saved placeholders to localStorage:', updatedPlaceholders);
          } catch (err) {
            console.error('[image-generator] Failed to save placeholders to localStorage:', err);
          }
        }
        
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
          
          // Dispatch a browser event for generated images completion
          // This will signal the Recent tab to expand this container and collapse others
          if (response.images && Array.isArray(response.images) && response.images.length > 0) {
            console.log('[image-generator] Dispatching generation-complete event:', {
              batchId: currentBatchId,
              count: response.images.length
            });
            
            window.dispatchEvent(
              new CustomEvent('recent:generation-complete', {
                detail: {
                  batchId: currentBatchId,
                  count: response.images.length,
                  autoExpand: true // Signal to expand this container
                }
              })
            );
          }
        } else {
          // For async workflows, we'll handle the results through WebSocket messages
          addConsoleLog({
            type: 'info',
            message: `Async generation started for batch ${currentBatchId}. Results will be delivered via WebSocket.`
          });
        }
        
        // Remove from active generations
        setActiveGenerations(prev => prev.filter(id => id !== currentBatchId));
        
        // Dispatch a browser event for Recent tab can update immediately
        if (response.recent_files && Array.isArray(response.recent_files) && response.recent_files.length > 0) {
          window.dispatchEvent(
            new CustomEvent('recent:add', {
              detail: {
                batchId: response.batch_id,
                files: response.recent_files
              }
            })
          );
        }
        
        // Also remove active placeholders for this batch from localStorage since generation is complete
        try {
          const existingPlaceholders = JSON.parse(localStorage.getItem('activePlaceholders') || '[]');
          const updatedPlaceholders = existingPlaceholders.filter((p: any) => p.batchId !== currentBatchId);
          
          if (updatedPlaceholders.length !== existingPlaceholders.length) {
            localStorage.setItem('activePlaceholders', JSON.stringify(updatedPlaceholders));
            console.log(`[image-generator] Removed completed placeholders for batch ${currentBatchId} from localStorage`);
          }
        } catch (err) {
          console.error('[image-generator] Failed to remove completed placeholders from localStorage:', err);
        }
        
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
        
        // Show error toast with actual error message
        const errorMessage = error instanceof Error ? error.message : 'Failed to generate image';
        toast.error(errorMessage);
        
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
