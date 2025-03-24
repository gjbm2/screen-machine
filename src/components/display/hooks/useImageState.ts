
import { useState, useRef } from 'react';
import { toast } from 'sonner';
import { extractImageMetadata } from '../utils';

export const useImageState = () => {
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [imageKey, setImageKey] = useState<number>(0);
  const [lastModified, setLastModified] = useState<string | null>(null);
  const [lastChecked, setLastChecked] = useState<Date | null>(null);
  const [imageChanged, setImageChanged] = useState<boolean>(false);
  const [metadata, setMetadata] = useState<Record<string, string>>({});
  const [isLoading, setIsLoading] = useState<boolean>(false);
  
  const imageRef = useRef<HTMLImageElement | null>(null);
  const lastModifiedRef = useRef<string | null>(null);
  const intervalRef = useRef<number | null>(null);
  const preloadImageRef = useRef<HTMLImageElement | null>(null);
  // Add a ref to track the last URL we extracted metadata from
  const lastMetadataUrlRef = useRef<string | null>(null);
  // Add a ref to track if we're currently loading metadata
  const isExtractingMetadataRef = useRef<boolean>(false);

  const checkImageModified = async (url: string) => {
    try {
      setLastChecked(new Date());
      
      // For URL with query parameters, we need to handle them specially
      const checkUrl = url.includes('?') ? url : url;
      
      // Try with HEAD request first
      try {
        const response = await fetch(checkUrl, { method: 'HEAD' });
        const lastModified = response.headers.get('last-modified');
        
        setLastModified(lastModified);
        
        if (lastModified && lastModified !== lastModifiedRef.current) {
          console.log('Image modified, updating from:', lastModifiedRef.current, 'to:', lastModified);
          
          if (lastModifiedRef.current !== null) {
            setImageChanged(true);
            toast.info("Image has been updated on the server");
            lastModifiedRef.current = lastModified;
            // Reset the last metadata URL to force metadata extraction for the changed image
            lastMetadataUrlRef.current = null;
            return true;
          }
          
          lastModifiedRef.current = lastModified;
        }
        return false;
      } catch (e) {
        // If HEAD request fails (e.g., CORS issues), fall back to checking if the image loads
        console.warn('HEAD request failed, falling back to image reload check:', e);
        
        // We consider the image changed if we previously had no lastModified date
        // This isn't perfect but helps with URLs that don't support HEAD requests
        if (lastModifiedRef.current === null) {
          setImageChanged(true);
          toast.info("Image may have been updated");
          lastModifiedRef.current = new Date().toISOString();
          lastMetadataUrlRef.current = null;
          return true;
        }
      }
    } catch (err) {
      console.error('Error checking image modification:', err);
      return false;
    }
    return false;
  };

  const handleManualCheck = async (url: string | null, debugMode: boolean) => {
    if (url) {
      setImageChanged(false);
      const hasChanged = await checkImageModified(url);
      if (!hasChanged) {
        toast.info("Image has not changed since last check");
      }
      return hasChanged;
    } else {
      toast.error("No image URL to check");
      return false;
    }
  };

  const extractMetadataFromImage = async (url: string, dataTag?: string) => {
    try {
      // Log that we're trying to extract metadata
      console.log('[useImageState] Starting metadata extraction for URL:', url);
      
      // If we're already extracting metadata, just return the current metadata
      if (isExtractingMetadataRef.current) {
        console.log('[useImageState] Already extracting metadata, using current metadata');
        return metadata;
      }
      
      // Reset the metadata state to ensure we don't show stale data
      setMetadata({});
      
      // We'll extract metadata regardless of whether we've seen this URL before
      // This ensures we always have fresh metadata
      console.log('[useImageState] Extracting metadata for URL:', url);
      isExtractingMetadataRef.current = true;
      
      try {
        // Get the metadata from the image
        const newMetadata = await extractImageMetadata(url);
        console.log('[useImageState] Extracted metadata:', newMetadata);
        
        // Update the state with the new metadata
        setMetadata(newMetadata);
        
        // Update the last metadata URL
        lastMetadataUrlRef.current = url;
        
        return newMetadata;
      } finally {
        isExtractingMetadataRef.current = false;
      }
    } catch (err) {
      console.error('Error extracting metadata:', err);
      toast.error("Failed to extract metadata");
      isExtractingMetadataRef.current = false;
      return {};
    }
  };

  return {
    imageUrl,
    setImageUrl,
    imageKey,
    setImageKey,
    lastModified,
    setLastModified,
    lastChecked,
    setLastChecked,
    imageChanged,
    setImageChanged,
    metadata,
    setMetadata,
    isLoading,
    setIsLoading,
    imageRef,
    lastModifiedRef,
    intervalRef,
    preloadImageRef,
    lastMetadataUrlRef,
    isExtractingMetadataRef,
    checkImageModified,
    handleManualCheck,
    extractMetadataFromImage
  };
};
