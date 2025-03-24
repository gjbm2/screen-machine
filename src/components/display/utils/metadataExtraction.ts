
// Specialized module for image metadata extraction

/**
 * Extracts metadata from an image file using the API endpoint
 */
export const extractMetadataUsingApi = async (url: string): Promise<Record<string, string>> => {
  try {
    console.log('[extractMetadataUsingApi] Attempting API extraction for:', url);
    
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
      console.error('[extractMetadataUsingApi] Expected JSON response but got', contentType);
      throw new Error(`Expected JSON response but got ${contentType}`);
    }

    // Parse the response
    const data = await response.json();
    
    if (!response.ok) {
      console.error('[extractMetadataUsingApi] API error response:', data);
      throw new Error(data.error || `Failed to extract metadata: ${response.status}`);
    }
    
    console.log('[extractMetadataUsingApi] API response data:', data);
    
    if (!data.success) {
      throw new Error(data.error || 'Failed to extract metadata');
    }
    
    // Convert all values to strings for consistency
    const stringMetadata: Record<string, string> = {};
    Object.entries(data.metadata).forEach(([key, value]) => {
      stringMetadata[key] = String(value);
    });
    
    console.log('[extractMetadataUsingApi] Extracted metadata:', stringMetadata);
    return stringMetadata;
  } catch (error) {
    console.error('[extractMetadataUsingApi] API extraction failed:', error);
    throw error; // Rethrow to be handled by the caller
  }
};

/**
 * Extracts metadata directly in the browser using canvas and image analysis
 */
export const extractMetadataUsingBrowser = async (url: string): Promise<Record<string, string>> => {
  try {
    console.log('[extractMetadataUsingBrowser] Starting browser extraction for:', url);
    
    // Add some basic metadata even if we fail to load the image
    const metadata: Record<string, string> = {
      'source': 'browser-extraction'
    };
    
    // Create a new image with crossOrigin set
    const img = new Image();
    img.crossOrigin = 'anonymous'; // Set this before the src to avoid CORS issues
    
    const imageLoaded = new Promise<HTMLImageElement>((resolve, reject) => {
      img.onload = () => resolve(img);
      img.onerror = (err) => {
        console.error('[extractMetadataUsingBrowser] Image load error:', err);
        reject(new Error('Failed to load image'));
      };
      
      // Set the source after setting up event handlers
      img.src = url;
    });
    
    try {
      const loadedImg = await imageLoaded;
      console.log('[extractMetadataUsingBrowser] Image loaded successfully');
      
      // Add basic image dimensions
      metadata.width = loadedImg.naturalWidth.toString();
      metadata.height = loadedImg.naturalHeight.toString();
      metadata.aspectRatio = (loadedImg.naturalWidth / loadedImg.naturalHeight).toFixed(2);
      
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
          
          // Add more metadata about the image type
          const fileExtension = url.split('.').pop()?.toLowerCase() || '';
          if (fileExtension) {
            metadata.fileType = fileExtension;
          }
          
          // Check for PNG metadata chunks if it's a PNG
          if (url.toLowerCase().endsWith('.png') || url.includes('.png')) {
            metadata.format = 'PNG';
            // PNG metadata extraction would go here
          } else if (url.toLowerCase().endsWith('.jpg') || url.toLowerCase().endsWith('.jpeg') || url.includes('.jpg') || url.includes('.jpeg')) {
            metadata.format = 'JPEG';
          }
        } catch (err) {
          console.warn('[extractMetadataUsingBrowser] Error analyzing image data:', err);
        }
      }
    } catch (err) {
      console.error('[extractMetadataUsingBrowser] Error loading image:', err);
      // Continue with the metadata we have so far
    }
    
    console.log('[extractMetadataUsingBrowser] Extracted metadata:', metadata);
    return metadata;
    
  } catch (err) {
    console.error('[extractMetadataUsingBrowser] Browser extraction failed:', err);
    // Return some minimal metadata to avoid undefined errors
    return { 
      'error': 'Failed to extract metadata',
      'message': err instanceof Error ? err.message : String(err)
    };
  }
};
