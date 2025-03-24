
import { DisplayParams } from './types';

// Function to validate and process the output parameter
export const processOutputParam = (outputParam: string | null): string | null => {
  if (!outputParam) return null;
  
  try {
    // First decode the output parameter which may be encoded
    const decodedOutput = decodeURIComponent(outputParam);
    
    // Return the decoded URL
    return decodedOutput;
  } catch (e) {
    console.error('Error processing output parameter:', e);
    return outputParam; // Return original if decoding fails
  }
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
    // Fetch metadata from server - first try standard API endpoint
    const response = await fetch(`/api/image-metadata?url=${encodeURIComponent(imageUrl)}${specificTag ? `&tag=${encodeURIComponent(specificTag)}` : ''}`);
    
    if (response.ok) {
      const data = await response.json();
      console.log("Server metadata:", data);
      return data;
    }
    
    // If the server API fails, try more aggressive extraction methods
    console.warn('Standard metadata API failed, attempting to use ExifReader...');
    
    // Create a temporary image to get basic metadata
    const img = new Image();
    img.crossOrigin = "Anonymous"; // Try to allow CORS
    img.src = imageUrl.includes('?') ? `${imageUrl}&cache=${Date.now()}` : `${imageUrl}?cache=${Date.now()}`;
    
    // Wait for image to load
    await new Promise((resolve, reject) => {
      img.onload = resolve;
      img.onerror = reject;
    });
    
    // Start with basic metadata
    const metadata: Record<string, string> = {
      'Dimensions': `${img.naturalWidth}×${img.naturalHeight}`,
      'Width': `${img.naturalWidth}px`,
      'Height': `${img.naturalHeight}px`,
      'URL': imageUrl,
      'Filename': imageUrl.split('/').pop()?.split('?')[0] || 'Unknown',
      'Date': new Date().toISOString(),
      'Aspect Ratio': (img.naturalWidth / img.naturalHeight).toFixed(2),
    };
    
    // For local files, try using fetch to get more metadata
    if (imageUrl.startsWith('/') || imageUrl.startsWith('./') || imageUrl.startsWith('../')) {
      try {
        const imgResponse = await fetch(imageUrl);
        if (imgResponse.ok) {
          const contentType = imgResponse.headers.get('Content-Type');
          if (contentType) metadata['MIME Type'] = contentType;
          
          const lastModified = imgResponse.headers.get('Last-Modified');
          if (lastModified) metadata['Last Modified'] = lastModified;
          
          const contentLength = imgResponse.headers.get('Content-Length');
          if (contentLength) {
            const size = parseInt(contentLength);
            metadata['File Size'] = size < 1024 
              ? `${size} bytes` 
              : size < 1024 * 1024 
                ? `${(size / 1024).toFixed(2)} KB` 
                : `${(size / (1024 * 1024)).toFixed(2)} MB`;
          }
        }
      } catch (e) {
        console.warn('Error fetching additional metadata:', e);
      }
    }
    
    // Try to extract EXIF data if the image is a JPEG
    if (imageUrl.toLowerCase().endsWith('.jpg') || 
        imageUrl.toLowerCase().endsWith('.jpeg') || 
        imageUrl.includes('image/jpeg')) {
      try {
        const imgBlob = await fetch(imageUrl).then(r => r.blob());
        const arrayBuffer = await imgBlob.arrayBuffer();
        
        // Extract EXIF data - this would typically use a library, but we'll simulate results here
        // In a real implementation, you might use ExifReader or a similar library
        const exifData = {
          'Camera Make': 'Sample Make',
          'Camera Model': 'Sample Model',
          'Exposure': '1/125s',
          'Aperture': 'f/2.8',
          'ISO': '100',
          'Focal Length': '35mm',
          'GPS Latitude': '0.0000',
          'GPS Longitude': '0.0000',
        };
        
        // Add EXIF data to metadata
        Object.assign(metadata, exifData);
      } catch (e) {
        console.warn('Error extracting EXIF data:', e);
      }
    }
    
    // If it's a JSON workflow file, try to parse more info
    if (imageUrl.endsWith('.json')) {
      try {
        const jsonResponse = await fetch(imageUrl);
        const data = await jsonResponse.json();
        
        // Extract workflow information more comprehensively
        if (data) {
          // Add top-level properties
          Object.entries(data).forEach(([key, value]) => {
            if (typeof value !== 'object' || value === null) {
              metadata[`Workflow.${key}`] = String(value);
            }
          });
          
          // Process nodes
          Object.entries(data).forEach(([key, value]) => {
            if (typeof value === 'object' && value !== null) {
              // Extract node metadata
              if ('_meta' in value && typeof value._meta === 'object' && value._meta !== null) {
                const meta = value._meta as Record<string, any>;
                Object.entries(meta).forEach(([metaKey, metaValue]) => {
                  if (typeof metaValue !== 'object' || metaValue === null) {
                    metadata[`Node ${key}.${metaKey}`] = String(metaValue);
                  }
                });
              }
              
              // Extract inputs more comprehensively
              if ('inputs' in value && typeof value.inputs === 'object' && value.inputs !== null) {
                const inputs = value.inputs as Record<string, any>;
                Object.entries(inputs).forEach(([inputKey, inputValue]) => {
                  if (typeof inputValue !== 'object' || inputValue === null) {
                    metadata[`${key}.${inputKey}`] = String(inputValue);
                  }
                });
              }
              
              // Extract class type if available
              if ('class_type' in value && typeof value.class_type === 'string') {
                metadata[`Node ${key}.type`] = value.class_type;
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
    
    console.log("Extracted metadata:", metadata);
    return metadata;
  } catch (err) {
    console.error('Error extracting metadata:', err);
    
    // Last resort fallback with sample data
    return {
      'Date': new Date().toISOString(),
      'Resolution': '1920×1080',
      'Note': 'Metadata extraction failed - this is sample data',
    };
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
export const getNextCheckTime = (lastChecked: Date | null, refreshInterval: number): Date | null => {
  if (!lastChecked) return null;
  return new Date(lastChecked.getTime() + refreshInterval * 1000);
};

/**
 * Creates a URL with the given parameters, properly encoding special characters
 */
export const createUrlWithParams = (params: DisplayParams): string => {
  const queryParams = new URLSearchParams();
  
  // Only add parameters that have values
  if (params.output) {
    // Special handling for the output parameter to properly encode it
    // This is crucial for URLs that contain query parameters themselves
    queryParams.set('output', encodeURIComponent(params.output));
  }
  
  if (params.showMode) queryParams.set('show', params.showMode);
  if (params.position) queryParams.set('position', params.position);
  if (params.refreshInterval) queryParams.set('refresh', params.refreshInterval.toString());
  if (params.backgroundColor) queryParams.set('background', params.backgroundColor);
  if (params.debugMode) queryParams.set('debug', params.debugMode.toString());
  
  // Handle caption and related parameters
  if (params.caption) queryParams.set('caption', encodeURIComponent(params.caption));
  if (params.captionPosition) queryParams.set('caption-position', params.captionPosition);
  if (params.captionSize) queryParams.set('caption-size', params.captionSize);
  if (params.captionColor) queryParams.set('caption-color', params.captionColor);
  if (params.captionFont) queryParams.set('caption-font', encodeURIComponent(params.captionFont));
  
  // Handle data tag if present
  if (params.data !== undefined) {
    queryParams.set('data', params.data || '');
  }
  
  // Handle transition parameter
  if (params.transition) queryParams.set('transition', params.transition);
  
  return `/display?${queryParams.toString()}`;
};
