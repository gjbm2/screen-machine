
import { useState } from 'react';

export interface GeneratedImage {
  url: string;
  prompt: string;
  workflow: string;
  timestamp: number;
  params?: Record<string, any>;
  batchId?: string;
  batchIndex?: number;
  status?: string;
  refiner?: string;
  refinerParams?: Record<string, any>;
  referenceImageUrl?: string;
  containerId?: number;
}

export const useImageState = () => {
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [currentPrompt, setCurrentPrompt] = useState<string | null>(null);
  const [uploadedImageUrls, setUploadedImageUrls] = useState<string[]>([]);
  const [currentWorkflow, setCurrentWorkflow] = useState<string | null>(null);
  const [currentParams, setCurrentParams] = useState<Record<string, any>>({});
  const [currentGlobalParams, setCurrentGlobalParams] = useState<Record<string, any>>({});
  const [currentRefiner, setCurrentRefiner] = useState<string | null>(null);
  const [currentRefinerParams, setCurrentRefinerParams] = useState<Record<string, any>>({});
  const [generatedImages, setGeneratedImages] = useState<GeneratedImage[]>([]);
  const [isFirstRun, setIsFirstRun] = useState(true);

  return {
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
  };
};

export default useImageState;
