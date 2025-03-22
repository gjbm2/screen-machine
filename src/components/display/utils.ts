
import { DisplayParams } from './types';

// Function to validate and process the output parameter
export const processOutputParam = (outputParam: string | null): string | null => {
  if (!outputParam) return null;
  
  // Check if it's an absolute URL
  if (outputParam.startsWith('http://') || outputParam.startsWith('https://')) {
    // Escape special characters for URL parameters
    return outputParam;
  }
  
  // Otherwise, treat as relative path from /output/
  return `/output/${encodeURIComponent(outputParam)}`;
};

// Function to fetch available output files
export const fetchOutputFiles = async (): Promise<string[]> => {
  try {
    // Real API endpoint to fetch actual files from the server
    const response = await fetch('/api/output-files');
    
    if (!response.ok) {
      throw new Error('Failed to fetch output files');
    }
    
    return await response.json();
  } catch (err) {
    console.error('Error fetching output files:', err);
    
    // Fallback to listing files directly from /output directory
    try {
      const listResponse = await fetch('/output/');
      const html = await listResponse.text();
      
      // Parse directory listing HTML to extract file names
      const parser = new DOMParser();
      const doc = parser.parseFromString(html, 'text/html');
      const fileLinks = Array.from(doc.querySelectorAll('a'));
      
      // Filter out parent directory links and extract filenames
      return fileLinks
        .map(link => link.getAttribute('href'))
        .filter((href): href is string => 
          href !== null && 
          href !== '../' && 
          !href.startsWith('?') && 
          !href.startsWith('/'))
        .map(href => decodeURIComponent(href));
    } catch (listErr) {
      console.error('Error fetching directory listing:', listErr);
      return [];
    }
  }
};

// Extract image metadata from image file - enhanced version that loads all available metadata
export const extractImageMetadata = async (imageUrl: string, specificTag?: string): Promise<Record<string, string>> => {
  try {
    // Fetch metadata from server
    const response = await fetch(`/api/image-metadata?url=${encodeURIComponent(imageUrl)}${specificTag ? `&tag=${encodeURIComponent(specificTag)}` : ''}`);
    
    if (!response.ok) {
      throw new Error('Failed to fetch image metadata');
    }
    
    return await response.json();
  } catch (err) {
    console.error('Error fetching image metadata:', err);
    
    // For demo or fallback purposes, try to extract basic metadata from the image
    try {
      // Create a temporary image to get basic metadata
      const img = new Image();
      img.src = imageUrl;
      
      // Wait for image to load
      await new Promise((resolve, reject) => {
        img.onload = resolve;
        img.onerror = reject;
      });
      
      const metadata: Record<string, string> = {
        'Width': `${img.naturalWidth}px`,
        'Height': `${img.naturalHeight}px`,
        'URL': imageUrl,
        'Filename': imageUrl.split('/').pop() || 'Unknown',
        'Date': new Date().toISOString(),
      };
      
      // If it's a JSON workflow file, try to parse more info
      if (imageUrl.endsWith('.json')) {
        try {
          const jsonResponse = await fetch(imageUrl);
          const data = await jsonResponse.json();
          
          // Extract some info from the workflow if available
          if (data) {
            Object.entries(data).forEach(([key, value]) => {
              if (typeof value === 'object' && value !== null) {
                if ('_meta' in value && typeof value._meta === 'object' && value._meta !== null) {
                  if ('title' in value._meta) {
                    metadata[`Node ${key}`] = value._meta.title as string;
                  }
                }
                if ('inputs' in value && typeof value.inputs === 'object' && value.inputs !== null) {
                  Object.entries(value.inputs).forEach(([inputKey, inputValue]) => {
                    if (typeof inputValue === 'string' || typeof inputValue === 'number') {
                      metadata[`${key}.${inputKey}`] = String(inputValue);
                    }
                  });
                }
              }
            });
          }
        } catch (e) {
          console.error('Error parsing JSON workflow:', e);
        }
      }
      
      // If specific tag was requested, filter to just that tag
      if (specificTag && specificTag !== '') {
        return specificTag in metadata 
          ? { [specificTag]: metadata[specificTag] } 
          : {};
      }
      
      return metadata;
    } catch (e) {
      console.error('Error extracting basic metadata:', e);
      
      // Last resort fallback
      if (specificTag) {
        return { [specificTag]: 'Sample metadata value for ' + specificTag };
      }
      
      return {
        'Date': new Date().toISOString(),
        'Camera': 'Sample Camera',
        'Software': 'Sample Software',
        'Resolution': '1920x1080',
      };
    }
  }
};

// Process caption with metadata substitutions
export const processCaptionWithMetadata = (caption: string | null, metadata: Record<string, string>): string | null => {
  if (!caption) return null;
  
  // Handle special {all} case
  if (caption.trim() === '{all}') {
    return Object.entries(metadata)
      .map(([key, value]) => `${key}: ${value}`)
      .join('\n');
  }
  
  // Replace all metadata placeholders {key} with their values
  return caption.replace(/\{([^}]+)\}/g, (match, key) => {
    return metadata[key] !== undefined ? metadata[key] : match;
  });
};

// Format date for display
export const formatDateTime = (date: Date | null) => {
  if (!date) return 'N/A';
  return date.toLocaleTimeString();
};

// Calculate next check time
export const getNextCheckTime = (lastChecked: Date | null, refreshInterval: number) => {
  if (!lastChecked) return 'N/A';
  const nextCheck = new Date(lastChecked.getTime() + refreshInterval * 1000);
  return formatDateTime(nextCheck);
};

// Create URL with encoded parameters
export const createUrlWithParams = (params: DisplayParams): string => {
  const queryParams = new URLSearchParams();
  
  // Only add parameters that are set
  if (params.output) queryParams.set('output', params.output);
  queryParams.set('show', params.showMode);
  if (params.position) queryParams.set('position', params.position);
  queryParams.set('refresh', params.refreshInterval.toString());
  queryParams.set('background', params.backgroundColor);
  if (params.debugMode) queryParams.set('debug', 'true');
  
  // Add optional parameters
  if (params.data !== undefined) {
    if (params.data) {
      queryParams.set('data', params.data);
    } else {
      queryParams.set('data', '');
    }
  }
  
  if (params.caption) queryParams.set('caption', params.caption);
  if (params.captionPosition) queryParams.set('caption-position', params.captionPosition);
  if (params.captionSize) queryParams.set('caption-size', params.captionSize);
  if (params.captionColor) queryParams.set('caption-color', params.captionColor);
  if (params.captionFont) queryParams.set('caption-font', params.captionFont);
  if (params.transition) queryParams.set('transition', params.transition);
  
  return `/display?${queryParams.toString()}`;
};
