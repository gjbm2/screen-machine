
import { useState } from 'react';
import { toast } from 'sonner';
import apiService from '@/utils/api';
import { GeneratedImage } from './use-image-state';

export const useImageGenerationApi = (
  addConsoleLog: (message: string) => Promise<void>,
  setGeneratedImages: React.Dispatch<React.SetStateAction<GeneratedImage[]>>,
  setImageUrl: React.Dispatch<React.SetStateAction<string | null>>,
  imageUrl: string | null
) => {
  const [activeGenerations, setActiveGenerations] = useState<string[]>([]);

  const generateImage = async (
    prompt: string,
    currentBatchId: string,
    currentContainerId: number,
    referenceImageUrl: string | undefined,
    requestData: any,
    uploadableFiles: File[] | undefined,
    batchSize: number
  ) => {
    try {
      const placeholderImages: GeneratedImage[] = [];
      
      for (let i = 0; i < batchSize; i++) {
        const placeholderImage: GeneratedImage = {
          url: '',
          prompt: prompt,
          workflow: requestData.workflow || 'text-to-image',
          timestamp: Date.now(),
          params: { ...requestData.params, ...requestData.global_params },
          refiner: requestData.refiner,
          refinerParams: requestData.refiner_params,
          batchId: currentBatchId,
          batchIndex: requestData.batch_id ? 
            (prev: GeneratedImage[]) => prev.filter(img => img.batchId === requestData.batch_id).length + i : 
            i,
          status: 'generating',
          referenceImageUrl,
          containerId: currentContainerId
        };
        
        placeholderImages.push(placeholderImage);
      }
      
      setGeneratedImages(prev => [...placeholderImages, ...prev]);
      setActiveGenerations(prev => [...prev, currentBatchId]);
      
      addConsoleLog(`Sending request to backend with batch size: ${batchSize}`);
      
      if (uploadableFiles && uploadableFiles.length > 0) {
        addConsoleLog(`Uploading ${uploadableFiles.length} reference image(s)`);
      }
      
      // Non-blocking approach with setTimeout
      setTimeout(() => {
        apiService.generateImage(requestData)
          .then(response => {
            if (response.success) {
              const newImages: GeneratedImage[] = [];
              
              for (const img of response.images) {
                const newGeneratedImage: GeneratedImage = {
                  url: img.url,
                  prompt: img.prompt,
                  workflow: img.workflow,
                  timestamp: img.timestamp,
                  params: img.params || {},
                  refiner: img.refiner,
                  refinerParams: img.refiner_params,
                  batchId: img.batch_id || currentBatchId,
                  batchIndex: img.batch_index || 0,
                  status: 'completed',
                  referenceImageUrl,
                  containerId: currentContainerId
                };
                
                newImages.push(newGeneratedImage);
              }
              
              setGeneratedImages(prev => {
                const filteredImages = prev.filter(img => !(img.batchId === currentBatchId && img.status === 'generating'));
                return [...newImages, ...filteredImages];
              });
              
              setActiveGenerations(prev => prev.filter(id => id !== currentBatchId));
              
              if (!imageUrl) {
                setImageUrl(newImages[0]?.url);
              }
              
              toast.success(`${newImages.length} image${newImages.length > 1 ? 's' : ''} generated successfully!`);
              addConsoleLog(`${newImages.length} image(s) generated successfully!`);
            } else {
              throw new Error(response.error || 'Unknown error');
            }
          })
          .catch(error => {
            handleGenerationError(error, currentBatchId);
          });
      }, 0);
      
      return true;
    } catch (error) {
      handleGenerationError(error, currentBatchId);
      return false;
    }
  };

  const handleGenerationError = (error: any, batchId: string) => {
    console.error('Error generating image:', error);
    addConsoleLog(`API error: ${error}`);
    
    toast.error('Failed to generate image. Please try again.');
    
    setGeneratedImages(prev => 
      prev.map(img => 
        (img.batchId === batchId && img.status === 'generating') 
          ? { ...img, status: 'failed' } 
          : img
      )
    );
    
    setActiveGenerations(prev => prev.filter(id => id !== batchId));
  };

  return {
    activeGenerations,
    setActiveGenerations,
    generateImage,
    handleGenerationError
  };
};

export default useImageGenerationApi;
