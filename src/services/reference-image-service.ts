// ReferenceImageService: fetches and validates reference images for a generation
import { ReferenceImageInfo } from '@/utils/api';
import apiService from '@/utils/api';

export class ReferenceImageService {
  // Convert stored reference image paths to accessible URLs
  static getReferenceImageUrls(bucketId: string, referenceImages: ReferenceImageInfo[]): string[] {
    if (!referenceImages || referenceImages.length === 0) return [];
    
    // The backend should provide full URLs - frontend should NOT construct them
    return referenceImages.map(ref => ref.stored_path);
  }
  
  // Get thumbnail URLs for reference images
  static getReferenceImageThumbnailUrls(bucketId: string, referenceImages: ReferenceImageInfo[]): string[] {
    if (!referenceImages || referenceImages.length === 0) return [];
    
    // The backend should provide full URLs - frontend should NOT construct them
    return referenceImages.map(ref => ref.thumbnail_path);
  }
} 