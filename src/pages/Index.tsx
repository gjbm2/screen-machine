
import React, { useState, useEffect, useCallback } from 'react';
import { toast } from 'sonner';
import Header from '@/components/Header';
import PromptForm from '@/components/PromptForm';
import ImageDisplay from '@/components/image-display/ImageDisplay';
import ResizableConsole from '@/components/debug/ResizableConsole';
import { Terminal } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import Footer from '@/components/Footer';
import introTexts from '@/data/intro-texts.json';
import apiService from '@/utils/api';

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
  refinerParams?: Record<string, any>;
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
  const [currentRefinerParams, setCurrentRefinerParams] = useState<Record<string, any>>({});
  const [generatedImages, setGeneratedImages] = useState<GeneratedImage[]>([]);
  const [imageContainerOrder, setImageContainerOrder] = useState<string[]>([]);
  const [consoleLogs, setConsoleLogs] = useState<string[]>([]);
  const [isConsoleVisible, setIsConsoleVisible] = useState(false);
  const [nextContainerId, setNextContainerId] = useState<number>(1);
  const [isFirstRun, setIsFirstRun] = useState(true);
  const [randomIntroText, setRandomIntroText] = useState('');
  const [activeDetailBatchId, setActiveDetailBatchId] = useState<string | null>(null);
  const [activeDetailIndex, setActiveDetailIndex] = useState<number>(0);
  const [isDetailViewOpen, setIsDetailViewOpen] = useState(false);

  // Navigation functions for keyboard support
  const handleNavigateGlobal = useCallback((index: number) => {
    if (!allFlattenedImages || allFlattenedImages.length === 0) return;
    
    const image = allFlattenedImages[index];
    if (image) {
      setActiveDetailBatchId(image.batchId);
      setActiveDetailIndex(image.batchIndex || 0);
    }
  }, []);

  const handleKeyNavigation = useCallback((e: KeyboardEvent) => {
    // Only handle arrow keys when in detail view
    if (isDetailViewOpen) {
      if (e.key === 'ArrowLeft') {
        e.preventDefault();
        const currentIndex = allFlattenedImages.findIndex(
          img => img.batchId === activeDetailBatchId && img.batchIndex === activeDetailIndex
        );
        
        if (currentIndex > 0) {
          handleNavigateGlobal(currentIndex - 1);
        }
      } else if (e.key === 'ArrowRight') {
        e.preventDefault();
        const currentIndex = allFlattenedImages.findIndex(
          img => img.batchId === activeDetailBatchId && img.batchIndex === activeDetailIndex
        );
        
        if (currentIndex < allFlattenedImages.length - 1) {
          handleNavigateGlobal(currentIndex + 1);
        }
      } else if (e.key === 'Escape') {
        setIsDetailViewOpen(false);
      }
    }
  }, [isDetailViewOpen, activeDetailBatchId, activeDetailIndex, handleNavigateGlobal]);

  // Compute all images flattened into a single array for navigation
  const allFlattenedImages = generatedImages
    .filter(img => img.status === 'completed')
    .sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0));

  useEffect(() => {
    window.addEventListener('keydown', handleKeyNavigation);
    return () => {
      window.removeEventListener('keydown', handleKeyNavigation);
    };
  }, [handleKeyNavigation]);

  useEffect(() => {
    const introsList = introTexts.intros || [];
    const randomIndex = Math.floor(Math.random() * introsList.length);
    setRandomIntroText(introsList[randomIndex]);
  }, []);

  useEffect(() => {
    if (currentGlobalParams.showConsoleOutput) {
      setIsConsoleVisible(true);
    }
  }, [currentGlobalParams.showConsoleOutput]);

  const addConsoleLog = async (message: string) => {
    const timestamp = new Date().toLocaleTimeString();
    const formattedMessage = `[${timestamp}] ${message}`;
    
    setConsoleLogs(prev => [...prev, formattedMessage]);
    
    await apiService.sendLog(message);
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

  const handleCreateAgain = (batchId?: string) => {
    // Find the image with the given batchId
    const batchImage = generatedImages.find(img => img.batchId === batchId);
    
    if (!batchImage) {
      toast.error('No image information available to regenerate');
      return;
    }
    
    // Use the selected image's prompt, not the current prompt
    const imagePrompt = batchImage.prompt;
    const imageWorkflow = batchImage.workflow;
    const imageParams = batchImage.params || {};
    const imageRefiner = batchImage.refiner || null;
    const imageRefinerParams = batchImage.refinerParams || {};
    
    let imageFiles;
    if (batchImage.referenceImageUrl) {
      imageFiles = [batchImage.referenceImageUrl];
    }
    
    // Set current prompt to the image's prompt temporarily for UI feedback
    setCurrentPrompt(imagePrompt);
    
    // Use a batch size of 1 for recreations
    const modifiedGlobalParams = { 
      ...currentGlobalParams, 
      batchSize: 1 
    };
    
    addConsoleLog(`Creating another image with same settings: "${imagePrompt.substring(0, 50)}${imagePrompt.length > 50 ? '...' : ''}"`);
    
    handleSubmitPrompt(
      imagePrompt, 
      imageFiles,
      imageWorkflow,
      imageParams,
      modifiedGlobalParams,
      imageRefiner || undefined,
      imageRefinerParams,
      batchId
    );
    
    toast.info('Creating another image with the same settings...');
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
            prev.filter(img => !(img.batchId === currentBatchId && img.status === 'generating'))
          );
          setActiveGenerations(prev => prev.filter(id => id !== currentBatchId));
        }
      } catch (error) {
        console.error('Error calling API:', error);
        addConsoleLog(`Error calling API: ${error}`);
        
        setGeneratedImages(prev => 
          prev.filter(img => !(img.batchId === currentBatchId && img.status === 'generating'))
        );
        setActiveGenerations(prev => prev.filter(id => id !== currentBatchId));
        
        toast.error('Failed to generate image. Please try again.');
      }
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
  
  // Add handler for detail view to track state
  const handleOpenDetailView = (batchId: string, index: number) => {
    setActiveDetailBatchId(batchId);
    setActiveDetailIndex(index);
    setIsDetailViewOpen(true);
  };
  
  const handleCloseDetailView = () => {
    setIsDetailViewOpen(false);
  };

  return (
    <div className="min-h-screen hero-gradient flex flex-col">
      <div className="container max-w-full mx-auto px-4 sm:px-6 lg:px-8 flex-grow">
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
          <div className="mt-8 mb-6 text-center">
            <p className="text-lg text-muted-foreground max-w-2xl mx-auto">
              {randomIntroText}
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
            onOpenDetailView={handleOpenDetailView}
            onCloseDetailView={handleCloseDetailView}
            activeDetailBatchId={activeDetailBatchId}
            activeDetailIndex={activeDetailIndex}
            isDetailViewOpen={isDetailViewOpen}
            allFlattenedImages={allFlattenedImages}
            onNavigateGlobal={handleNavigateGlobal}
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
