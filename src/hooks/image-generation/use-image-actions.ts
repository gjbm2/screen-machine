
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

  const handleUseGeneratedAsInput = (url: string) => {
    // Find the image in our collection
    const image = generatedImages.find(img => img.url === url);
    if (!image) {
      console.error('Image not found:', url);
      return;
    }

    // Set the reference image
    setUploadedImageUrls([url]);
    
    // Set the prompt and workflow to match the generated image
    if (image.prompt) {
      setCurrentPrompt(image.prompt);
    }
    
    if (image.workflow) {
      setCurrentWorkflow(image.workflow);
    }
    
    // Set the image URL
    setImageUrl(url);

    toast.success('Image set as input reference');
  };

  const handleCreateAgain = (batchId?: string) => {
    if (batchId) {
      // Find the first image in the batch
      const batchImage = generatedImages.find(img => img.batchId === batchId);
      if (batchImage) {
        console.log('Creating again from batch:', batchId);
        
        // Set the prompt to the one used to generate this image
        if (batchImage.prompt) {
          setCurrentPrompt(batchImage.prompt);
        }
        
        // Set the workflow to the one used to generate this image
        if (batchImage.workflow) {
          setCurrentWorkflow(batchImage.workflow);
        }

        // If it has a reference image, use that
        if (batchImage.referenceImageUrl) {
          setUploadedImageUrls([batchImage.referenceImageUrl]);
        } else {
          setUploadedImageUrls([]);
        }
        
        // Submit the prompt
        if (batchImage.prompt) {
          const referenceImages = batchImage.referenceImageUrl ? [batchImage.referenceImageUrl] : undefined;
          handleSubmitPrompt(batchImage.prompt, referenceImages);
        }
      }
    }
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
