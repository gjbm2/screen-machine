// ReferenceImageService: fetches and validates reference images for a generation
import { ReferenceImageInfo } from '@/utils/api';
import apiService from '@/utils/api';

export class ReferenceImageService {
  // Convert stored reference image paths to accessible URLs
  static getReferenceImageUrls(bucketId: string, referenceImages: ReferenceImageInfo[]): string[] {
    if (!referenceImages || referenceImages.length === 0) return [];
    
    // The backend provides stored_path as just filenames (e.g., "filename.ref.1")
    // We need to construct the full URLs
    return referenceImages.map(ref => {
      // If it's already a full URL, return as-is
      if (ref.stored_path.startsWith('http') || ref.stored_path.startsWith('/')) {
        return ref.stored_path;
      }
      // Otherwise, construct the full URL
      return `/output/${bucketId}/${ref.stored_path}`;
    });
  }
  
  // Get thumbnail URLs for reference images
  static getReferenceImageThumbnailUrls(bucketId: string, referenceImages: ReferenceImageInfo[]): string[] {
    if (!referenceImages || referenceImages.length === 0) return [];
    
    // The backend provides thumbnail_path as relative paths (e.g., "thumbnails/filename.ref.1.jpg")
    // We need to construct the full URLs
    return referenceImages.map(ref => {
      // If it's already a full URL, return as-is
      if (ref.thumbnail_path.startsWith('http') || ref.thumbnail_path.startsWith('/')) {
        return ref.thumbnail_path;
      }
      // Otherwise, construct the full URL
      return `/output/${bucketId}/${ref.thumbnail_path}`;
    });
  }
} 