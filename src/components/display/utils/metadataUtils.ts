
// Extract metadata from an image file
export const extractImageMetadata = async (url: string): Promise<Record<string, string>> => {
  try {
    console.log('Extracting metadata for image:', url);
    
    // Try with fetch API directly to the endpoint
    try {
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
        console.error('Expected JSON response but got', contentType);
        throw new Error(`Expected JSON response but got ${contentType}`);
      }

      // Parse the response
      const data = await response.json();
      
      if (!response.ok) {
        console.error('API error response:', data);
        throw new Error(data.error || `Failed to extract metadata: ${response.status}`);
      }
      
      console.log('API response data:', data);
      
      if (!data.success) {
        throw new Error(data.error || 'Failed to extract metadata');
      }
      
      // Convert all values to strings for consistency
      const stringMetadata: Record<string, string> = {};
      Object.entries(data.metadata).forEach(([key, value]) => {
        stringMetadata[key] = String(value);
      });
      
      console.log('Extracted metadata:', stringMetadata);
      return stringMetadata;
    } catch (apiError) {
      console.error('Error with API endpoint:', apiError);
      
      // Fallback to browser-based extraction if the API fails
      console.log('Falling back to browser-based extraction');
      try {
        const browserMetadata = await extractMetadataUsingBrowser(url);
        if (Object.keys(browserMetadata).length > 0) {
          return browserMetadata;
        }
      } catch (browserError) {
        console.error('Browser extraction also failed:', browserError);
      }
      
      // Return an empty record if all methods fail
      return {
        'error': 'Failed to extract metadata',
        'message': apiError instanceof Error ? apiError.message : String(apiError)
      };
    }
  } catch (error) {
    console.error('Error extracting metadata:', error);
    return {
      'error': 'Failed to extract metadata',
      'message': error instanceof Error ? error.message : String(error)
    };
  }
};

// Enhanced browser-based metadata extraction
async function extractMetadataUsingBrowser(url: string): Promise<Record<string, string>> {
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
}

/**
 * Process caption with metadata substitutions
 */
export const processCaptionWithMetadata = (caption: string | null, metadata: Record<string, string>): string | null => {
  if (!caption) return null;
  
  let processedCaption = caption;
  
  Object.entries(metadata).forEach(([key, value]) => {
    const regex = new RegExp(`\\{${key}\\}`, 'g');
    processedCaption = processedCaption?.replace(regex, value) || '';
  });
  
  return processedCaption;
};
