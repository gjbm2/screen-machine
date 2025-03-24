
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
    
    const img = new Image();
    
    const imageLoaded = new Promise<HTMLImageElement>((resolve, reject) => {
      img.onload = () => resolve(img);
      img.onerror = (err) => {
        console.error('[extractMetadataUsingBrowser] Image load error:', err);
        reject(new Error('Failed to load image'));
      };
      
      img.crossOrigin = 'anonymous';
      img.src = url;
    });
    
    const loadedImg = await imageLoaded;
    console.log('[extractMetadataUsingBrowser] Image loaded successfully');
    
    const metadata: Record<string, string> = {
      width: loadedImg.naturalWidth.toString(),
      height: loadedImg.naturalHeight.toString(),
      aspectRatio: (loadedImg.naturalWidth / loadedImg.naturalHeight).toFixed(2)
    };
    
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
        const binary = atob(dataUrl.split(',')[1]);
        
        // Check for PNG metadata chunks
        if (url.toLowerCase().endsWith('.png') || url.includes('.png')) {
          const pngSignature = binary.slice(0, 8);
          if (pngSignature.startsWith('\x89PNG\r\n\x1a\n')) {
            metadata.format = 'PNG';
            
            // Look for tEXt chunks
            let pos = 8;
            while (pos < binary.length) {
              const length = binary.charCodeAt(pos) * 16777216 + 
                           binary.charCodeAt(pos + 1) * 65536 + 
                           binary.charCodeAt(pos + 2) * 256 + 
                           binary.charCodeAt(pos + 3);
              
              const type = binary.slice(pos + 4, pos + 8);
              
              if (type === 'tEXt') {
                const textData = binary.slice(pos + 8, pos + 8 + length);
                const nullPos = textData.indexOf('\0');
                if (nullPos !== -1) {
                  const key = textData.slice(0, nullPos);
                  const value = textData.slice(nullPos + 1, length);
                  metadata[key] = value;
                }
              }
              
              pos += 8 + length + 4; // Skip CRC
            }
          }
        }
      } catch (err) {
        console.warn('[extractMetadataUsingBrowser] Error analyzing image data:', err);
      }
    }
    
    console.log('[extractMetadataUsingBrowser] Extracted metadata:', metadata);
    return metadata;
    
  } catch (err) {
    console.error('[extractMetadataUsingBrowser] Browser extraction failed:', err);
    return {};
  }
};
