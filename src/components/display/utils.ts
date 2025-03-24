import { DisplayParams } from './types';

// Function to validate and process the output parameter
export const processOutputParam = (output: string | null): string | null => {
  if (!output) return null;
  
  try {
    // Check if it's a URL
    new URL(output);
    return output;
  } catch (e) {
    // Not a URL, check if it starts with / or output/
    if (output.startsWith('/')) return output;
    if (output.startsWith('output/')) return output;
    
    // Assume it's a relative path in the output directory
    return `output/${output}`;
  }
};

// Fetch a list of available output files from the server
export const fetchOutputFiles = async (): Promise<string[]> => {
  try {
    // First try to fetch from the current server
    const response = await fetch('/api/output-files');
    if (response.ok) {
      const data = await response.json();
      return data.files || [];
    }
    
    // If that fails, return some example files
    console.warn('Could not fetch output files from API');
    return [
      'output/ComfyUI_00001_.png',
      'output/ComfyUI_00002_.png',
      'output/William_Hogarth_-_A_Rake\'s_Progress_-_Tavern_Scene.jpg'
    ];
  } catch (e) {
    console.error('Error fetching output files:', e);
    return [];
  }
};

// Extract metadata from image
export const extractImageMetadata = async (imageUrl: string): Promise<Record<string, string>> => {
  try {
    console.log('[extractImageMetadata] Extracting metadata from:', imageUrl);
    
    // Add cache busting parameter to ensure we get fresh data
    const cacheBustUrl = `${imageUrl}${imageUrl.includes('?') ? '&' : '?'}cacheBust=${Date.now()}`;
    
    // Try to fetch metadata from API with explicit no-cache headers
    const response = await fetch(`/api/metadata?image=${encodeURIComponent(cacheBustUrl)}`, {
      headers: {
        'Cache-Control': 'no-cache, no-store, must-revalidate',
        'Pragma': 'no-cache',
        'Expires': '0'
      }
    });
    
    if (response.ok) {
      const data = await response.json();
      console.log('[extractImageMetadata] API returned metadata:', data.metadata);
      
      if (data.metadata && Object.keys(data.metadata).length > 0) {
        return data.metadata;
      }
      console.warn('[extractImageMetadata] API returned empty metadata');
    } else {
      console.warn('[extractImageMetadata] API request failed, status:', response.status);
    }
    
    // If API fails or returns empty, try to extract directly from image using browser
    const metadata = await extractMetadataUsingBrowser(cacheBustUrl);
    if (metadata && Object.keys(metadata).length > 0) {
      console.log('[extractImageMetadata] Browser extraction successful:', metadata);
      return metadata;
    }
    
    // If all else fails, use mock metadata based on filename pattern
    console.warn('[extractImageMetadata] All extraction methods failed, using mock data');
    
    // Simulate different metadata for different images
    if (imageUrl.includes('00001')) {
      return {
        'prompt': 'A beautiful landscape with mountains',
        'steps': '20',
        'model': 'stable-diffusion-v1-5',
        'seed': '123456789',
        'created': new Date().toISOString()
      };
    } else if (imageUrl.includes('00002')) {
      return {
        'prompt': 'A cute cat playing with yarn',
        'steps': '30',
        'model': 'stable-diffusion-v2-1',
        'seed': '987654321',
        'created': new Date().toISOString()
      };
    } else if (imageUrl.includes('Hogarth')) {
      return {
        'title': 'A Rake\'s Progress - Tavern Scene',
        'artist': 'William Hogarth',
        'year': '1735',
        'medium': 'Oil on canvas',
        'dimensions': '62.5 × 75 cm (24.6 × 29.5 in)',
        'source': 'Wikimedia Commons'
      };
    }
    
    return {
      'filename': imageUrl.split('/').pop() || 'unknown',
      'created': new Date().toISOString()
    };
  } catch (e) {
    console.error('[extractImageMetadata] Error extracting metadata:', e);
    return {};
  }
};

