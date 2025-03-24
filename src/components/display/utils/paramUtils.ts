import { DisplayParams } from '../types';

// Create a URL with display parameters
export const createUrlWithParams = (params: DisplayParams): string => {
  const queryParams = new URLSearchParams();
  
  // Process the output parameter if it exists
  if (params.output) {
    // Check if the URL already contains encoded characters
    // If it does, don't encode it again to avoid double encoding
    if (params.output.includes('%') && (
        params.output.includes('http%3A') || 
        params.output.includes('https%3A') ||
        params.output.includes('%2F') ||
        params.output.includes('%3F') || // encoded ?
        params.output.includes('%26')    // encoded &
      )) {
      console.log('[createUrlWithParams] URL already contains encoded characters, using as is');
      queryParams.set('output', params.output);
    } else {
      // URL is not encoded yet, safe to encode
      console.log('[createUrlWithParams] Encoding URL parameter');
      queryParams.set('output', encodeURIComponent(params.output));
    }
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

// This function processes the output parameter to ensure it's properly formatted
export const processOutputParam = (output: string | null): string | null => {
  if (!output) return null;
  
  // If it's already a fully formed URL, return it as is
  if (output.startsWith('http://') || output.startsWith('https://')) {
    console.log('[processOutputParam] URL already starts with http(s), using as is:', output);
    return output;
  }
  
  // Check if this is an encoded URL that we need to decode first
  if (output.includes('http%3A') || output.includes('https%3A')) {
    try {
      const decodedUrl = fullyDecodeUrl(output);
      console.log('[processOutputParam] Decoded URL from encoded form:', decodedUrl);
      return decodedUrl;
    } catch (e) {
      console.error('[processOutputParam] Failed to decode URL:', e);
    }
  }
  
  // Now normalize the path for local files
  // First, strip any leading slashes for consistency
  let normalizedPath = output.replace(/^\/+/, '');
  
  // Ensure output folder is prefixed correctly
  if (!normalizedPath.startsWith('output/')) {
    // If it doesn't have output/ prefix but is a known output file, add the prefix
    if (!normalizedPath.includes('/')) {
      normalizedPath = `output/${normalizedPath}`;
    }
  }
  
  // Always ensure a leading slash for absolute path from server root
  normalizedPath = `/${normalizedPath}`;
  
  console.log('[processOutputParam] Processed local path:', normalizedPath);
  return normalizedPath;
};

// Recursively decode a URL until it can't be decoded further
export const fullyDecodeUrl = (url: string): string => {
  let decodedUrl = url;
  let prevUrl = '';
  let count = 0;
  const maxIterations = 5; // Prevent infinite loops
  
  while (decodedUrl !== prevUrl && count < maxIterations) {
    prevUrl = decodedUrl;
    try {
      decodedUrl = decodeURIComponent(prevUrl);
      count++;
      console.log(`[fullyDecodeUrl] Iteration ${count}:`, decodedUrl);
    } catch (e) {
      console.warn(`[fullyDecodeUrl] Decoding stopped at iteration ${count}:`, e);
      break;
    }
  }
  
  return decodedUrl;
};

// Decode URL parameter (improved to handle multiple levels of encoding)
export const decodeComplexOutputParam = (output: string | null): string | null => {
  if (!output) return null;
  
  console.log('[decodeComplexOutputParam] Starting with output:', output);
  
  // Use our recursive decoder
  try {
    const fullyDecodedUrl = fullyDecodeUrl(output);
    console.log('[decodeComplexOutputParam] Fully decoded URL:', fullyDecodedUrl);
    return fullyDecodedUrl;
  } catch (e) {
    console.error("[decodeComplexOutputParam] Error fully decoding output parameter:", e);
    
    // Fallback to single decoding if recursive decoding fails
    try {
      const singleDecodedUrl = decodeURIComponent(output);
      console.log('[decodeComplexOutputParam] Single decoded URL (fallback):', singleDecodedUrl);
      return singleDecodedUrl;
    } catch (e2) {
      console.error("[decodeComplexOutputParam] Error with single decoding:", e2);
      return output; // Return original as last resort
    }
  }
};

// Normalize a path for display (ensures consistent formatting)
export const normalizePathForDisplay = (path: string): string => {
  // If it's already a fully formed URL, return it as is
  if (path.startsWith('http://') || path.startsWith('https://')) {
    return path;
  }
  
  // Check if this is an encoded URL that we need to decode first
  if (path.includes('http%3A') || path.includes('https%3A')) {
    try {
      const decodedUrl = fullyDecodeUrl(path);
      console.log('[normalizePathForDisplay] Decoded URL from encoded form:', decodedUrl);
      return decodedUrl;
    } catch (e) {
      console.error('[normalizePathForDisplay] Failed to decode URL:', e);
    }
  }
  
  // Remove any extra slashes at the beginning
  let normalizedPath = path.replace(/^\/+/, '');
  
  // Always ensure output/ directory is referenced correctly
  if (!normalizedPath.startsWith('output/') && !normalizedPath.includes('/')) {
    normalizedPath = `output/${normalizedPath}`;
  }
  
  // Always ensure leading slash for absolute path
  normalizedPath = `/${normalizedPath}`;
  
  return normalizedPath;
};
