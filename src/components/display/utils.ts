
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
    // Try to fetch metadata from API
    const response = await fetch(`/api/metadata?image=${encodeURIComponent(imageUrl)}`);
    if (response.ok) {
      const data = await response.json();
      return data.metadata || {};
    }
    
    // If that fails, return basic metadata
    console.warn('Could not fetch metadata from API, using mock data');
    
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
    console.error('Error fetching image metadata:', e);
    return {};
  }
};

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
