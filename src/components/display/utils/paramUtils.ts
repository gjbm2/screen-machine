import { DisplayParams } from '../types';

export const createUrlWithParams = (params: DisplayParams): string => {
  const queryParams = new URLSearchParams();
  
  // Add each parameter to the query string
  if (params.output) {
    console.log('[createUrlWithParams] Adding output param:', params.output);
    // For external URLs, use them directly without additional encoding
    if (params.output.startsWith('http://') || params.output.startsWith('https://')) {
      queryParams.set('output', params.output);
      console.log('[createUrlWithParams] Added external URL directly:', params.output);
    } else {
      // For local paths, use single encoding
      queryParams.set('output', encodeURIComponent(params.output));
      console.log('[createUrlWithParams] Encoded local path:', encodeURIComponent(params.output));
    }
  }
  
  if (params.showMode) {
    queryParams.set('showMode', params.showMode);
  }
  
  if (params.position) {
    queryParams.set('position', params.position);
  }
  
  if (params.refreshInterval) {
    queryParams.set('refreshInterval', params.refreshInterval.toString());
  }
  
  if (params.backgroundColor) {
    queryParams.set('backgroundColor', params.backgroundColor);
  }
  
  if (params.caption) {
    queryParams.set('caption', encodeURIComponent(params.caption));
  }
  
  if (params.captionPosition) {
    queryParams.set('captionPosition', params.captionPosition);
  }
  
  if (params.captionSize) {
    queryParams.set('captionSize', params.captionSize);
  }
  
  if (params.captionColor) {
    queryParams.set('captionColor', params.captionColor);
  }
  
  if (params.captionFont) {
    queryParams.set('captionFont', params.captionFont);
  }
  
  if (params.captionBgColor) {
    queryParams.set('captionBgColor', params.captionBgColor);
  }
  
  if (params.captionBgOpacity !== undefined) {
    queryParams.set('captionBgOpacity', params.captionBgOpacity.toString());
  }
  
  if (params.transition) {
    queryParams.set('transition', params.transition);
  }
  
  // Only add debug=true if debugMode is true, otherwise omit entirely
  if (params.debugMode === true) {
    queryParams.set('debug', 'true');
  }
  
  const result = `?${queryParams.toString()}`;
  console.log('[createUrlWithParams] Final URL params:', result);
  return result;
};

// Improved recursive function to fully decode a URL
export const fullyDecodeUrl = (url: string): string => {
  if (!url) return url;
  
  console.log('[fullyDecodeUrl] Starting with URL:', url);
  
  let decodedUrl = url;
  let previousUrl = '';
  let iterations = 0;
  
  // Keep decoding until there's no more change or we hit max iterations
  while (decodedUrl !== previousUrl && iterations < 10) {
    previousUrl = decodedUrl;
    try {
      decodedUrl = decodeURIComponent(previousUrl);
      iterations++;
      console.log(`[fullyDecodeUrl] Iteration ${iterations}, decoded to:`, decodedUrl);
      
      // If we have a valid URL now, we can stop decoding
      if (decodedUrl.startsWith('http://') || decodedUrl.startsWith('https://')) {
        console.log('[fullyDecodeUrl] Found valid URL, stopping decode loop');
        break;
      }
      
      // Special handling for when we might have nested encodings
      if (decodedUrl.includes('%') && iterations < 9) {
        // Continue decoding in next iteration
        continue;
      }
    } catch (e) {
      // If we encounter an error, return the last valid decoded URL
      console.warn('[fullyDecodeUrl] Error decoding URL, returning last valid decode:', previousUrl, e);
      return previousUrl;
    }
  }
  
  console.log('[fullyDecodeUrl] Final decoded URL:', decodedUrl);
  return decodedUrl;
};

export const processOutputParam = (output: string): string => {
  if (!output) return output;
  
  console.log('[processOutputParam] Processing output:', output);
  
  // Check if this is already an http URL
  if (output.startsWith('http://') || output.startsWith('https://')) {
    console.log('[processOutputParam] Already a valid URL, using as-is:', output);
    return output;
  }
  
  // For encoded URLs, decode them fully
  if (output.includes('%') && (output.includes('http%3A') || output.includes('https%3A'))) {
    console.log('[processOutputParam] Processing encoded URL:', output);
    // Try to fully decode the URL
    try {
      const fullyDecoded = fullyDecodeUrl(output);
      
      // Check if we now have a properly formatted URL
      if (fullyDecoded.startsWith('http://') || fullyDecoded.startsWith('https://')) {
        console.log('[processOutputParam] Successfully decoded external URL:', fullyDecoded);
        return fullyDecoded;
      }
    } catch (e) {
      console.error('[processOutputParam] Error decoding URL:', e);
    }
  }
  
  // For local file paths, normalize them
  const normalized = normalizePathForDisplay(output);
  console.log('[processOutputParam] Normalized path:', normalized);
  return normalized;
};

export const decodeComplexOutputParam = (output: string | null): string | null => {
  if (!output) {
    console.log('[decodeComplexOutputParam] Output param is null or empty');
    return null;
  }
  
  console.log('[decodeComplexOutputParam] Starting to decode:', output);
  
  // First check if it's already a valid URL
  if (output.startsWith('http://') || output.startsWith('https://')) {
    console.log('[decodeComplexOutputParam] Already a valid URL, using as-is:', output);
    return output;
  }
  
  // For encoded URLs, decode them
  try {
    const decoded = fullyDecodeUrl(output);
    console.log('[decodeComplexOutputParam] Successfully decoded to:', decoded);
    return decoded;
  } catch (e) {
    console.error("[decodeComplexOutputParam] Error decoding output parameter:", e);
    return output; // Return the original if decoding fails
  }
};

export const normalizePathForDisplay = (path: string): string => {
  if (!path) return path;
  
  console.log('[normalizePathForDisplay] Normalizing path:', path);
  
  // Make sure the path starts with a forward slash
  let normalizedPath = path.startsWith('/') ? path : `/${path}`;
  
  // Handle paths that might have the 'output' folder path repeated
  if (normalizedPath.includes('/output/output/')) {
    normalizedPath = normalizedPath.replace('/output/output/', '/output/');
    console.log('[normalizePathForDisplay] Fixed repeated output folder:', normalizedPath);
  }
  
  // Ensure the output folder is in the path
  if (!normalizedPath.includes('/output/')) {
    normalizedPath = `/output${normalizedPath}`;
    console.log('[normalizePathForDisplay] Added output folder prefix:', normalizedPath);
  }
  
  console.log('[normalizePathForDisplay] Final normalized path:', normalizedPath);
  return normalizedPath;
};
