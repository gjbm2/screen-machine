
import { useCallback } from 'react';
import { DisplayParams } from '../types';

export const useEnhancedManualCheck = (
  mountedRef: React.MutableRefObject<boolean>,
  imageUrl: string | null,
  imagePollerHandleManualCheck: (imageUrl: string | null, originalHandleManualCheck: () => Promise<boolean>, params: DisplayParams) => Promise<boolean>,
  originalHandleManualCheck: () => Promise<boolean>,
  params: DisplayParams,
  extractMetadataFromImage: (url: string) => Promise<Record<string, string>>
) => {
  const handleManualCheck = useCallback(async () => {
    if (!mountedRef.current) return false; // Skip if unmounted
    
    const result = await imagePollerHandleManualCheck(imageUrl, originalHandleManualCheck, params);
    
    // If there's a new image loaded, extract metadata
    if (result && imageUrl && mountedRef.current) {
      console.log('[useEnhancedManualCheck] New image loaded after manual check, extracting metadata');
      await extractMetadataFromImage(imageUrl);
    }
    
    return result;
  }, [mountedRef, imageUrl, imagePollerHandleManualCheck, originalHandleManualCheck, params, extractMetadataFromImage]);

  return {
    handleManualCheck
  };
};
