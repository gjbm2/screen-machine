
import { DisplayParams } from '../types';

// Create a URL with display parameters
export const createUrlWithParams = (params: DisplayParams): string => {
  const queryParams = new URLSearchParams();
  
  // Enhanced logging for output parameter
  console.log('[paramUtils] Creating URL with output param:', params.output);
  console.log('[paramUtils] Debug mode:', params.debugMode);
  
  // Process the output parameter if it exists
  if (params.output) {
    // Ensure it's properly formatted for URL parameter use
    const processedOutput = processOutputParam(params.output);
    console.log('[paramUtils] Processed output for URL:', processedOutput);
    queryParams.set('output', processedOutput || '');
  }
  
  if (params.showMode !== 'fit') queryParams.set('show', params.showMode);
  if (params.position !== 'center') queryParams.set('position', params.position);
  if (params.refreshInterval !== 5) queryParams.set('refresh', params.refreshInterval.toString());
  if (params.backgroundColor !== '000000') queryParams.set('background', params.backgroundColor);
  
  // Only add debug parameter if it's true
  if (params.debugMode === true) queryParams.set('debug', 'true');
  
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
  console.log('[paramUtils] Final query string:', queryString);
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
  
  console.log('[processOutputParam] Processing output param:', output);
  
  // First, we need to ensure we handle URLs and relative paths consistently
  try {
    // Check if it's a valid URL
    new URL(output);
    console.log('[processOutputParam] Valid URL detected, using as-is:', output);
    return output;
  } catch (e) {
    // Not a valid URL, so handle as a path
    
    // Remove any duplicate slashes
    let processedPath = output.replace(/\/+/g, '/');
    
    // Ensure it has a leading slash
    if (!processedPath.startsWith('/')) {
      // If it starts with 'output/', add a leading slash
      if (processedPath.startsWith('output/')) {
        processedPath = `/${processedPath}`;
        console.log('[processOutputParam] Added leading slash to output path:', processedPath);
      } else {
        // Otherwise assume it's a relative path in the output directory
        processedPath = `/output/${processedPath}`;
        console.log('[processOutputParam] Added output directory prefix:', processedPath);
      }
    }
    
    console.log('[processOutputParam] Final processed path:', processedPath);
    return processedPath;
  }
};

// New helper function to ensure consistent path format for display
export const normalizePathForDisplay = (path: string): string => {
  if (!path) return '';
  
  // Handle URLs
  if (path.startsWith('http')) {
    return path;
  }
  
  // Remove any duplicate slashes
  let normalizedPath = path.replace(/\/+/g, '/');
  
  // Ensure it has a leading slash
  if (!normalizedPath.startsWith('/')) {
    normalizedPath = `/${normalizedPath}`;
  }
  
  console.log('[normalizePathForDisplay] Normalized path:', normalizedPath);
  return normalizedPath;
};
