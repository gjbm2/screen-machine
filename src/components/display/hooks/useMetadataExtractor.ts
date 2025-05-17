import { useState, useRef, useCallback } from 'react';
import { toast } from 'sonner';
import { getImageMetadata } from '../utils';

export const useMetadataExtractor = () => {
  const [metadata, setMetadata] = useState<Record<string, string>>({});
  const lastMetadataUrlRef = useRef<string | null>(null);
  const isExtractingMetadataRef = useRef<boolean>(false);
  const extractionTimeoutRef = useRef<number | null>(null);
  const lastExtractionTimeRef = useRef<number>(0);
  const MIN_EXTRACTION_INTERVAL = 60000; // 60 seconds

  const extractMetadataFromImage = useCallback(async (url: string, dataTag?: string): Promise<Record<string, string>> => {
    try {
      console.log('[useMetadataExtractor] Starting metadata extraction for URL:', url);
      
      // Add rate limiting to prevent excessive extractions
      const now = Date.now();
      if (now - lastExtractionTimeRef.current < MIN_EXTRACTION_INTERVAL && 
          url === lastMetadataUrlRef.current && Object.keys(metadata).length > 0) {
        console.log('[useMetadataExtractor] Skipping extraction, throttled:', 
          Math.ceil((MIN_EXTRACTION_INTERVAL - (now - lastExtractionTimeRef.current)) / 1000), 'seconds remaining');
        return metadata;
      }
      
      // If URL is the same and we already have metadata, return it without re-extracting
      if (url === lastMetadataUrlRef.current && Object.keys(metadata).length > 0) {
        console.log('[useMetadataExtractor] Using cached metadata for URL:', url);
        return metadata;
      }
      
      if (isExtractingMetadataRef.current) {
        console.log('[useMetadataExtractor] Already extracting metadata, preventing duplicate extraction');
        
        // Clear any existing timeout to prevent overlapping extractions
        if (extractionTimeoutRef.current !== null) {
          window.clearTimeout(extractionTimeoutRef.current);
          extractionTimeoutRef.current = null;
        }
        
        // Set a timeout to check again if metadata was successfully extracted
        return new Promise<Record<string, string>>((resolve) => {
          extractionTimeoutRef.current = window.setTimeout(() => {
            if (Object.keys(metadata).length > 0) {
              console.log('[useMetadataExtractor] Returning cached metadata after waiting');
              resolve(metadata);
            } else {
              console.log('[useMetadataExtractor] No metadata after waiting, returning empty object');
              resolve({});
            }
          }, 500);
        });
      }
      
      isExtractingMetadataRef.current = true;
      lastExtractionTimeRef.current = now;
      
      try {
        // Add a random query parameter and timestamp to bypass cache completely
        const cacheBustUrl = `${url}${url.includes('?') ? '&' : '?'}cacheBust=${Date.now()}_${Math.random()}`;
        console.log('[useMetadataExtractor] Using cache-busted URL:', cacheBustUrl);
        
        // Try a direct API call first - skip it for now as the API doesn't seem to be working
        const newMetadata = await getImageMetadata(cacheBustUrl);
        console.log('[useMetadataExtractor] Extracted metadata:', newMetadata);
        
        if (Object.keys(newMetadata).length > 0) {
          setMetadata(newMetadata);
          lastMetadataUrlRef.current = url;
          isExtractingMetadataRef.current = false;
          return newMetadata;
        }
        
        console.warn('[useMetadataExtractor] No metadata found, providing basic metadata');
        
        // If all extraction methods fail, return basic metadata
        const basicMetadata = {
          'filename': url.split('/').pop() || 'unknown',
          'loadedAt': new Date().toISOString(),
          'status': 'No embedded metadata found'
        };
        
        setMetadata(basicMetadata);
        lastMetadataUrlRef.current = url;
        isExtractingMetadataRef.current = false;
        return basicMetadata;
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
    } finally {
      // Clean up timeout if it exists
      if (extractionTimeoutRef.current !== null) {
        window.clearTimeout(extractionTimeoutRef.current);
        extractionTimeoutRef.current = null;
      }
    }
  }, [metadata]);

  return {
    metadata,
    setMetadata,
    lastMetadataUrlRef,
    isExtractingMetadataRef,
    extractMetadataFromImage
  };
};
