
import { useState, useEffect } from 'react';
import { toast } from 'sonner';
import apiService from '@/utils/api';
import { ImageGenerationStatus } from '@/types/workflows';

interface GeneratedImage {
  url: string;
  prompt: string;
  workflow: string;
  timestamp: number;
  params?: Record<string, any>;
  batchId?: string;
  batchIndex?: number;
  status?: ImageGenerationStatus;
  refiner?: string;
  refinerParams?: Record<string, any>;
  referenceImageUrl?: string;
  containerId?: number;
}

export const useImageGeneration = (
  addConsoleLog: (message: string) => Promise<void>
) => {
  const [activeGenerations, setActiveGenerations] = useState<string[]>([]);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [currentPrompt, setCurrentPrompt] = useState<string | null>(null);
  const [uploadedImageUrls, setUploadedImageUrls] = useState<string[]>([]);
  const [currentWorkflow, setCurrentWorkflow] = useState<string | null>(null);
  const [currentParams, setCurrentParams] = useState<Record<string, any>>({});
  const [currentGlobalParams, setCurrentGlobalParams] = useState<Record<string, any>>({});
  const [currentRefiner, setCurrentRefiner] = useState<string | null>(null);
  const [currentRefinerParams, setCurrentRefinerParams] = useState<Record<string, any>>({});
  const [generatedImages, setGeneratedImages] = useState<GeneratedImage[]>([]);
  const [imageContainerOrder, setImageContainerOrder] = useState<string[]>([]);
  const [nextContainerId, setNextContainerId] = useState<number>(1);
  const [isFirstRun, setIsFirstRun] = useState(true);

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
      return;
    }
    
    const imageFiles = originalUploadedImages.length > 0 ? originalUploadedImages : undefined;
    
    const modifiedGlobalParams = { 
      ...currentGlobalParams, 
      batchSize: 1 
    };
    
    addConsoleLog(`Creating another image with same settings: "${prompt.substring(0, 50)}${prompt.length > 50 ? '...' : ''}"`);
    
    handleSubmitPrompt(
      prompt, 
      imageFiles,
      originalWorkflow || undefined,
      originalParams,
      modifiedGlobalParams,
      originalRefiner || undefined,
      originalRefinerParams,
      batchId
    );
    
    toast.info('Creating another image...');
  };

  const handleSubmitPrompt = async (
    prompt: string, 
    imageFiles?: File[] | string[],
    workflow?: string,
    params?: Record<string, any>,
    globalParams?: Record<string, any>,
    refiner?: string,
    refinerParams?: Record<string, any>,
    batchId?: string
  ) => {
    if (isFirstRun) {
      setIsFirstRun(false);
    }
    
    setCurrentPrompt(prompt);
    setCurrentWorkflow(workflow || null);
    setCurrentParams(params || {});
    setCurrentGlobalParams(globalParams || {});
    setCurrentRefiner(refiner || null);
    setCurrentRefinerParams(refinerParams || {});
    
    try {
      addConsoleLog(`Starting image generation: "${prompt.substring(0, 50)}${prompt.length > 50 ? '...' : ''}"`);
      addConsoleLog(`Workflow: ${workflow || 'text-to-image'}, Refiner: ${refiner || 'none'}`);
      
      const currentBatchId = batchId || `batch-${Date.now()}-${Math.random().toString(36).substring(2, 7)}`;
      
      const currentContainerId = nextContainerId;
      if (!batchId) {
        setNextContainerId(prev => prev + 1);
      }
      
      if (!batchId || !imageContainerOrder.includes(batchId)) {
        setImageContainerOrder(prev => [currentBatchId, ...prev]);
      }
      
      let referenceImageUrl: string | undefined;
      let uploadableFiles: File[] | undefined;
      
      if (imageFiles && imageFiles.length > 0) {
        if (typeof imageFiles[0] === 'string') {
          referenceImageUrl = imageFiles[0] as string;
          try {
            const response = await fetch(imageFiles[0] as string);
            const blob = await response.blob();
            const file = new File([blob], `reference-${Date.now()}.png`, { type: blob.type });
            uploadableFiles = [file];
          } catch (error) {
            console.error('Error converting image URL to file:', error);
            addConsoleLog(`Error converting image URL to file: ${error}`);
          }
        } else {
          uploadableFiles = imageFiles as File[];
          referenceImageUrl = URL.createObjectURL(imageFiles[0] as File);
        }
      }
      
      const batchSize = globalParams?.batchSize || 1;
      
      const placeholderImages: GeneratedImage[] = [];
      
      for (let i = 0; i < batchSize; i++) {
        const placeholderImage: GeneratedImage = {
          url: '',
          prompt: prompt,
          workflow: workflow || 'text-to-image',
          timestamp: Date.now(),
          params: { ...params, ...globalParams },
          refiner: refiner,
          refinerParams: refinerParams,
          batchId: currentBatchId,
          batchIndex: batchId ? 
            generatedImages.filter(img => img.batchId === batchId).length + i : 
            i,
          status: 'generating',
          referenceImageUrl,
          containerId: batchId ? 
            generatedImages.find(img => img.batchId === batchId)?.containerId : 
            currentContainerId
        };
        
        placeholderImages.push(placeholderImage);
      }
      
      setGeneratedImages(prev => [...placeholderImages, ...prev]);
      
      setActiveGenerations(prev => [...prev, currentBatchId]);
      
      try {
        const requestData = {
          prompt,
          workflow: workflow || 'text-to-image',
          params: params || {},
          global_params: globalParams || {},
          refiner: refiner || 'none',
          refiner_params: refinerParams || {},
          imageFiles: uploadableFiles,
          batch_id: currentBatchId,
          batch_size: batchSize
        };
        
        addConsoleLog(`Sending request to backend with batch size: ${batchSize}`);
        
        if (uploadableFiles && uploadableFiles.length > 0) {
          addConsoleLog(`Uploading ${uploadableFiles.length} reference image(s)`);
        }
        
        try {
          const response = await apiService.generateImage(requestData);
          
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
        } catch (error) {
          addConsoleLog(`API error: ${error}`);
          
          toast.error('Failed to generate image. Please try again.');
          
          setGeneratedImages(prev => 
            prev.map(img => 
              (img.batchId === currentBatchId && img.status === 'generating') 
                ? { ...img, status: 'failed' } 
                : img
            )
          );
          
          setActiveGenerations(prev => prev.filter(id => id !== currentBatchId));
        }
      } catch (error) {
        console.error('Error calling API:', error);
        addConsoleLog(`Error calling API: ${error}`);
        
        setGeneratedImages(prev => 
          prev.map(img => 
            (img.batchId === currentBatchId && img.status === 'generating') 
              ? { ...img, status: 'failed' } 
              : img
          )
        );
        
        setActiveGenerations(prev => prev.filter(id => id !== currentBatchId));
        
        toast.error('Failed to generate image. Please try again.');
      }
    } catch (error) {
      console.error('Error generating image:', error);
      addConsoleLog(`Error generating image: ${error}`);
      toast.error('Failed to generate image. Please try again.');
      
      setGeneratedImages(prev => 
        prev.map(img => 
          (img.batchId === batchId && img.status === 'generating') 
            ? { ...img, status: 'failed' } 
            : img
        )
      );
      
      setActiveGenerations(prev => prev.filter(id => id !== batchId));
    }
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
    } catch (error) {
      console.error('Error using image as input:', error);
      addConsoleLog(`Error using image as input: ${error}`);
      toast.error('Failed to use image as input');
    }
  };

  const handleReorderContainers = (sourceIndex: number, destinationIndex: number) => {
    setImageContainerOrder(prev => {
      const newOrder = [...prev];
      const [removed] = newOrder.splice(sourceIndex, 1);
      newOrder.splice(destinationIndex, 0, removed);
      return newOrder;
    });
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

  const handleDeleteContainer = (batchId: string) => {
    setGeneratedImages(prev => prev.filter(img => img.batchId !== batchId));
    setImageContainerOrder(prev => prev.filter(id => id !== batchId));
  };

  return {
    activeGenerations,
    imageUrl,
    currentPrompt,
    uploadedImageUrls,
    currentWorkflow,
    currentParams,
    currentGlobalParams,
    currentRefiner,
    currentRefinerParams,
    generatedImages,
    imageContainerOrder,
    isFirstRun,
    setCurrentGlobalParams,
    handleSubmitPrompt,
    handleUseGeneratedAsInput,
    handleCreateAgain,
    handleReorderContainers,
    handleDeleteImage,
    handleDeleteContainer
  };
};

export default useImageGeneration;
