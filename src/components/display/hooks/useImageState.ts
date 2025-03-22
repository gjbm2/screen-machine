
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

  const checkImageModified = async (url: string) => {
    try {
      setLastChecked(new Date());
      
      const response = await fetch(url, { method: 'HEAD' });
      const lastModified = response.headers.get('last-modified');
      
      setLastModified(lastModified);
      
      if (lastModified && lastModified !== lastModifiedRef.current) {
        console.log('Image modified, updating from:', lastModifiedRef.current, 'to:', lastModified);
        
        if (lastModifiedRef.current !== null) {
          setImageChanged(true);
          toast.info("Image has been updated on the server");
          lastModifiedRef.current = lastModified;
          return true;
        }
        
        lastModifiedRef.current = lastModified;
      }
      return false;
    } catch (err) {
      console.error('Error checking image modification:', err);
      return false;
    }
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
      const newMetadata = await extractImageMetadata(url, dataTag);
      setMetadata(newMetadata);
      return newMetadata;
    } catch (err) {
      console.error('Error extracting metadata:', err);
      toast.error("Failed to extract metadata");
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
    checkImageModified,
    handleManualCheck,
    extractMetadataFromImage
  };
};
