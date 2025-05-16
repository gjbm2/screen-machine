// Metadata extraction utilities
import { toast } from 'sonner';

// Function to extract metadata using browser fetch API
export const extractMetadata = async (url: string): Promise<Record<string, string>> => {
  console.log('[metadataExtraction] Starting extraction for:', url);
  
  try {
    // Load the image
    const response = await fetch(url, { cache: "no-store" });
    
    if (!response.ok) {
      throw new Error(`Failed to fetch image: ${response.status} ${response.statusText}`);
    }
    
    // Try to extract basic image information
    const blob = await response.blob();
    const tempImage = new Image();
    
    // Create a promise to wait for image load
    const imageLoadPromise = new Promise<void>((resolve, reject) => {
      tempImage.onload = () => resolve();
      tempImage.onerror = () => reject(new Error("Failed to load image"));
      tempImage.src = URL.createObjectURL(blob);
    });
    
    await imageLoadPromise;
    
    // Extract basic metadata
    const metadata: Record<string, string> = {
      'width': tempImage.naturalWidth.toString(),
      'height': tempImage.naturalHeight.toString(),
      'size': `${(blob.size / 1024).toFixed(2)} KB`,
      'type': blob.type,
      'url': url
    };
    
    // Clean up the object URL
    URL.revokeObjectURL(tempImage.src);
    
    console.log('[metadataExtraction] Extraction successful:', metadata);
    return metadata;
  } catch (error) {
    console.error('[metadataExtraction] Extraction failed:', error);
    toast.error('Failed to extract image metadata');
    
    return {
      'error': 'Extraction failed',
      'message': error instanceof Error ? error.message : String(error),
      'url': url
    };
  }
};
