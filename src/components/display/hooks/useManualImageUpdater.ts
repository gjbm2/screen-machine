
import { useCallback } from 'react';
import { toast } from 'sonner';
import { extractImageMetadata } from '../utils';
import { DisplayParams } from '../types';

export const useManualImageUpdater = (
  mountedRef: React.MutableRefObject<boolean>
) => {
  const handleManualUpdate = useCallback(async (
    imageUrl: string | null,
    originalHandleManualCheck: () => Promise<boolean>,
    params: DisplayParams
  ): Promise<boolean> => {
    if (!mountedRef.current) return false; // Skip if component unmounted
    
    console.log('[useManualImageUpdater] Manual check initiated');
    
    if (imageUrl) {
      // Call the base image check function
      const result = await originalHandleManualCheck();
      
      if (!mountedRef.current) return false; // Skip if component unmounted during async operation
      
      // Force metadata refresh regardless of whether the image changed
      try {
        console.log('[useManualImageUpdater] Forcing metadata refresh on manual check');
        const extractedMetadata = await extractImageMetadata(imageUrl);
        
        if (!mountedRef.current) return false; // Skip if component unmounted during async operation
        
        console.log('[useManualImageUpdater] Manually extracted metadata:', extractedMetadata);
        
        if (Object.keys(extractedMetadata).length === 0) {
          toast.warning("No metadata found in this image");
        }
      } catch (err) {
        if (mountedRef.current) {
          console.error('[useManualImageUpdater] Error during manual metadata extraction:', err);
          toast.error("Failed to extract metadata");
        }
      }
      
      return result;
    }
    
    return false;
  }, [mountedRef]);
  
  return {
    handleManualUpdate
  };
};
