
import React, { useState, useEffect } from 'react';
import { toast } from 'sonner';
import Header from '@/components/Header';
import PromptForm from '@/components/PromptForm';
import ImageDisplay from '@/components/image-display/ImageDisplay';
import ResizableConsole from '@/components/debug/ResizableConsole';
import { Terminal } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import Footer from '@/components/Footer';

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
  referenceImageUrl?: string;
  containerId?: number;
}

const Index = () => {
  const [activeGenerations, setActiveGenerations] = useState<string[]>([]);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [currentPrompt, setCurrentPrompt] = useState<string | null>(null);
  const [uploadedImageUrls, setUploadedImageUrls] = useState<string[]>([]);
  const [currentWorkflow, setCurrentWorkflow] = useState<string | null>(null);
  const [currentParams, setCurrentParams] = useState<Record<string, any>>({});
  const [currentGlobalParams, setCurrentGlobalParams] = useState<Record<string, any>>({});
  const [currentRefiner, setCurrentRefiner] = useState<string | null>(null);
  const [generatedImages, setGeneratedImages] = useState<GeneratedImage[]>([]);
  const [imageContainerOrder, setImageContainerOrder] = useState<string[]>([]);
  const [consoleLogs, setConsoleLogs] = useState<string[]>([]);
  const [isConsoleVisible, setIsConsoleVisible] = useState(false);
  const [nextContainerId, setNextContainerId] = useState<number>(1);
  const [isFirstRun, setIsFirstRun] = useState(true);
  
  useEffect(() => {
    if (currentGlobalParams.showConsoleOutput) {
      setIsConsoleVisible(true);
    }
  }, [currentGlobalParams.showConsoleOutput]);
  
  const addConsoleLog = (message: string) => {
    const timestamp = new Date().toLocaleTimeString();
    setConsoleLogs(prev => [...prev, `[${timestamp}] ${message}`]);
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
    } catch (error) {
      console.error('Error using image as input:', error);
      addConsoleLog(`Error using image as input: ${error}`);
      toast.error('Failed to use image as input');
    }
  };

  const handleCreateAgain = (batchId?: string) => {
    if (!currentPrompt) {
      toast.error('No prompt available to regenerate');
      return;
    }
    
    let originalParams = { ...currentParams };
    let originalWorkflow = currentWorkflow;
    let originalRefiner = currentRefiner;
    let originalUploadedImages = [...uploadedImageUrls];
    
    if (batchId) {
      const batchImage = generatedImages.find(img => img.batchId === batchId);
      if (batchImage) {
        originalParams = batchImage.params || {};
        originalWorkflow = batchImage.workflow;
        originalRefiner = batchImage.refiner || null;
        
        if (batchImage.referenceImageUrl) {
          originalUploadedImages = [batchImage.referenceImageUrl];
        } else {
          originalUploadedImages = [];
        }
      }
    }
    
    const imageFiles = originalUploadedImages.length > 0 ? originalUploadedImages : undefined;
    
    const modifiedGlobalParams = { 
      ...currentGlobalParams, 
      batchSize: 1 
    };
    
    addConsoleLog(`Creating another image with same settings: "${currentPrompt.substring(0, 50)}${currentPrompt.length > 50 ? '...' : ''}"`);
    
    handleSubmitPrompt(
      currentPrompt, 
      imageFiles,
      originalWorkflow || undefined,
      originalParams,
      modifiedGlobalParams,
      originalRefiner || undefined,
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
    batchId?: string
  ) => {
    // Set first run to false when the generator is first run
    if (isFirstRun) {
      setIsFirstRun(false);
    }
    
    setCurrentPrompt(prompt);
    setCurrentWorkflow(workflow || null);
    setCurrentParams(params || {});
    setCurrentGlobalParams(globalParams || {});
    setCurrentRefiner(refiner || null);
    
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
      if (imageFiles && imageFiles.length > 0) {
        if (typeof imageFiles[0] === 'string') {
          referenceImageUrl = imageFiles[0] as string;
        } else {
          const file = imageFiles[0] as File;
          referenceImageUrl = URL.createObjectURL(file);
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
      
      const requestData = {
        prompt,
        workflow: workflow || 'text-to-image',
        params: params || {},
        global_params: globalParams || {},
        refiner: refiner || 'none',
        has_reference_images: imageFiles ? imageFiles.length > 0 : false,
        reference_image_count: imageFiles ? imageFiles.length : 0,
        batch_id: currentBatchId,
        batch_size: batchSize
      };
      
      console.log('Sending request with data:', requestData);
      addConsoleLog(`Sending request: ${JSON.stringify(requestData, null, 2)}`);
      
      setTimeout(() => {
        try {
          const mockImageUrls = [
            "https://images.unsplash.com/photo-1518020382113-a7e8fc38eac9",
            "https://images.unsplash.com/photo-1561037404-61cd46aa615b",
            "https://images.unsplash.com/photo-1425082661705-1834bfd09dca",
            "https://images.unsplash.com/photo-1560807707-8cc77767d783"
          ];
          
          const existingBatchCount = batchId ? 
            generatedImages.filter(img => img.batchId === batchId && img.status !== 'generating').length : 0;
          
          const imageCount = batchSize;
          const newImages: GeneratedImage[] = [];
          
          addConsoleLog(`Received response: ${imageCount} images generated`);
          
          for (let i = 0; i < imageCount; i++) {
            const randomIndex = Math.floor(Math.random() * mockImageUrls.length);
            const newImageUrl = mockImageUrls[randomIndex];
            
            const newGeneratedImage: GeneratedImage = {
              url: newImageUrl,
              prompt: prompt,
              workflow: workflow || 'text-to-image',
              timestamp: Date.now(),
              params: { ...params },
              refiner: refiner,
              batchId: currentBatchId,
              batchIndex: existingBatchCount + i,
              status: 'completed',
              referenceImageUrl,
              containerId: batchId ? 
                generatedImages.find(img => img.batchId === batchId)?.containerId : 
                currentContainerId
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
          
          toast.success(`${imageCount} image${imageCount > 1 ? 's' : ''} generated successfully!`);
        } catch (error) {
          console.error('Error processing response:', error);
          addConsoleLog(`Error processing response: ${error}`);
          
          setGeneratedImages(prev => 
            prev.filter(img => !(img.batchId === currentBatchId && img.status === 'generating'))
          );
          setActiveGenerations(prev => prev.filter(id => id !== currentBatchId));
          
          toast.error('An error occurred while processing images.');
        }
      }, 5000);
    } catch (error) {
      console.error('Error generating image:', error);
      addConsoleLog(`Error generating image: ${error}`);
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

  const handleCloseConsole = () => {
    setIsConsoleVisible(false);
    setCurrentGlobalParams(prev => ({
      ...prev,
      showConsoleOutput: false
    }));
  };

  const toggleConsole = () => {
    const newState = !isConsoleVisible;
    setIsConsoleVisible(newState);
    setCurrentGlobalParams(prev => ({
      ...prev,
      showConsoleOutput: newState
    }));
  };

  return (
    <div className="min-h-screen hero-gradient flex flex-col">
      <div className="container max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 flex-grow">
        <div className="flex justify-between items-center">
          <Header />
          <div className="flex items-center space-x-1">
            <Tooltip>
              <TooltipTrigger asChild>
                <Button 
                  variant="ghost" 
                  size="icon" 
                  onClick={toggleConsole}
                  className="h-10 w-10 bg-background/80 backdrop-blur-sm z-50 mr-2"
                >
                  <Terminal className={`h-5 w-5 ${isConsoleVisible ? 'text-primary' : ''}`} />
                </Button>
              </TooltipTrigger>
              <TooltipContent>
                <p>Toggle Command Console</p>
              </TooltipContent>
            </Tooltip>
          </div>
        </div>
        
        {isFirstRun && (
          <div className="mt-16 mb-8 text-center">
            <h1 className="text-3xl font-bold mb-4">Welcome to the Image Generator</h1>
            <p className="text-lg text-muted-foreground max-w-2xl mx-auto">
              Transform your imagination into stunning visuals. Enter a prompt below, 
              select your options, and watch as your ideas come to life.
            </p>
          </div>
        )}
        
        <div className={`${isFirstRun ? 'mt-4' : 'mt-8'} transition-all duration-500`}>
          <PromptForm 
            onSubmit={handleSubmitPrompt} 
            isLoading={activeGenerations.length > 0}
            currentPrompt={currentPrompt}
            isFirstRun={isFirstRun}
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
            onUseGeneratedAsInput={handleUseGeneratedAsInput}
            onCreateAgain={handleCreateAgain}
            onReorderContainers={handleReorderContainers}
            onDeleteImage={handleDeleteImage}
            onDeleteContainer={handleDeleteContainer}
            generationParams={{...currentParams, ...currentGlobalParams}}
          />
        </div>
      </div>
      
      <Footer />
      
      <ResizableConsole 
        logs={consoleLogs}
        isVisible={isConsoleVisible}
        onClose={handleCloseConsole}
      />
    </div>
  );
};

export default Index;
