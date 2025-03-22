
import { ImageGenerationStatus } from '@/types/workflows';
import { GeneratedImage } from '../types';

export interface ApiResponse {
  images: {
    url: string;
    [key: string]: any;
  }[];
  [key: string]: any;
}

export interface GenerateImagePayload {
  prompt: string;
  workflow: string;
  params?: Record<string, any>;
  global_params?: Record<string, any>;
  refiner?: string;
  refiner_params?: Record<string, any>;
  imageFiles?: File[];
  batch_id?: string;
}

export interface UploadedImageInfo {
  files: File[];
  urls: string[];
}
