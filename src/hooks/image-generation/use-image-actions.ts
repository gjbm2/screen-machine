
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
        // FIXED: Use an empty string prompt if no prompt exists
        const promptToUse = batchImage.prompt || '';
        
        // Make sure to pass the existing batchId to reuse the same container
        handleSubmitPrompt(promptToUse, referenceImages);
        
        toast.success('Generating new image with same settings');
        
        // Return the batch ID so caller can know which batch was regenerated
        return batchId;
      }
    }
    return null;
  };

  const handleDownloadImage = (url: string) => {
    // Get the filename from the URL
    const image = generatedImages.find(img => img.url === url);
    if (!image) {
      console.error('Image not found:', url);
      return;
    }
    
    // Create a filename based on the prompt or fallback to the timestamp
    const filenameBase = image.prompt 
      ? image.prompt.substring(0, 30).replace(/[^a-zA-Z0-9]/g, '_') 
      : `generated_${new Date().getTime()}`;
      
    // Save the file
    saveAs(url, `${filenameBase}.png`);
    toast.success('Image downloaded');
  };

  const handleDeleteImage = (batchId: string, imageIndex: number) => {
    setGeneratedImages(prev => {
      const newImages = [...prev];
      const imageToDelete = newImages.find(img => img.batchId === batchId && img.batchIndex === imageIndex);
      
      if (!imageToDelete) {
        console.error(`Image not found with batchId ${batchId} and index ${imageIndex}`);
        return prev;
      }
      
      return newImages.filter(img => !(img.batchId === batchId && img.batchIndex === imageIndex));
    });
    
    toast.success('Image deleted');
  };

  const handleReorderContainers = (sourceIndex: number, destinationIndex: number) => {
    const newImageContainerOrder = [...imageContainerOrder];
    const [removed] = newImageContainerOrder.splice(sourceIndex, 1);
    newImageContainerOrder.splice(destinationIndex, 0, removed);
    setImageContainerOrder(newImageContainerOrder);
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

export default useImageActions;
