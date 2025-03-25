
// Metadata extraction utilities
import { toast } from 'sonner';

// Function to extract metadata using browser fetch API
export const extractMetadataUsingBrowser = async (url: string): Promise<Record<string, string>> => {
  console.log('[metadataExtraction] Attempting browser-based extraction for:', url);
  
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
    
    console.log('[metadataExtraction] Browser extraction successful:', metadata);
    return metadata;
  } catch (error) {
    console.error('[metadataExtraction] Browser extraction failed:', error);
    return {
      'error': 'Browser extraction failed',
      'message': error instanceof Error ? error.message : String(error)
    };
  }
};

// Function to extract metadata using API endpoint (if available)
export const extractMetadataUsingApi = async (url: string): Promise<Record<string, string>> => {
  console.log('[metadataExtraction] Attempting API-based extraction for:', url);
  
  try {
    // Check if URL is external and show warning
    const isExternalUrl = url.startsWith('http') && !url.includes(window.location.hostname);
    if (isExternalUrl) {
      console.warn('[metadataExtraction] External URL detected, metadata extraction may be limited:', url);
    }
    
    // Prepare the API endpoint
    const apiEndpoint = '/api/extract-metadata';
    
    // Prepare the request payload
    const payload = {
      url: url,
      type: 'image'
    };
    
    // Make the API request
    const response = await fetch(apiEndpoint, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(10000) // 10 second timeout
    });
    
    if (!response.ok) {
      throw new Error(`API extraction failed: ${response.status} ${response.statusText}`);
    }
    
    const data = await response.json();
    
    if (!data || !data.metadata) {
      throw new Error('API response did not include valid metadata');
    }
    
    console.log('[metadataExtraction] API extraction successful:', data.metadata);
    return data.metadata;
  } catch (error) {
    // If the API doesn't exist or fails, throw an error to trigger fallback
    console.error('[metadataExtraction] API extraction failed:', error);
    throw error;
  }
};

// Unified function to extract metadata - tries API first, then falls back to browser
export const extractMetadata = async (url: string): Promise<Record<string, string>> => {
  try {
    console.log('[metadataExtraction] Starting extraction for:', url);
    
    // Try API extraction first
    try {
      return await extractMetadataUsingApi(url);
    } catch (apiError) {
      console.warn('[metadataExtraction] API extraction failed, falling back to browser:', apiError);
      
      // Fall back to browser extraction
      const browserMetadata = await extractMetadataUsingBrowser(url);
      
      if (Object.keys(browserMetadata).length > 0 && !browserMetadata.error) {
        return browserMetadata;
      }
      
      throw new Error('All extraction methods failed');
    }
  } catch (error) {
    console.error('[metadataExtraction] All extraction methods failed:', error);
    toast.error('Failed to extract image metadata');
    
    return {
      'error': 'Extraction failed',
      'message': error instanceof Error ? error.message : String(error),
      'url': url
    };
  }
};
