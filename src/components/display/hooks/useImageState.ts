import { useState, useRef, useEffect } from 'react';
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
  const lastMetadataUrlRef = useRef<string | null>(null);
  const isExtractingMetadataRef = useRef<boolean>(false);

  useEffect(() => {
    console.log('[useImageState] Metadata state changed:', metadata);
  }, [metadata]);

  const checkImageModified = async (url: string) => {
    try {
      setLastChecked(new Date());
      
      const checkUrl = url.includes('?') ? url : url;
      
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
            lastMetadataUrlRef.current = null;
            return true;
          }
          
          lastModifiedRef.current = lastModified;
        }
        return false;
      } catch (e) {
        console.warn('HEAD request failed, falling back to image reload check:', e);
        
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
      console.log('[useImageState] Starting metadata extraction for URL:', url);
      
      if (isExtractingMetadataRef.current) {
        console.log('[useImageState] Already extracting metadata, using current metadata');
        return metadata;
      }
      
      setMetadata({});
      
      console.log('[useImageState] Extracting metadata for URL:', url);
      isExtractingMetadataRef.current = true;
      
      try {
        const newMetadata = await extractImageMetadata(url);
        console.log('[useImageState] Extracted metadata:', newMetadata);
        
        if (Object.keys(newMetadata).length === 0) {
          console.warn('[useImageState] No metadata extracted from image');
        }
        
        setMetadata(newMetadata);
        lastMetadataUrlRef.current = url;
        
        return newMetadata;
      } catch (err) {
        console.error('[useImageState] Error in metadata extraction:', err);
        return {};
      } finally {
        isExtractingMetadataRef.current = false;
      }
    } catch (err) {
      console.error('[useImageState] Error extracting metadata:', err);
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
