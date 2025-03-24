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

export const extractImageMetadata = async (url: string): Promise<Record<string, string>> => {
  try {
    console.log('Extracting metadata for image:', url);
    
    // Try with fetch API directly to the endpoint
    try {
      const response = await fetch('/api/extract-metadata', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ imageUrl: url }),
      });

      // Check if the response is valid JSON
      const contentType = response.headers.get('content-type');
      if (!contentType || !contentType.includes('application/json')) {
        console.error('Expected JSON response but got', contentType);
        throw new Error(`Expected JSON response but got ${contentType}`);
      }

      // Parse the response
      const data = await response.json();
      
      if (!response.ok) {
        console.error('API error response:', data);
        throw new Error(data.error || `Failed to extract metadata: ${response.status}`);
      }
      
      console.log('API response data:', data);
      
      if (!data.success) {
        throw new Error(data.error || 'Failed to extract metadata');
      }
      
      // Convert all values to strings for consistency
      const stringMetadata: Record<string, string> = {};
      Object.entries(data.metadata).forEach(([key, value]) => {
        stringMetadata[key] = String(value);
      });
      
      console.log('Extracted metadata:', stringMetadata);
      return stringMetadata;
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
      
      // Return an empty record if all methods fail
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

// Enhanced browser-based metadata extraction
async function extractMetadataUsingBrowser(url: string): Promise<Record<string, string>> {
  try {
    console.log('[extractMetadataUsingBrowser] Starting browser extraction for:', url);
    
    const img = new Image();
    
    const imageLoaded = new Promise<HTMLImageElement>((resolve, reject) => {
      img.onload = () => resolve(img);
      img.onerror = (err) => {
        console.error('[extractMetadataUsingBrowser] Image load error:', err);
        reject(new Error('Failed to load image'));
      };
      
      img.crossOrigin = 'anonymous';
      img.src = url;
    });
    
    const loadedImg = await imageLoaded;
    console.log('[extractMetadataUsingBrowser] Image loaded successfully');
    
    const metadata: Record<string, string> = {
      width: loadedImg.naturalWidth.toString(),
      height: loadedImg.naturalHeight.toString(),
      aspectRatio: (loadedImg.naturalWidth / loadedImg.naturalHeight).toFixed(2)
    };
    
    // Create a canvas to analyze image data
    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d');
    
    if (ctx) {
      canvas.width = loadedImg.naturalWidth;
      canvas.height = loadedImg.naturalHeight;
      ctx.drawImage(loadedImg, 0, 0);
      
      try {
        // Get image data as binary
        const dataUrl = canvas.toDataURL('image/png');
        const binary = atob(dataUrl.split(',')[1]);
        
        // Check for PNG metadata chunks
        if (url.toLowerCase().endsWith('.png') || url.includes('.png')) {
          const pngSignature = binary.slice(0, 8);
          if (pngSignature.startsWith('\x89PNG\r\n\x1a\n')) {
            metadata.format = 'PNG';
            
            // Look for tEXt chunks
            let pos = 8;
            while (pos < binary.length) {
              const length = binary.charCodeAt(pos) * 16777216 + 
                           binary.charCodeAt(pos + 1) * 65536 + 
                           binary.charCodeAt(pos + 2) * 256 + 
                           binary.charCodeAt(pos + 3);
              
              const type = binary.slice(pos + 4, pos + 8);
              
              if (type === 'tEXt') {
                const textData = binary.slice(pos + 8, pos + 8 + length);
                const nullPos = textData.indexOf('\0');
                if (nullPos !== -1) {
                  const key = textData.slice(0, nullPos);
                  const value = textData.slice(nullPos + 1, length);
                  metadata[key] = value;
                }
              }
              
              pos += 8 + length + 4; // Skip CRC
            }
          }
        }
      } catch (err) {
        console.warn('[extractMetadataUsingBrowser] Error analyzing image data:', err);
      }
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
