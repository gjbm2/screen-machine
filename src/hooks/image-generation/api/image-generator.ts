
import { nanoid } from '@/lib/utils';
import { toast } from 'sonner';
import apiService from '@/utils/api';
import { GeneratedImage } from '../types';
import { ImageGenerationStatus } from '@/types/workflows';
import { ApiResponse, GenerateImagePayload } from './types';
import { 
  createPlaceholderImage, 
  updateImageWithResult, 
  updateImageWithError,
  processUploadedFiles
} from './image-utils';

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

// Get or initialize counter for image titles
const getNextImageNumber = (): number => {
  // Initialize counter if it doesn't exist yet
  if (typeof window.imageCounter === 'undefined') {
    window.imageCounter = 0;
  }
  
  // Increment counter and return new value
  window.imageCounter += 1;
  return window.imageCounter;
};

// Generate a formatted title with the current counter, prompt, and workflow
const generateImageTitle = (prompt: string, workflow: string): string => {
  const imageNumber = getNextImageNumber();
  return `${imageNumber}. ${prompt} (${workflow})`;
};

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

  // Create or use the provided batch ID
  const currentBatchId = batchId || nanoid();
  
  // Find if there's an existing containerId for this batch
  let existingContainerId: number | undefined;
  
  if (batchId) {
    // If we're reusing a batch ID, find out if it already has a containerId
    setGeneratedImages(prevImages => {
      // Look for any existing image with this batch ID that has a containerId
      const existingImage = prevImages.find(img => img.batchId === batchId && img.containerId);
      if (existingImage && existingImage.containerId) {
        existingContainerId = existingImage.containerId;
      }
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
    console.log(`[image-generator] Using reference images: ${uploadedImageUrls.join(', ')}`);
  }

  addConsoleLog({
    type: 'info',
    message: `Generating image with prompt: "${prompt}"`,
    details: {
      workflow,
      params,
      globalParams,
      hasReferenceImage: uploadedFiles.length > 0 || uploadedImageUrls.length > 0,
      referenceImageUrls: uploadedImageUrls.length > 0 ? uploadedImageUrls : undefined,
      batchSize: globalParams?.batch_size // Log the batch size
    }
  });

  try {
    // Prepare reference image URL string - make sure it's not empty
    const referenceImageUrl = uploadedImageUrls.length > 0 ? uploadedImageUrls.join(',') : undefined;
    
    // Add additional debug log for reference images
    if (referenceImageUrl) {
      console.log("[image-generator] Reference images being used for generation:", referenceImageUrl);
    }
    
    // Pre-create placeholder records for the images
    // First, let's see how many exist already in this batch
    const existingBatchIndexes = new Set<number>();
    const batchSize = globalParams?.batch_size || 1;
    
    // Log batch size to debug
    console.log(`[image-generator] Creating ${batchSize} placeholder(s) for batch ${currentBatchId}`);
    
    setGeneratedImages(prevImages => {
      prevImages.forEach(img => {
        if (img.batchId === currentBatchId && typeof img.batchIndex === 'number') {
          existingBatchIndexes.add(img.batchIndex);
        }
      });
      
      // Create a placeholder entry for each image in the batch
      const newPlaceholders: GeneratedImage[] = [];
      
      for (let i = 0; i < batchSize; i++) {
        const nextIndex = existingBatchIndexes.size + i;
        
        const placeholderImage = createPlaceholderImage(
          prompt,
          workflow,
          currentBatchId,
          nextIndex,
          params,
          refiner,
          refinerParams,
          referenceImageUrl,
          // Use existing containerId if available, otherwise use the provided one
          existingContainerId || (nextContainerId && !batchId ? nextContainerId : undefined)
        );
        
        // Add the title to the placeholder image
        placeholderImage.title = imageTitle;
        
        // Additional debug for placeholder
        if (placeholderImage.referenceImageUrl) {
          console.log("[image-generator] Placeholder created with reference images:", placeholderImage.referenceImageUrl);
        }
        
        newPlaceholders.push(placeholderImage);
      }
      
      return [...prevImages, ...newPlaceholders];
    });

    // Use setTimeout to allow the UI to update before starting the API call
    setTimeout(async () => {
      try {
        // Make the API call
        const payload: GenerateImagePayload = {
          prompt,
          workflow,
          params,
          global_params: {
            ...globalParams,
            batch_size: globalParams?.batch_size || 1, // Ensure batch_size is passed
          },
          refiner,
          refiner_params: refinerParams,
          imageFiles: uploadedFiles,
          batch_id: currentBatchId
        };
        
        // Log the payload to debug batch size issues
        console.log("[image-generator] API payload:", {
          ...payload,
          global_params: payload.global_params
        });
        
        const response = await apiService.generateImage(payload);
        
        if (!response || !response.images) {
          throw new Error('No images were returned');
        }
        
        const images = response.images;
        
        addConsoleLog({
          type: 'success',
          message: `Generated ${images.length} images successfully`,
          details: { 
            batchId: currentBatchId,
            hasReferenceImages: uploadedImageUrls.length > 0,
            referenceImageUrls: uploadedImageUrls.length > 0 ? uploadedImageUrls : undefined
          }
        });
        
        console.log("[image-generator] Generation successful, updating images with reference URLs:", referenceImageUrl);
        
        // Update the images with the actual URLs
        setGeneratedImages(prevImages => {
          const newImages = [...prevImages];
          
          images.forEach((img: any, index: number) => {
            // Find the placeholder for this image
            const placeholderIndex = newImages.findIndex(
              pi => pi.batchId === currentBatchId && pi.batchIndex === index && pi.status === 'generating'
            );
            
            if (placeholderIndex >= 0) {
              // Update the placeholder with actual data - preserve reference image URL and title
              const updatedImage = updateImageWithResult(
                newImages[placeholderIndex], 
                img.url
              );
              
              // Make sure the title is preserved
              updatedImage.title = newImages[placeholderIndex].title || imageTitle;
              
              newImages[placeholderIndex] = updatedImage;
              
              // Log the updated image for debugging
              if (newImages[placeholderIndex].referenceImageUrl) {
                console.log("[image-generator] Updated image with reference image URL:", newImages[placeholderIndex].referenceImageUrl);
              }
            } else {
              // No placeholder found, this is an additional image
              const newImage: GeneratedImage = {
                url: img.url,
                prompt,
                workflow,
                timestamp: Date.now(),
                batchId: currentBatchId,
                batchIndex: index,
                status: 'completed' as ImageGenerationStatus,
                params,
                refiner,
                refinerParams,
                title: imageTitle // Add title to new image
              };
              
              // If there's a reference image, make sure to include it
              if (referenceImageUrl) {
                newImage.referenceImageUrl = referenceImageUrl;
                console.log('[image-generator] Adding reference images to new image:', referenceImageUrl);
              }
              
              // Add containerId - use existing if available, otherwise use the provided one
              if (existingContainerId) {
                newImage.containerId = existingContainerId;
              } else if (nextContainerId && !batchId) {
                newImage.containerId = nextContainerId;
              }
              
              newImages.push(newImage);
            }
          });
          
          return newImages;
        });

        // Success message
        if (images.length > 0) {
          toast.success(`Generated ${images.length} image${images.length > 1 ? 's' : ''} successfully`);
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
          return prevImages.map(img => {
            if (img.batchId === currentBatchId && img.status === 'generating') {
              const errorImage = updateImageWithError(img);
              // Preserve the title even when we have an error
              errorImage.title = img.title;
              return errorImage;
            }
            return img;
          });
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
