
import { useCallback } from 'react';
import { toast } from 'sonner';

export const useImageErrorHandler = (imageUrl: string | null, mountedRef: React.MutableRefObject<boolean>) => {
  const handleImageError = useCallback(() => {
    if (!mountedRef.current) return; // Skip if unmounted
    
    console.error('[handleImageError] Failed to load image:', imageUrl);
    toast.error("Failed to load image");
    
    // Log more details about the failed image
    if (imageUrl) {
      console.error('[handleImageError] Image URL that failed to load:', imageUrl);
      fetch(imageUrl, { method: 'HEAD' })
        .then(response => {
          if (mountedRef.current) {
            console.log('[handleImageError] HTTP status for image:', response.status, response.statusText);
          }
        })
        .catch(err => {
          if (mountedRef.current) {
            console.error('[handleImageError] Network error when checking image:', err);
          }
        });
    }
  }, [imageUrl, mountedRef]);

  return {
    handleImageError
  };
};
