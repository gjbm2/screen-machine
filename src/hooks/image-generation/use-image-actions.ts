
import { useState } from 'react';
import { toast } from 'sonner';
import { GeneratedImage } from './use-image-state';

export const useImageActions = (
  addConsoleLog: (message: string) => Promise<void>,
  setUploadedImageUrls: React.Dispatch<React.SetStateAction<string[]>>,
  setCurrentWorkflow: React.Dispatch<React.SetStateAction<string | null>>,
  setGeneratedImages: React.Dispatch<React.SetStateAction<GeneratedImage[]>>,
  imageContainerOrder: string[],
  generatedImages: GeneratedImage[],
  currentPrompt: string | null,
  currentParams: Record<string, any>,
  currentGlobalParams: Record<string, any>,
  currentRefiner: string | null,
  currentRefinerParams: Record<string, any>,
  uploadedImageUrls: string[]
) => {
  const handleCreateAgain = (batchId?: string) => {
    let prompt = currentPrompt;
    let originalParams = { ...currentParams };
    let originalWorkflow = currentWorkflow;
    let originalRefiner = currentRefiner;
    let originalRefinerParams = { ...currentRefinerParams };
    let originalUploadedImages = [...uploadedImageUrls];
    
    if (batchId) {
      const batchImage = generatedImages.find(img => img.batchId === batchId);
      if (batchImage) {
        prompt = batchImage.prompt || '';
        originalParams = batchImage.params || {};
        originalWorkflow = batchImage.workflow;
        originalRefiner = batchImage.refiner || null;
        originalRefinerParams = batchImage.refinerParams || {};
        
        if (batchImage.referenceImageUrl) {
          originalUploadedImages = [batchImage.referenceImageUrl];
        } else {
          originalUploadedImages = [];
        }
      }
    }
    
    if (!prompt) {
      toast.error('No prompt available to regenerate');
      return null;
    }
    
    const imageFiles = originalUploadedImages.length > 0 ? originalUploadedImages : undefined;
    
    const modifiedGlobalParams = { 
      ...currentGlobalParams, 
      batchSize: 1 
    };
    
    addConsoleLog(`Creating another image with same settings: "${prompt.substring(0, 50)}${prompt.length > 50 ? '...' : ''}"`);
    
    return {
      prompt,
      imageFiles,
      workflow: originalWorkflow || undefined,
      params: originalParams,
      globalParams: modifiedGlobalParams,
      refiner: originalRefiner || undefined,
      refinerParams: originalRefinerParams,
      batchId
    };
  };

  const handleUseGeneratedAsInput = async (selectedImageUrl: string) => {
    try {
      const response = await fetch(selectedImageUrl);
      const blob = await response.blob();
      
      const fileName = `input-image-${Date.now()}.png`;
      const file = new File([blob], fileName, { type: 'image/png' });
      
      const imageFiles = [file];
      
      const newUrl = URL.createObjectURL(blob);
      setUploadedImageUrls([newUrl]);
      
      setCurrentWorkflow('image-to-image');
      
      const uploadEvent = new CustomEvent('image-selected', { 
        detail: { files: imageFiles, urls: [newUrl] } 
      });
      document.dispatchEvent(uploadEvent);
      
      toast.success('Image set as input');
      addConsoleLog('Using generated image as input');
      
      return true;
    } catch (error) {
      console.error('Error using image as input:', error);
      addConsoleLog(`Error using image as input: ${error}`);
      toast.error('Failed to use image as input');
      return false;
    }
  };

  const handleDeleteImage = (batchId: string, imageIndex: number) => {
    setGeneratedImages(prev => {
      const batchImages = prev.filter(img => img.batchId === batchId);
      
      if (batchImages.length === 1) {
        setImageContainerOrder(order => order.filter(id => id !== batchId));
      }
      
      return prev.filter(img => !(img.batchId === batchId && img.batchIndex === imageIndex));
    });
  };

  return {
    handleCreateAgain,
    handleUseGeneratedAsInput,
    handleDeleteImage
  };
};

export default useImageActions;
