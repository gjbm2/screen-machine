import { nanoid } from '@/lib/utils';
import { toast } from 'sonner';
import apiService from '@/utils/api';
import { GeneratedImage } from '../types';
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
  
  if (!batchId) {
    addConsoleLog({
      type: 'info',
      message: `Starting new batch with ID: ${currentBatchId}`
    });
    
    // Add this batch to the container order
    setImageContainerOrder(prev => [currentBatchId, ...prev]);
    
    // Increment container ID
    setNextContainerId(prevId => prevId + 1);
  }

  // Keep track of this generation
  setActiveGenerations(prev => [...prev, currentBatchId]);
  
  const { uploadedFiles, uploadedImageUrls } = processUploadedFiles(imageFiles);
  
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

  addConsoleLog({
    type: 'info',
    message: `Generating image with prompt: "${prompt}"`,
    details: {
      workflow,
      params,
      globalParams,
      hasReferenceImage: uploadedFiles.length > 0 || uploadedImageUrls.length > 0,
      referenceImageUrls: uploadedImageUrls.length > 0 ? uploadedImageUrls : undefined
    }
  });

  try {
    // Prepare reference image URL string
    const referenceImageUrl = uploadedImageUrls.length > 0 ? uploadedImageUrls.join(',') : undefined;
    
    // Pre-create placeholder records for the images
    // First, let's see how many exist already in this batch
    const existingBatchIndexes = new Set<number>();
    
    setGeneratedImages(prevImages => {
      prevImages.forEach(img => {
        if (img.batchId === currentBatchId && typeof img.batchIndex === 'number') {
          existingBatchIndexes.add(img.batchIndex);
        }
      });
      
      // Create a placeholder entry
      const nextIndex = existingBatchIndexes.size;
      
      const placeholderImage = createPlaceholderImage(
        prompt,
        workflow,
        currentBatchId,
        nextIndex,
        params,
        refiner,
        refinerParams,
        referenceImageUrl,
        nextContainerId && !batchId ? nextContainerId : undefined
      );
      
      return [...prevImages, placeholderImage];
    });

    // Use setTimeout to allow the UI to update before starting the API call
    setTimeout(async () => {
      try {
        // Make the API call
        const payload: GenerateImagePayload = {
          prompt,
          workflow,
          params,
          global_params: globalParams,
          refiner,
          refiner_params: refinerParams,
          imageFiles: uploadedFiles,
          batch_id: currentBatchId
        };
        
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
        
        // Update the images with the actual URLs
        setGeneratedImages(prevImages => {
          const newImages = [...prevImages];
          
          images.forEach((img: any, index: number) => {
            // Find the placeholder for this image
            const placeholderIndex = newImages.findIndex(
              pi => pi.batchId === currentBatchId && pi.batchIndex === index && pi.status === 'generating'
            );
            
            if (placeholderIndex >= 0) {
              // Update the placeholder with actual data
              newImages[placeholderIndex] = updateImageWithResult(
                newImages[placeholderIndex], 
                img.url
              );
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
                refinerParams
              };
              
              // If there's a reference image, make sure to include it
              if (referenceImageUrl) {
                newImage.referenceImageUrl = referenceImageUrl;
                console.log('Adding reference images to new image:', newImage.referenceImageUrl);
              }
              
              // Add containerId if this is a new batch
              if (nextContainerId && !batchId) {
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
              return updateImageWithError(img);
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
