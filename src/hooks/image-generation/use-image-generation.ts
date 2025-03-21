
import { useImageState } from './use-image-state';
import { useImageContainer } from './use-image-container';
import { useImageActions } from './use-image-actions';
import { useImageGenerationApi } from './use-image-generation-api';
import { toast } from 'sonner';

export const useImageGeneration = (
  addConsoleLog: (message: string) => Promise<void>
) => {
  // Image state management
  const {
    imageUrl,
    setImageUrl,
    currentPrompt,
    setCurrentPrompt,
    uploadedImageUrls,
    setUploadedImageUrls,
    currentWorkflow,
    setCurrentWorkflow,
    currentParams,
    setCurrentParams,
    currentGlobalParams,
    setCurrentGlobalParams,
    currentRefiner,
    setCurrentRefiner,
    currentRefinerParams,
    setCurrentRefinerParams,
    generatedImages,
    setGeneratedImages,
    isFirstRun,
    setIsFirstRun
  } = useImageState();

  // Container management
  const {
    imageContainerOrder,
    setImageContainerOrder,
    nextContainerId,
    setNextContainerId,
    handleReorderContainers,
    handleDeleteContainer: deleteContainerFunc
  } = useImageContainer();

  // API calls for image generation
  const {
    activeGenerations,
    generateImage
  } = useImageGenerationApi(
    addConsoleLog,
    setGeneratedImages,
    setImageUrl,
    imageUrl
  );

  // Image action handlers
  const {
    handleCreateAgain: createAgainConfig,
    handleUseGeneratedAsInput,
    handleDeleteImage
  } = useImageActions(
    addConsoleLog,
    setUploadedImageUrls,
    setCurrentWorkflow,
    setGeneratedImages,
    imageContainerOrder,
    generatedImages,
    currentPrompt,
    currentParams,
    currentGlobalParams,
    currentRefiner,
    currentRefinerParams,
    uploadedImageUrls
  );

  const handleCreateAgain = (batchId?: string) => {
    const config = createAgainConfig(batchId);
    if (config) {
      handleSubmitPrompt(
        config.prompt, 
        config.imageFiles,
        config.workflow,
        config.params,
        config.globalParams,
        config.refiner,
        config.refinerParams,
        config.batchId
      );
      
      toast.info('Creating another image...');
    }
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
      
      generateImage(
        prompt,
        currentBatchId,
        currentContainerId,
        referenceImageUrl,
        requestData,
        uploadableFiles,
        batchSize
      );
      
    } catch (error) {
      console.error('Error generating image:', error);
      addConsoleLog(`Error generating image: ${error}`);
      toast.error('Failed to generate image. Please try again.');
    }
  };

  const handleDeleteContainer = (batchId: string) => {
    deleteContainerFunc(batchId, setGeneratedImages);
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
