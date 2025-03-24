
import { useState, useRef } from 'react';
import { toast } from 'sonner';
import { extractImageMetadata } from '../utils';

export const useMetadataExtractor = () => {
  const [metadata, setMetadata] = useState<Record<string, string>>({});
  const lastMetadataUrlRef = useRef<string | null>(null);
  const isExtractingMetadataRef = useRef<boolean>(false);

  const extractMetadataFromImage = async (url: string, dataTag?: string) => {
    try {
      console.log('[useMetadataExtractor] Starting metadata extraction for URL:', url);
      console.log('[useMetadataExtractor] Current lastMetadataUrlRef:', lastMetadataUrlRef.current);
      
      if (isExtractingMetadataRef.current) {
        console.log('[useMetadataExtractor] Already extracting metadata, waiting...');
        // Wait for completion if already extracting
        await new Promise(resolve => setTimeout(resolve, 500));
        if (Object.keys(metadata).length > 0) {
          return metadata;
        }
      }
      
      isExtractingMetadataRef.current = true;
      
      try {
        // Add a random query parameter and timestamp to bypass cache completely
        const cacheBustUrl = `${url}${url.includes('?') ? '&' : '?'}cacheBust=${Date.now()}_${Math.random()}`;
        console.log('[useMetadataExtractor] Using cache-busted URL:', cacheBustUrl);
        
        // First attempt - using the utility function with cache busting
        console.log('[useMetadataExtractor] Attempting to extract metadata from:', cacheBustUrl);
        const newMetadata = await extractImageMetadata(cacheBustUrl);
        console.log('[useMetadataExtractor] Extracted metadata (1st attempt):', newMetadata);
        
        if (Object.keys(newMetadata).length > 0) {
          setMetadata(newMetadata);
          lastMetadataUrlRef.current = url;
          isExtractingMetadataRef.current = false;
          return newMetadata;
        }
        
        console.warn('[useMetadataExtractor] First attempt returned no metadata, trying second approach');
        
        // Second attempt - fetch the image directly as a blob and use the blob URL
        try {
          const response = await fetch(cacheBustUrl, { 
            cache: 'no-store',
            headers: {
              'Cache-Control': 'no-cache, no-store, must-revalidate',
              'Pragma': 'no-cache',
              'Expires': '0'
            }
          });
          
          if (!response.ok) {
            throw new Error(`Failed to fetch image: ${response.status}`);
          }
          
          const blob = await response.blob();
          const imgUrl = URL.createObjectURL(blob);
          
          console.log('[useMetadataExtractor] Created blob URL for second attempt:', imgUrl);
          const retryMetadata = await extractImageMetadata(imgUrl);
          
          // Clean up the blob URL
          URL.revokeObjectURL(imgUrl);
          
          if (Object.keys(retryMetadata).length > 0) {
            console.log('[useMetadataExtractor] Second attempt successful, metadata:', retryMetadata);
            setMetadata(retryMetadata);
            lastMetadataUrlRef.current = url;
            isExtractingMetadataRef.current = false;
            return retryMetadata;
          }
          
          console.warn('[useMetadataExtractor] Second attempt returned no metadata');
        } catch (blobErr) {
          console.error('[useMetadataExtractor] Error in blob approach:', blobErr);
        }
        
        // Third attempt - try directly with the API endpoint
        try {
          console.log('[useMetadataExtractor] Attempting direct API call to extract-metadata');
          
          const apiUrl = '/api/extract-metadata';
          const params = new URLSearchParams({ url: cacheBustUrl });
          const apiResponse = await fetch(`${apiUrl}?${params.toString()}`);
          
          if (!apiResponse.ok) {
            throw new Error(`API call failed: ${apiResponse.status}`);
          }
          
          const apiData = await apiResponse.json();
          console.log('[useMetadataExtractor] API response:', apiData);
          
          if (apiData && typeof apiData === 'object' && Object.keys(apiData).length > 0) {
            console.log('[useMetadataExtractor] API call successful, metadata:', apiData);
            setMetadata(apiData);
            lastMetadataUrlRef.current = url;
            isExtractingMetadataRef.current = false;
            return apiData;
          }
          
          console.warn('[useMetadataExtractor] API call returned no metadata');
          
          // If still no metadata, show an error toast
          toast.error("No metadata found in this image");
          
          // Return at least basic metadata
          const basicMetadata = {
            'filename': url.split('/').pop() || 'unknown',
            'loadedAt': new Date().toISOString(),
            'status': 'No embedded metadata found'
          };
          
          setMetadata(basicMetadata);
          lastMetadataUrlRef.current = url;
          isExtractingMetadataRef.current = false;
          return basicMetadata;
        } catch (apiErr) {
          console.error('[useMetadataExtractor] Error in API approach:', apiErr);
        }
        
        // All approaches failed
        const fallbackMetadata = {
          'filename': url.split('/').pop() || 'unknown',
          'loadedAt': new Date().toISOString(),
          'error': 'Failed to extract metadata after multiple attempts'
        };
        
        setMetadata(fallbackMetadata);
        lastMetadataUrlRef.current = url;
        isExtractingMetadataRef.current = false;
        return fallbackMetadata;
      } catch (err) {
        console.error('[useMetadataExtractor] Error in metadata extraction:', err);
        isExtractingMetadataRef.current = false;
        const errorMetadata = {
          'error': 'Extraction failed',
          'errorMessage': String(err)
        };
        setMetadata(errorMetadata);
        return errorMetadata;
      }
    } catch (err) {
      console.error('[useMetadataExtractor] Fatal error extracting metadata:', err);
      toast.error("Failed to extract metadata");
      isExtractingMetadataRef.current = false;
      return {};
    }
  };

  return {
    metadata,
    setMetadata,
    lastMetadataUrlRef,
    isExtractingMetadataRef,
    extractMetadataFromImage
  };
};
