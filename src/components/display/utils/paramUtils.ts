import { DisplayParams } from '../types';

// Create a URL with display parameters
export const createUrlWithParams = (params: DisplayParams): string => {
  const queryParams = new URLSearchParams();
  
  // Enhanced logging for output parameter
  console.log('[paramUtils] Creating URL with output param:', params.output);
  console.log('[paramUtils] Debug mode:', params.debugMode);
  
  // Process the output parameter if it exists
  if (params.output) {
    // For complex URLs with query parameters, we need to ensure they're properly encoded
    // to avoid breaking the URL structure
    if (params.output.includes('?') && (params.output.startsWith('http://') || params.output.startsWith('https://'))) {
      // For URLs with query params, we need to ensure they're properly encoded
      const encodedOutput = encodeURIComponent(params.output);
      console.log('[paramUtils] Encoded complex URL for param use:', encodedOutput);
      queryParams.set('output', encodedOutput);
    } else {
      // For simpler URLs or paths, process normally
      const processedOutput = processOutputParam(params.output);
      console.log('[paramUtils] Processed output for URL:', processedOutput);
      queryParams.set('output', processedOutput || '');
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

// This function processes the output parameter to ensure it's properly formatted
export const processOutputParam = (output: string | null): string | null => {
  if (!output) return null;
  
  console.log("[processOutputParam] Processing output path:", output);
  
  // If it's already a fully formed URL, return it as is
  if (output.startsWith('http://') || output.startsWith('https://')) {
    console.log("[processOutputParam] Already a full URL, returning as is");
    return output;
  }
  
  // Now normalize the path for local files
  // First, strip any leading slashes for consistency
  let normalizedPath = output.replace(/^\/+/, '');
  
  // Ensure output folder is prefixed correctly
  if (!normalizedPath.startsWith('output/')) {
    // If it doesn't have output/ prefix but is a known output file, add the prefix
    if (!normalizedPath.includes('/')) {
      console.log("[processOutputParam] Adding output/ prefix to filename");
      normalizedPath = `output/${normalizedPath}`;
    }
  }
  
  // Always ensure a leading slash for absolute path from server root
  normalizedPath = `/${normalizedPath}`;
  
  console.log("[processOutputParam] Normalized path:", normalizedPath);
  return normalizedPath;
};

// Decode complex output parameters (handles encoded URLs and special chars)
export const decodeComplexOutputParam = (output: string | null): string | null => {
  if (!output) return null;
  
  try {
    // Try to decode the parameter in case it was URL encoded
    const decoded = decodeURIComponent(output);
    console.log("[decodeComplexOutputParam] Decoded output param:", decoded);
    return decoded;
  } catch (e) {
    console.error("[decodeComplexOutputParam] Error decoding output parameter:", e);
    return output; // Return the original if decoding fails
  }
};

// Normalize a path for display (ensures consistent formatting)
export const normalizePathForDisplay = (path: string): string => {
  console.log("[normalizePathForDisplay] Original path:", path);
  
  // If it's already a fully formed URL, return it as is
  if (path.startsWith('http://') || path.startsWith('https://')) {
    console.log("[normalizePathForDisplay] Already a full URL");
    return path;
  }
  
  // Remove any extra slashes at the beginning
  let normalizedPath = path.replace(/^\/+/, '');
  
  // Always ensure output/ directory is referenced correctly
  if (!normalizedPath.startsWith('output/') && !normalizedPath.includes('/')) {
    normalizedPath = `output/${normalizedPath}`;
    console.log("[normalizePathForDisplay] Added output/ prefix");
  }
  
  // Always ensure leading slash for absolute path
  normalizedPath = `/${normalizedPath}`;
  
  console.log("[normalizePathForDisplay] Final normalized path:", normalizedPath);
  return normalizedPath;
};
