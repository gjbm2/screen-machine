import React, { useState } from 'react';
import { toast } from 'sonner';
import Header from '@/components/Header';
import PromptForm from '@/components/PromptForm';
import ImageDisplay from '@/components/ImageDisplay';

interface GeneratedImage {
  url: string;
  prompt: string;
  workflow: string;
  timestamp: number;
  params?: Record<string, any>;
  batchId?: string;
  batchIndex?: number;
  status?: 'generating' | 'completed' | 'error';
  refiner?: string;
}

const Index = () => {
  const [activeGenerations, setActiveGenerations] = useState<string[]>([]); // Track active generation batch IDs
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [currentPrompt, setCurrentPrompt] = useState<string | null>(null);
  const [uploadedImageUrls, setUploadedImageUrls] = useState<string[]>([]);
  const [currentWorkflow, setCurrentWorkflow] = useState<string | null>(null);
  const [currentParams, setCurrentParams] = useState<Record<string, any>>({});
  const [currentGlobalParams, setCurrentGlobalParams] = useState<Record<string, any>>({});
  const [currentRefiner, setCurrentRefiner] = useState<string | null>(null);
  const [generatedImages, setGeneratedImages] = useState<GeneratedImage[]>([]);
  const [imageContainerOrder, setImageContainerOrder] = useState<string[]>([]);
  
  const handleUseGeneratedAsInput = async (selectedImageUrl: string) => {
    toast.error('This feature has been removed');
  };

  const handleCreateAgain = (batchId?: string) => {
    if (!currentPrompt) {
      toast.error('No prompt available to regenerate');
      return;
    }
    
    handleSubmitPrompt(
      currentPrompt, 
      uploadedImageUrls.length > 0 ? [] : undefined,
      currentWorkflow || undefined,
      currentParams,
      currentGlobalParams,
      currentRefiner || undefined,
      batchId
    );
    
    toast.info('Creating another image...');
  };

  const handleSubmitPrompt = async (
    prompt: string, 
    imageFiles?: File[],
    workflow?: string,
    params?: Record<string, any>,
    globalParams?: Record<string, any>,
    refiner?: string,
    batchId?: string
  ) => {
    setCurrentPrompt(prompt);
    setCurrentWorkflow(workflow || null);
    setCurrentParams(params || {});
    setCurrentGlobalParams(globalParams || {});
    setCurrentRefiner(refiner || null);
    
    if (imageFiles && imageFiles.length > 0) {
      const localImageUrls = imageFiles.map(file => URL.createObjectURL(file));
      setUploadedImageUrls(localImageUrls);
    }
    
    try {
      const currentBatchId = batchId || `batch-${Date.now()}-${Math.random().toString(36).substring(2, 7)}`;
      
      if (!batchId || !imageContainerOrder.includes(batchId)) {
        setImageContainerOrder(prev => [currentBatchId, ...prev]);
      }
      
      const placeholderImage: GeneratedImage = {
        url: '',
        prompt: prompt,
        workflow: workflow || 'text-to-image',
        timestamp: Date.now(),
        params: { ...params, ...globalParams },
        refiner: refiner,
        batchId: currentBatchId,
        batchIndex: batchId ? generatedImages.filter(img => img.batchId === batchId).length : 0,
        status: 'generating'
      };
      
      setGeneratedImages(prev => [placeholderImage, ...prev]);
      
      setActiveGenerations(prev => [...prev, currentBatchId]);
      
      const requestData = {
        prompt,
        workflow: workflow || 'text-to-image',
        params: params || {},
        global_params: globalParams || {},
        refiner: refiner || 'none',
        has_reference_images: imageFiles ? imageFiles.length > 0 : false,
        reference_image_count: imageFiles ? imageFiles.length : 0,
        batch_id: currentBatchId
      };
      
      console.log('Sending request with data:', requestData);
      
      setTimeout(() => {
        const mockImageUrls = [
          "https://images.unsplash.com/photo-1518020382113-a7e8fc38eac9",
          "https://images.unsplash.com/photo-1561037404-61cd46aa615b",
          "https://images.unsplash.com/photo-1425082661705-1834bfd09dca",
          "https://images.unsplash.com/photo-1560807707-8cc77767d783"
        ];
        
        const existingBatchCount = batchId ? 
          generatedImages.filter(img => img.batchId === batchId && img.status !== 'generating').length : 0;
        
        const imageCount = Math.floor(Math.random() * 2) + 1;
        const newImages: GeneratedImage[] = [];
        
        for (let i = 0; i < imageCount; i++) {
          const randomIndex = Math.floor(Math.random() * mockImageUrls.length);
          const newImageUrl = mockImageUrls[randomIndex];
          
          const newGeneratedImage: GeneratedImage = {
            url: newImageUrl,
            prompt: prompt,
            workflow: workflow || 'text-to-image',
            timestamp: Date.now(),
            params: { ...params, ...globalParams },
            refiner: refiner,
            batchId: currentBatchId,
            batchIndex: existingBatchCount + i,
            status: 'completed'
          };
          
          newImages.push(newGeneratedImage);
        }
        
        setGeneratedImages(prev => {
          const prevCopy = [...prev];
          const filteredImages = prevCopy.filter(img => !(img.batchId === currentBatchId && img.status === 'generating'));
          return [...newImages, ...filteredImages];
        });
        
        setActiveGenerations(prev => prev.filter(id => id !== currentBatchId));
        
        if (!imageUrl) {
          setImageUrl(newImages[0].url);
        }
        
        toast.success(`${imageCount} image${imageCount > 1 ? 's' : ''} generated successfully!`);
      }, 1500);
    } catch (error) {
      console.error('Error generating image:', error);
      toast.error('Failed to generate image. Please try again.');
      
      setGeneratedImages(prev => 
        prev.filter(img => !(img.batchId === batchId && img.status === 'generating'))
      );
      setActiveGenerations(prev => prev.filter(id => id !== batchId));
    }
  };

  const handleReorderContainers = (sourceIndex: number, destinationIndex: number) => {
    setImageContainerOrder(prev => {
      const newOrder = [...prev];
      const [removed] = newOrder.splice(sourceIndex, 1);
      
      if (destinationIndex >= newOrder.length) {
        return newOrder;
      }
      
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

  return (
    <div className="min-h-screen hero-gradient">
      <div className="container max-w-5xl mx-auto px-4 sm:px-6 lg:px-8">
        <Header />
        
        <div className="mt-8 text-center animate-fade-in">
          <h1 className="text-2xl font-medium text-foreground/70">
            Turn your words into <span className="text-primary">art</span>
          </h1>
        </div>
        
        <div className="mt-8 max-w-2xl mx-auto">
          <PromptForm 
            onSubmit={handleSubmitPrompt} 
            isLoading={activeGenerations.length > 0}
            currentPrompt={currentPrompt}
          />
        </div>
        
        <div className="mb-20">
          <ImageDisplay 
            imageUrl={imageUrl}
            prompt={currentPrompt}
            isLoading={activeGenerations.length > 0}
            uploadedImages={uploadedImageUrls}
            generatedImages={generatedImages}
            imageContainerOrder={imageContainerOrder}
            workflow={currentWorkflow}
            onUseGeneratedAsInput={null}
            onCreateAgain={handleCreateAgain}
            onReorderContainers={handleReorderContainers}
            onDeleteImage={handleDeleteImage}
            onDeleteContainer={handleDeleteContainer}
            generationParams={{...currentParams, ...currentGlobalParams}}
          />
        </div>
      </div>
    </div>
  );
};

export default Index;
