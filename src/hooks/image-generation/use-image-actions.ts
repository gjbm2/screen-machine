
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
    imageFiles?: File[] | string[] | undefined
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
        
        // Submit the prompt
        // Use an empty string prompt if no prompt exists
        const promptToUse = batchImage.prompt || '';
        
        // CRITICAL FIX: Pass the batchId to ensure we use the same container
        handleSubmitPrompt(promptToUse, referenceImages);
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
