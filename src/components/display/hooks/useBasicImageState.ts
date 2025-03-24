
import { useState, useRef, useEffect } from 'react';

export const useBasicImageState = () => {
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [imageKey, setImageKey] = useState<number>(0);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  
  const imageRef = useRef<HTMLImageElement | null>(null);
  const intervalRef = useRef<number | null>(null);
  const preloadImageRef = useRef<HTMLImageElement | null>(null);

  // Store current image URL in localStorage for potential refreshes
  useEffect(() => {
    if (imageUrl) {
      localStorage.setItem('currentImageUrl', imageUrl);
      console.log('[useBasicImageState] Stored image URL in localStorage:', imageUrl);
    }
  }, [imageUrl]);

  return {
    imageUrl,
    setImageUrl,
    imageKey,
    setImageKey,
    isLoading,
    setIsLoading,
    imageRef,
    intervalRef,
    preloadImageRef
  };
};
