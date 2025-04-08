import { useState } from 'react';
import { ReferenceImageData } from '@/types/workflows';
import { processUploadedFiles } from './api/reference-image-utils';
import { findImageCapableWorkflow } from '@/utils/workflow-utils';

export const useImageActions = (
  setGeneratedImages: React.Dispatch<React.SetStateAction<any[]>>,
  imageContainerOrder: string[],
  setImageContainerOrder: React.Dispatch<React.SetStateAction<string[]>>,
  setCurrentPrompt: React.Dispatch<React.SetStateAction<string>>,
  setCurrentWorkflow: React.Dispatch<React.SetStateAction<string>>,
  setUploadedImageUrls: React.Dispatch<React.SetStateAction<string[]>>,
  handleSubmitPrompt: any,
  generatedImages: any[]
) => {
  const [imageUrl, setImageUrl] = useState<string | null>(null);

  const handleUseGeneratedAsInput = (imageUrl: string, prompt: string) => {
    // When using a generated image as input, use our common findImageCapableWorkflow function
    // rather than hardcoding to 'image-to-image'
    const currentWorkflow = 'sd35.json'; // Default workflow
    const nextWorkflow = findImageCapableWorkflow(currentWorkflow, true);
    
    setCurrentPrompt(prompt);
    setCurrentWorkflow(nextWorkflow);
    setUploadedImageUrls([imageUrl]);

    const event = new CustomEvent('image-selected', {
      detail: {
        files: [],
        urls: [imageUrl]
      }
    });
    document.dispatchEvent(event);
  };

  const handleCreateAgain = async (batchId: string, prompt: string, workflow?: string, params?: Record<string, any>) => {
    try {
      await handleSubmitPrompt(prompt, undefined, workflow, params, undefined, undefined, undefined, undefined, batchId);
    } catch (error) {
      console.error('Error re-generating image:', error);
    }
  };

  const handleDownloadImage = (imageUrl: string) => {
    const link = document.createElement('a');
    link.href = imageUrl;
    link.download = 'generated_image.png';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  const handleDeleteImage = (batchId: string, imageUrl: string) => {
    setGeneratedImages(prevImages => {
      return prevImages.map(container => {
        if (container.batchId === batchId) {
          const updatedImages = container.images.filter(img => img.url !== imageUrl);
          return { ...container, images: updatedImages };
        }
        return container;
      }).filter(container => container.images.length > 0);
    });
  };

  const handleReorderContainers = (newOrder: string[]) => {
    setImageContainerOrder(newOrder);
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
