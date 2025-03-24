
import { DisplayParams } from '../types';

// Create a URL with display parameters
export const createUrlWithParams = (params: DisplayParams): string => {
  const queryParams = new URLSearchParams();
  
  if (params.output) queryParams.set('output', params.output);
  if (params.showMode !== 'fit') queryParams.set('show', params.showMode);
  if (params.position !== 'center') queryParams.set('position', params.position);
  if (params.refreshInterval !== 5) queryParams.set('refresh', params.refreshInterval.toString());
  if (params.backgroundColor !== '000000') queryParams.set('background', params.backgroundColor);
  if (params.debugMode) queryParams.set('debug', 'true');
  
  if (params.data !== undefined) {
    const dataStr = typeof params.data === 'string' 
      ? params.data 
      : JSON.stringify(params.data);
    queryParams.set('data', dataStr);
  }
  
  if (params.caption) queryParams.set('caption', params.caption);
  if (params.captionPosition !== 'bottom-center') queryParams.set('caption-position', params.captionPosition);
  if (params.captionSize !== '16px') queryParams.set('caption-size', params.captionSize);
  if (params.captionColor !== 'ffffff') queryParams.set('caption-color', params.captionColor);
  if (params.captionFont !== 'Arial, sans-serif') queryParams.set('caption-font', params.captionFont);
  
  // Handle the backgroundColor with or without # prefix
  if (params.captionBgColor !== '#000000') {
    const color = params.captionBgColor.startsWith('#') 
      ? params.captionBgColor.substring(1) 
      : params.captionBgColor;
    queryParams.set('caption-bg-color', color);
  }
  
  if (params.captionBgOpacity !== 0.7) queryParams.set('caption-bg-opacity', params.captionBgOpacity.toString());
  if (params.transition !== 'cut') queryParams.set('transition', params.transition);
  
  const queryString = queryParams.toString();
  return queryString.length > 0 ? `?${queryString}` : '';
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
