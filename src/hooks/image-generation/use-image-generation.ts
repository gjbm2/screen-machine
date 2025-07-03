import { useState } from 'react';
import { useImageState } from './use-image-state';
import { useImageContainer } from './use-image-container';
import { useImageActions } from './use-image-actions';
import { useImageGenerationApi } from './use-image-generation-api';
import { useImageGenerationLoading } from './use-image-generation-loading';
import { useUploadedImages } from './use-uploaded-images';
import { usePromptSubmission } from './use-prompt-submission';
import { useContainerOrderEffect } from './use-container-order-effect';
import typedWorkflows from '@/data/typedWorkflows';
import { GeneratedImage } from './types';

// Interface for console messages
export interface ConsoleMessage {
  type: string;
  message: string;
  timestamp: number;
  details?: any;
}

// Define the return type for useImageGeneration
export interface UseImageGenerationResult {
  generatedImages: GeneratedImage[];
  activeGenerations: string[];
  imageUrl: string | null;
  currentPrompt: string;
  uploadedImageUrls: string[];
  currentWorkflow: string;
  currentParams: Record<string, any>;
  currentGlobalParams: Record<string, any>;
  imageContainerOrder: string[];
  expandedContainers: Record<string, boolean>;
  lastBatchId: string | null;
  isFirstRun: boolean;
  fullscreenRefreshTrigger: number;
  setCurrentPrompt: React.Dispatch<React.SetStateAction<string>>;
  setUploadedImageUrls: (urls: string[] | ((prev: string[]) => string[])) => void;
  setCurrentWorkflow: React.Dispatch<React.SetStateAction<string>>;
  setCurrentParams: React.Dispatch<React.SetStateAction<Record<string, any>>>;
  setCurrentGlobalParams: React.Dispatch<React.SetStateAction<Record<string, any>>>;
  setImageContainerOrder: React.Dispatch<React.SetStateAction<string[]>>;
  setExpandedContainers: React.Dispatch<React.SetStateAction<Record<string, boolean>>>;
  handleSubmitPrompt: (prompt: string, imageInputs?: (File | string)[] | undefined, workflow?: string, 
    workflowParams?: Record<string, any>, globalParams?: Record<string, any>, refiner?: string, 
    refinerParams?: Record<string, any>, publishDestination?: string, batchId?: string) => Promise<string | null>;
  handleUseGeneratedAsInput: (url: string, append?: boolean) => Promise<void>;
  handleCreateAgain: (image: GeneratedImage) => void;
  handleDownloadImage: (url: string, filename?: string) => Promise<void>;
  handleDeleteImage: (imageId: string, containerId: string) => void;
  handleReorderContainers: (oldIndex: number, newIndex: number) => void;
  handleDeleteContainer: (containerId: string) => void;
  setGeneratedImages: React.Dispatch<React.SetStateAction<GeneratedImage[]>>;
  removeUrl: (url: string) => void;
}

export const useImageGeneration = (
  addConsoleLog: (log: ConsoleMessage) => void
): UseImageGenerationResult => {
  // Use "auto" as the default workflow to let the backend resolve the best workflow
  const getDefaultWorkflowId = (): string => {
    console.log("useImageGeneration: Using auto workflow for backend resolution");
    return 'auto';
  };

  // State for current prompts and workflows using the default workflow ID
  const [currentPrompt, setCurrentPrompt] = useState<string>('');
  const [currentWorkflow, setCurrentWorkflow] = useState<string>(getDefaultWorkflowId());
  const [currentParams, setCurrentParams] = useState<Record<string, any>>({});
  const [currentGlobalParams, setCurrentGlobalParams] = useState<Record<string, any>>({});

  // Initialize global image counter if it doesn't exist
  useState(() => {
    if (typeof window.imageCounter === 'undefined') {
      window.imageCounter = 0;
    }
  });

  // Use our custom hooks
  const { generatedImages, setGeneratedImages } = useImageState();
  
  const { 
    imageContainerOrder, 
    setImageContainerOrder,
    expandedContainers,
    setExpandedContainers,
    nextContainerId,
    setNextContainerId,
    handleReorderContainers: containerReorder,
    handleDeleteContainer: internalHandleDeleteContainer,
    handleAddNewContainer,
    collapseAllExcept
  } = useImageContainer();

  const {
    isFirstRun,
    setIsFirstRun,
    fullscreenRefreshTrigger,
    lastBatchIdUsed,
    setLastBatchIdUsed,
    activeGenerations,
    setActiveGenerations,
    handleGenerationComplete
  } = useImageGenerationLoading();

  const { uploadedImageUrls, setUploadedImageUrls, removeUrl } = useUploadedImages();

  // Apply the container order effect
  useContainerOrderEffect({
    generatedImages,
    setImageContainerOrder
  });

  const {
    lastBatchId,
    generateImages,
  } = useImageGenerationApi(
    addConsoleLog,
    setGeneratedImages,
    setImageContainerOrder,
    nextContainerId,
    setNextContainerId,
    handleGenerationComplete
  );

  const { handleSubmitPrompt } = usePromptSubmission({
    currentWorkflow,
    currentParams,
    currentGlobalParams,
    lastBatchIdUsed,
    setIsFirstRun,
    setLastBatchIdUsed,
    generateImages,
    collapseAllExcept
  });
  
  const {
    imageUrl,
    handleUseGeneratedAsInput,
    handleCreateAgain,
    handleDownloadImage,
    handleDeleteImage,
    handleReorderContainers,
  } = useImageActions({
    setGeneratedImages,
    imageContainerOrder,
    setImageContainerOrder,
    setCurrentPrompt,
    setCurrentWorkflow,
    uploadedImageUrls,
    setUploadedImageUrls,
    handleSubmitPrompt,
    removeUrl
  });

  // Wrapper for handleDeleteContainer to match the expected signature
  const handleDeleteContainer = (batchId: string) => {
    internalHandleDeleteContainer(batchId, setGeneratedImages);
  };

  return {
    generatedImages,
    activeGenerations,
    imageUrl,
    currentPrompt,
    uploadedImageUrls,
    currentWorkflow,
    currentParams,
    currentGlobalParams,
    imageContainerOrder,
    expandedContainers,
    lastBatchId,
    isFirstRun,
    fullscreenRefreshTrigger,
    setCurrentPrompt,
    setUploadedImageUrls,
    setCurrentWorkflow,
    setCurrentParams,
    setCurrentGlobalParams,
    setImageContainerOrder,
    setExpandedContainers,
    handleSubmitPrompt,
    handleUseGeneratedAsInput,
    handleCreateAgain,
    handleDownloadImage,
    handleDeleteImage,
    handleReorderContainers,
    handleDeleteContainer,
    setGeneratedImages,
    removeUrl
  };
};
