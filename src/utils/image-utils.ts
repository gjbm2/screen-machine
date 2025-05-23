/**
 * Utility functions for image handling in Screen Machine
 */
import apiService from './api';

/**
 * Checks if a filename is a video file
 * @param filename The filename to check
 * @returns True if the file is a video
 */
export const isVideoFile = (filename: string): boolean => {
  const videoExtensions = ['.mp4', '.avi', '.mov', '.webm', '.mkv'];

  if (typeof filename !== 'string') return false;

  // Strip any trailing "#dup" marker that we append to favourite clones
  const baseName = filename.endsWith('#dup') ? filename.slice(0, -4) : filename;

  return videoExtensions.some((ext) => baseName.toLowerCase().endsWith(ext));
};

/**
 * Gets the appropriate URL to use for a reference image
 * For videos, returns a high-resolution image from the first frame
 * For images, returns the raw URL
 * 
 * @param item The image item or DnD data with URLs
 * @returns The URL to use as a reference
 */
export const getReferenceUrl = (item: any): string => {
  // First, log the structure of the item to understand what we're working with
  console.log('getReferenceUrl input item:', item);
  
  // Extract the filename/id to check if it's a video
  let filename = '';
  if (typeof item.id === 'string') {
    filename = item.id;
  } else if (item.filename) {
    filename = item.filename;
  } else if (item.image?.id) {
    filename = item.image.id;
  }
  
  const isVideo = isVideoFile(filename);
  console.log(`getReferenceUrl: File "${filename}" isVideo=${isVideo}`);
  
  // For videos, use the jpg_from_mp4 API for higher quality first frame
  if (isVideo) {
    // Get the raw URL of the video if available
    const videoRawUrl = 
      item.raw_url || 
      item.image?.raw_url || 
      item.url || 
      item.image?.url || 
      item.image?.urlFull ||
      (item.bucketId && filename ? `/output/${item.bucketId}/${filename}` : '');
    
    if (videoRawUrl) {
      // Use the high-resolution jpg_from_mp4 endpoint with the proper API URL
      // If videoRawUrl starts with a slash, prepend the window.location.origin
      const absoluteVideoUrl = videoRawUrl.startsWith('/') 
        ? `${window.location.origin}${videoRawUrl}`
        : videoRawUrl;
        
      const highResImageUrl = `${apiService.getApiUrl()}/jpg_from_mp4?file=${encodeURIComponent(absoluteVideoUrl)}`;
      console.log('Using high-resolution first frame for video reference:', highResImageUrl);
      return highResImageUrl;
    }
    
    // Fallback to thumbnail URLs if raw URL isn't available
    const thumbnailUrl = 
      item.thumbnail_url || 
      item.image?.thumbnail_url || 
      item.image?.urlThumb ||
      (item.bucketId && filename ? `/api/thumbnails/api/jpg_from_mp4/${item.bucketId}/${filename}` : null);
    
    if (thumbnailUrl) {
      console.log('Falling back to thumbnail_url for video reference:', thumbnailUrl);
      return thumbnailUrl;
    }
    
    console.warn('Video detected but no raw or thumbnail URL found');
  }
  
  // For non-videos, find a raw URL in any of the possible locations
  const rawUrl = 
    item.raw_url || 
    item.image?.raw_url || 
    item.url || 
    item.image?.url || 
    item.image?.urlFull ||
    (item.bucketId && filename ? `/output/${item.bucketId}/${filename}` : '');
  
  if (rawUrl) {
    console.log('Using raw_url for reference:', rawUrl);
    return rawUrl;
  }
  
  // Last resort fallback - return thumbnail even for non-videos if that's all we have
  const lastResortUrl = 
    item.thumbnail_url || 
    item.image?.thumbnail_url || 
    item.image?.urlThumb;
  
  if (lastResortUrl) {
    console.log('Falling back to thumbnail URL:', lastResortUrl);
    return lastResortUrl;
  }
  
  console.warn('Could not find any suitable reference URL for item:', item);
  return '';
}; 