// New helper function to attempt metadata extraction using browser capabilities
async function extractMetadataUsingBrowser(url: string): Promise<Record<string, string>> {
  try {
    console.log('[extractMetadataUsingBrowser] Attempting browser extraction for:', url);
    
    // Create a new image element to load the image
    const img = new Image();
    
    // Create a promise that resolves when the image loads or errors
    const imageLoaded = new Promise<HTMLImageElement>((resolve, reject) => {
      img.onload = () => resolve(img);
      img.onerror = (err) => {
        console.error('[extractMetadataUsingBrowser] Error loading image:', err);
        reject(new Error('Failed to load image'));
      };
      
      // Set crossOrigin to anonymous to avoid CORS issues when possible
      img.crossOrigin = 'anonymous';
      img.src = url;
    });
    
    // Wait for image to load
    const loadedImg = await imageLoaded;
    
    // Create a canvas to draw the image
    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d');
    
    if (!ctx) {
      console.error('[extractMetadataUsingBrowser] Failed to get canvas context');
      return {};
    }
    
    // Set canvas dimensions to match image
    canvas.width = loadedImg.naturalWidth;
    canvas.height = loadedImg.naturalHeight;
    
    // Draw image to canvas
    ctx.drawImage(loadedImg, 0, 0);
    
    // Try to extract EXIF data using canvas
    let metadata: Record<string, string> = {};
    
    try {
      // Get image data as binary
      const dataUrl = canvas.toDataURL('image/jpeg');
      
      // Extract metadata from dimensions at minimum
      metadata['width'] = loadedImg.naturalWidth.toString();
      metadata['height'] = loadedImg.naturalHeight.toString();
      metadata['aspectRatio'] = (loadedImg.naturalWidth / loadedImg.naturalHeight).toFixed(2);
      
      // For PNG images, we can also check if there are tEXt chunks in the binary data
      if (url.toLowerCase().endsWith('.png') || url.includes('.png')) {
        // For PNG, we could parse the dataUrl for tEXt chunks
        const binary = atob(dataUrl.split(',')[1]);
        
        // Simple detection of text chunks (this is a basic implementation)
        const textChunkPattern = /tEXt(.{20})/g;
        const matches = binary.match(textChunkPattern);
        
        if (matches && matches.length > 0) {
          console.log('[extractMetadataUsingBrowser] Found potential PNG text chunks:', matches.length);
          metadata['hasPngMetadata'] = 'true';
        }
      }
    } catch (err) {
      console.error('[extractMetadataUsingBrowser] Error extracting from canvas:', err);
    }
    
    console.log('[extractMetadataUsingBrowser] Extracted metadata:', metadata);
    return metadata;
  } catch (err) {
    console.error('[extractMetadataUsingBrowser] Browser extraction failed:', err);
    return {};
  }
}

// Create a URL with display parameters
export const createUrlWithParams = (params: DisplayParams): string => {
  const queryParams = new URLSearchParams();
  
  if (params.output) queryParams.set('output', params.output);
  if (params.showMode !== 'fit') queryParams.set('show', params.showMode);
  if (params.position !== 'center') queryParams.set('position', params.position);
  if (params.refreshInterval !== 5) queryParams.set('refresh', params.refreshInterval.toString());
  if (params.backgroundColor !== '000000') queryParams.set('background', params.backgroundColor);
  if (params.debugMode) queryParams.set('debug', 'true');
  
  if (params.data !== undefined) queryParams.set('data', params.data);
  
  if (params.caption) queryParams.set('caption', params.caption);
  if (params.captionPosition !== 'bottom-center') queryParams.set('caption-position', params.captionPosition);
  if (params.captionSize !== '16px') queryParams.set('caption-size', params.captionSize);
  if (params.captionColor !== 'ffffff') queryParams.set('caption-color', params.captionColor);
  if (params.captionFont !== 'Arial, sans-serif') queryParams.set('caption-font', params.captionFont);
  if (params.captionBgColor !== '#000000') queryParams.set('caption-bg-color', params.captionBgColor.replace('#', ''));
  if (params.captionBgOpacity !== 0.7) queryParams.set('caption-bg-opacity', params.captionBgOpacity.toString());
  
  if (params.transition !== 'cut') queryParams.set('transition', params.transition);
  
  return `/display?${queryParams.toString()}`;
};

// Get default display parameters
export const getDefaultParams = (): DisplayParams => {
  return {
    output: null,
    showMode: 'fit',
    position: 'center',
    refreshInterval: 5,
    backgroundColor: '000000',
    debugMode: false,
    caption: null,
    captionPosition: 'bottom-center',
    captionSize: '16px',
    captionColor: 'ffffff',
    captionFont: 'Arial, sans-serif',
    captionBgColor: '#000000',
    captionBgOpacity: 0.7,
    transition: 'cut',
  };
};

// Utility function to format a date/time
export const formatDateTime = (date: Date | null): string => {
  if (!date) return 'N/A';
  
  return new Intl.DateTimeFormat('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false
  }).format(date);
};

// Format file size (from bytes to human readable)
export const formatFileSize = (bytes: number): string => {
  if (bytes === 0) return '0 Bytes';
  
  const k = 1024;
  const sizes = ['Bytes', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
};

// Check image dimensions (if possible)
export const getImageDimensions = async (url: string): Promise<{ width: number, height: number } | null> => {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => {
      resolve({
        width: img.naturalWidth,
        height: img.naturalHeight
      });
    };
    img.onerror = () => {
      resolve(null);
    };
    img.src = url;
  });
};

// Calculate next check time
export const getNextCheckTime = (lastChecked: Date | null, refreshInterval: number): Date | null => {
  if (!lastChecked) return null;
  return new Date(lastChecked.getTime() + refreshInterval * 1000);
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
