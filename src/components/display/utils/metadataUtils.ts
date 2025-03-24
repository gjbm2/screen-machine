
// Metadata utilities for processing and extracting metadata from images
import { extractMetadataUsingApi, extractMetadataUsingBrowser } from './metadataExtraction';

// Main function to extract metadata from an image file
export const extractImageMetadata = async (url: string): Promise<Record<string, string>> => {
  try {
    console.log('Extracting metadata for image:', url);
    
    // Try with API endpoint first
    try {
      return await extractMetadataUsingApi(url);
    } catch (apiError) {
      console.error('Error with API endpoint:', apiError);
      
      // Fallback to browser-based extraction if the API fails
      console.log('Falling back to browser-based extraction');
      try {
        const browserMetadata = await extractMetadataUsingBrowser(url);
        if (Object.keys(browserMetadata).length > 0) {
          return browserMetadata;
        }
      } catch (browserError) {
        console.error('Browser extraction also failed:', browserError);
      }
      
      // Return an error record if all methods fail
      return {
        'error': 'Failed to extract metadata',
        'message': apiError instanceof Error ? apiError.message : String(apiError)
      };
    }
  } catch (error) {
    console.error('Error extracting metadata:', error);
    return {
      'error': 'Failed to extract metadata',
      'message': error instanceof Error ? error.message : String(error)
    };
  }
};

/**
 * Process caption with metadata substitutions
 */
export const processCaptionWithMetadata = (caption: string | null, metadata: Record<string, string>): string | null => {
  if (!caption) return null;
  
  let processedCaption = caption;
  
  Object.entries(metadata).forEach(([key, value]) => {
    const regex = new RegExp(`\\{${key}\\}`, 'g');
    processedCaption = processedCaption?.replace(regex, value) || '';
  });
  
  return processedCaption;
};
