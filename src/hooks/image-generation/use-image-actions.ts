import { useState } from 'react';
import { GeneratedImage } from './types';
import { toast } from 'sonner';
import { saveAs } from 'file-saver';
import React from 'react';
import { useUploadedImages } from './use-uploaded-images';

interface UseImageActionsProps {
  setGeneratedImages: React.Dispatch<React.SetStateAction<GeneratedImage[]>>;
  imageContainerOrder: string[];
  setImageContainerOrder: React.Dispatch<React.SetStateAction<string[]>>;
  setCurrentPrompt: React.Dispatch<React.SetStateAction<string>>;
  setCurrentWorkflow: React.Dispatch<React.SetStateAction<string>>;
  uploadedImageUrls: string[];
  setUploadedImageUrls: React.Dispatch<React.SetStateAction<string[]>>;
  handleSubmitPrompt: (
    prompt: string, 
    imageInputs?: (File | string)[] | undefined,
    workflow?: string | undefined,
    workflowParams?: Record<string, any> | undefined,
    globalParams?: Record<string, any> | undefined,
    refiner?: string | undefined,
    refinerParams?: Record<string, any> | undefined,
    publishDestination?: string | undefined,
    batchId?: string | undefined
  ) => void;
  removeUrl: (url: string) => void;
}

export const useImageActions = ({
  setGeneratedImages,
  imageContainerOrder,
  setImageContainerOrder,
  setCurrentPrompt,
  setCurrentWorkflow,
  uploadedImageUrls,
  setUploadedImageUrls,
  handleSubmitPrompt,
  removeUrl
}: UseImageActionsProps) => {
  const [imageUrl, setImageUrl] = useState<string | null>(null);

  const handleUseGeneratedAsInput = async (url: string, append: boolean = false) => {
    console.log('handleUseGeneratedAsInput called with URL:', url, 'append:', append);
    
    // Find the image in our collection
    const image = generatedImages.find(img => img.url === url);
    
    // If found, use its prompt and workflow
    if (image) {
      console.log('Found image in generated images:', image);
      
      // Set the prompt and workflow to match the generated image
      if (image.prompt) {
        setCurrentPrompt(image.prompt);
        console.log('Setting prompt to:', image.prompt);
      }
      
      if (image.workflow) {
        setCurrentWorkflow(image.workflow);
        console.log('Setting workflow to:', image.workflow);
      }
    } else {
      console.log('Image not found in generated images collection, using URL directly');
    }
    
    // Always make the URL unique with a timestamp to enable duplicates
    const uniqueUrl = url.includes('?') 
      ? `${url}&_t=${Date.now()}` 
      : `${url}?_t=${Date.now()}`;
    
    // Update the uploaded image URLs based on append flag
    if (append) {
      setUploadedImageUrls(prev => [...prev, uniqueUrl]);
      console.log('Appending image URL as reference:', uniqueUrl);
    } else {
      // First remove any existing URLs to prevent conflicts
      uploadedImageUrls.forEach(existingUrl => {
        removeUrl(existingUrl);
      });
      
      setUploadedImageUrls([uniqueUrl]);
      console.log('Setting image URL as reference:', uniqueUrl);
    }
    
    // Set the image URL
    setImageUrl(url);

    toast.success(append ? 'Image added to reference images' : 'Image settings applied to prompt');
  };

  const handleCreateAgain = (image: GeneratedImage) => {
    if (image.prompt) {
      setCurrentPrompt(image.prompt);
    }
    
    if (image.workflow) {
      setCurrentWorkflow(image.workflow);
    }
    
    // Trigger generation with the same parameters
    handleSubmitPrompt(
      image.prompt || '',
      undefined,
      image.workflow,
      image.workflowParams,
      image.globalParams,
      image.refiner,
      image.refinerParams,
      image.publishDestination
    );
  };

  const handleDownloadImage = async (url: string, filename?: string) => {
    try {
      const response = await fetch(url);
      const blob = await response.blob();
      saveAs(blob, filename || 'generated-image.png');
      toast.success('Image downloaded');
    } catch (error) {
      toast.error('Failed to download image');
      console.error('Download error:', error);
    }
  };

  const handleDeleteImage = (imageId: string, containerId: string) => {
    setGeneratedImages(prevImages => {
      // Find the container that contains this image
      const container = prevImages.find(img => img.id === containerId);
      
      if (!container) {
        return prevImages;
      }
      
      if (container.id === imageId) {
        // If this is the main container image, remove the entire container
        return prevImages.filter(img => img.id !== containerId);
      } else if (container.variations) {
        // If it's a variation, remove just that variation
        const updatedContainer = {
          ...container,
          variations: container.variations.filter(v => v.id !== imageId)
        };
        
        return prevImages.map(img => 
          img.id === containerId ? updatedContainer : img
        );
      }
      
      return prevImages;
    });
    
    // Also remove from the container order if needed
    if (imageId === containerId) {
      setImageContainerOrder(prevOrder => 
        prevOrder.filter(id => id !== containerId)
      );
    }
    
    toast.success('Image deleted');
  };

  const handleDeleteContainer = (containerId: string) => {
    // Remove all images in this container
    setGeneratedImages(prevImages => 
      prevImages.filter(img => img.id !== containerId)
    );
    
    // Remove from the container order
    setImageContainerOrder(prevOrder => 
      prevOrder.filter(id => id !== containerId)
    );
    
    toast.success('Image group deleted');
  };

  const handleReorderContainers = (oldIndex: number, newIndex: number) => {
    setImageContainerOrder(prevOrder => {
      const result = Array.from(prevOrder);
      const [removed] = result.splice(oldIndex, 1);
      result.splice(newIndex, 0, removed);
      return result;
    });
  };

  // Keep this empty for now, it will be populated by useImageGeneration
  const generatedImages: any[] = [];

  return {
    imageUrl,
    handleUseGeneratedAsInput,
    handleCreateAgain,
    handleDownloadImage,
    handleDeleteImage,
    handleDeleteContainer,
    handleReorderContainers,
    generatedImages
  };
};
