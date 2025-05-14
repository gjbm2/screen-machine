export interface ImageGenerationConfig {
  prompt: string;
  imageFiles?: File[];
  referenceUrls?: string[];
  workflow?: string;
  params?: Record<string, any>;
  globalParams?: Record<string, any>;
  batchId?: string;
  refiner?: string;
  refinerParams?: Record<string, any>;
  isAsync?: boolean;
}

export interface GeneratedImage {
  id: string;
  url: string;
  prompt: string;
  batchId: string;
  batchIndex: number;
  placeholderId?: string;
  status: 'generating' | 'completed' | 'error' | 'failed' | 'to_update';
  timestamp: number;
  workflow: string;
  params?: Record<string, any>;
  referenceImageUrl?: string;
  refiner?: string;
  refinerParams?: Record<string, any>;
  containerId?: number;
  title?: string;
  uniqueKey?: string;
}

export interface AsyncGenerationUpdate {
  type: 'generation_update';
  batch_id: string;
  status: 'progress' | 'completed' | 'error';
  progress?: number;
  message?: string;
  images?: string[];
  error?: string;
}

export type WebSocketMessage = AsyncGenerationUpdate;
