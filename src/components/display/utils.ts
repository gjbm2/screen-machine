
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

// Extract image metadata
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
    
    // For demo purposes or if API is unavailable
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
  
  return `/display?${queryParams.toString()}`;
};
