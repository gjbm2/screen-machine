
import { ImageGenerationStatus, Workflow } from '@/types/workflows';

export interface GeneratedImage {
  url: string;
  prompt: string;
  workflow: string;
  batchId: string;
  batchIndex: number;
  status: ImageGenerationStatus;
  timestamp: number;
  title?: string;
  params?: Record<string, any>;
  refiner?: string;
  refinerParams?: Record<string, any>;
  referenceImageUrl?: string;
  containerId?: number;
  placeholderId?: string; // Added placeholder ID for tracking
}

export interface ImageGenerationConfig {
  prompt: string;
  imageFiles?: (File | string)[];
  workflow?: string;
  params?: Record<string, any>;
  globalParams?: Record<string, any>;
  refiner?: string;
  refinerParams?: Record<string, any>;
  batchId?: string;
}

export interface GenerationResult {
  image: string;
  status: 'success' | 'error';
  message?: string;
}
