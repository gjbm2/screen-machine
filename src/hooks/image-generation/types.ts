
// Define types for image generation results
export interface GeneratedImage {
  id: string;
  url: string;
  prompt?: string;
  workflow: string;
  params?: Record<string, any>;
  globalParams?: Record<string, any>;
  refiner?: string;
  refinerParams?: Record<string, any>;
  batchId: string;
  batchIndex?: number;
  containerId?: number;
  loading?: boolean;
  error?: boolean;
  timestamp?: number;
  referenceImageUrl?: string | string[];
  title?: string;
}

// Define types for image generation configuration
export interface ImageGenerationConfig {
  prompt: string;
  imageFiles?: File[] | string[];
  workflow: string;
  params?: Record<string, any>;
  globalParams?: Record<string, any>;
  batchId?: string | null;
  refiner?: string;
  refinerParams?: Record<string, any>;
}
