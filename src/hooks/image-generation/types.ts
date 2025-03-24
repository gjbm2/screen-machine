
export type ImageGenerationStatus = 'generating' | 'completed' | 'error' | 'failed' | 'to_update';

export interface GeneratedImage {
  id?: string;
  url: string;
  batchId: string;
  batchIndex?: number;
  status: ImageGenerationStatus;
  prompt?: string;
  workflow?: string;
  params?: Record<string, any>;
  timestamp: number;
  seed?: number;
  error?: string;
  referenceImageUrl?: string;
  refiner?: string;
  refinerParams?: Record<string, any>;
  containerId?: number;
  title?: string; // Optional title for the image
}

export interface ImageGenerationConfig {
  prompt: string;
  imageFiles?: File[] | string[];
  workflow: string;
  params: Record<string, any>;
  globalParams: Record<string, any>;
  batchId?: string | null;
  refiner?: string;
  refinerParams?: Record<string, any>;
}
