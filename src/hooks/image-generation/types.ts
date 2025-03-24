
export interface ImageGenerationConfig {
  prompt: string;
  imageFiles?: File[] | string[];
  workflow?: string;
  params?: Record<string, any>;
  globalParams?: Record<string, any>;
  batchId?: string | null;
  refiner?: string;
  refinerParams?: Record<string, any>;
  isVerboseDebug?: boolean;
}

export interface GeneratedImage {
  id?: string;
  url?: string;
  batchId: string;
  batchIndex?: number;
  timestamp: number;
  prompt?: string;
  workflow?: string;
  seed?: number;
  params?: Record<string, any>;
  referenceImageUrl?: string;
  status: 'generating' | 'completed' | 'error' | 'failed' | 'to_update';
  containerId?: number;
  error?: string;
  refiner?: string;
  refinerParams?: Record<string, any>;
  title?: string;
}
