
import { useState } from 'react';
import { GeneratedImage } from './types';
import { toast } from 'sonner';
import { saveAs } from 'file-saver';

export const useImageActions = (
  setGeneratedImages: React.Dispatch<React.SetStateAction<GeneratedImage[]>>,
  imageContainerOrder: string[],
  setImageContainerOrder: React.Dispatch<React.SetStateAction<string[]>>,
  setCurrentPrompt: React.Dispatch<React.SetStateAction<string>>,
  setCurrentWorkflow: React.Dispatch<React.SetStateAction<string>>,
  setUploadedImageUrls: React.Dispatch<React.SetStateAction<string[]>>,
  handleSubmitPrompt: (
    prompt: string, 
    imageFiles?: File[] | string[] | undefined,
    workflow?: string | undefined,
    params?: Record<string, any> | undefined,
    globalParams?: Record<string, any> | undefined,
    refiner?: string | undefined,
    refinerParams?: Record<string, any> | undefined
  ) => void,
  generatedImages: GeneratedImage[]
) => {
  const [imageUrl, setImageUrl] = useState<string | null>(null);

  const handleUseGeneratedAsInput = async (url: string) => {
    // Find the image in our collection
    const image = generatedImages.find(img => img.url === url);
    if (!image) {
      console.error('Image not found:', url);
      return;
    }

    console.log('Using image as input:', image);

    // Set the prompt and workflow to match the generated image
    if (image.prompt) {
      setCurrentPrompt(image.prompt);
      console.log('Setting prompt to:', image.prompt);
    }
    
    if (image.workflow) {
      setCurrentWorkflow(image.workflow);
      console.log('Setting workflow to:', image.workflow);
    }
    
    // FIXED: Always use the generated image as input
    // Regardless of whether it was created from a reference image
    setUploadedImageUrls([url]);
    console.log('Setting current image as reference:', url);
    
    // Set the image URL
    setImageUrl(url);

    toast.success('Image settings applied to prompt');
  };

  const handleCreateAgain = (batchId?: string) => {
    if (batchId) {
      // Find the first image in the batch
      const batchImage = generatedImages.find(img => img.batchId === batchId);
      if (batchImage) {
        console.log('Creating again from batch:', batchId);
        console.log('Batch image:', batchImage);
        console.log('Container ID:', batchImage.containerId);
        
        // Prepare reference images if any
        let referenceImages: string[] | undefined = undefined;
        if (batchImage.referenceImageUrl) {
          if (typeof batchImage.referenceImageUrl === 'string') {
            if (batchImage.referenceImageUrl.includes(',')) {
              referenceImages = batchImage.referenceImageUrl.split(',')
                .map(url => url.trim())
                .filter(url => url !== '');
            } else {
              referenceImages = [batchImage.referenceImageUrl];
            }
          } else if (Array.isArray(batchImage.referenceImageUrl)) {
            referenceImages = batchImage.referenceImageUrl;
          }
          console.log('Using reference images for regeneration:', referenceImages);
        }
        
        // Extract all parameters from the original image
        const promptToUse = batchImage.prompt || '';
        const workflowToUse = batchImage.workflow;
        
        // Get workflow params from the original image
        const paramsToUse = batchImage.params || {};
        
        // Extract publish destination if it exists
        const publishDestination = paramsToUse.publish_destination;
        console.log('Using publish destination:', publishDestination);
        
        // Extract refiner and refiner params
        const refinerToUse = batchImage.refiner;
        const refinerParamsToUse = batchImage.refinerParams || {};
        
        console.log('Regenerating with full parameters:', {
          prompt: promptToUse,
          workflow: workflowToUse,
          params: paramsToUse,
          refiner: refinerToUse,
          refinerParams: refinerParamsToUse
        });
        
        // Submit the prompt with ALL original parameters including the batchId
        handleSubmitPrompt(
          promptToUse, 
          referenceImages,
          workflowToUse,
          paramsToUse,
          undefined, // No global params needed as they're not stored per image
          refinerToUse,
          refinerParamsToUse
        );
      }
    }
  };

  const handleDownloadImage = (url: string, title?: string) => {
    const filename = title || `image-${Date.now()}.jpg`;
    saveAs(url, filename);
    toast.success(`Downloaded image: ${filename}`);
  };

  const handleDeleteImage = (batchId: string, index: number) => {
    setGeneratedImages(prevImages => {
      // Find the specific image to delete based on the batch ID and index
      const imageToDelete = prevImages.find(
        img => img.batchId === batchId && img.batchIndex === index
      );
      
      if (!imageToDelete) return prevImages;
      
      // Remove this specific image
      const updatedImages = prevImages.filter(
        img => !(img.batchId === batchId && img.batchIndex === index)
      );
      
      // If we deleted the last image in a batch, remove the batch ID from the order
      const batchHasRemainingImages = updatedImages.some(img => img.batchId === batchId);
      if (!batchHasRemainingImages) {
        setImageContainerOrder(prev => prev.filter(id => id !== batchId));
      }
      
      return updatedImages;
    });
  };

  const handleReorderContainers = (sourceIndex: number, destinationIndex: number) => {
    setImageContainerOrder(prev => {
      const newOrder = [...prev];
      const [removed] = newOrder.splice(sourceIndex, 1);
      newOrder.splice(destinationIndex, 0, removed);
      return newOrder;
    });
  };

  return {
    imageUrl,
    handleUseGeneratedAsInput,
    handleCreateAgain,
    handleDownloadImage,
    handleDeleteImage,
    handleReorderContainers,
  };
};
