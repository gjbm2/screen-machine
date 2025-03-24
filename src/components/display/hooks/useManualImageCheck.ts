
import { useCallback } from 'react';
import { toast } from 'sonner';

export const useManualImageCheck = (
  imageUrl: string | null,
  setImageChanged: (changed: boolean) => void,
  checkImageModified: (url: string) => Promise<boolean>,
  extractMetadataFromImage: (url: string, dataTag?: string) => Promise<Record<string, string>>,
  lastMetadataUrlRef: React.MutableRefObject<string | null>
) => {
  const handleManualCheck = useCallback(async () => {
    if (imageUrl) {
      console.log('[handleManualCheck] Manual check for URL:', imageUrl);
      setImageChanged(false);
      
      // Force metadata re-extraction on manual check by clearing the last URL
      lastMetadataUrlRef.current = null;
      
      const hasChanged = await checkImageModified(imageUrl);
      
      // Extract metadata regardless of whether the image has changed
      try {
        const newMetadata = await extractMetadataFromImage(imageUrl);
        console.log('[handleManualCheck] Extracted metadata after check:', newMetadata);
      } catch (err) {
        console.error('[handleManualCheck] Error extracting metadata:', err);
      }
      
      if (!hasChanged) {
        toast.info("Image has not changed since last check");
      }
      
      return hasChanged;
    } else {
      toast.error("No image URL to check");
      return false;
    }
  }, [imageUrl, setImageChanged, checkImageModified, extractMetadataFromImage, lastMetadataUrlRef]);

  return {
    handleManualCheck
  };
};
