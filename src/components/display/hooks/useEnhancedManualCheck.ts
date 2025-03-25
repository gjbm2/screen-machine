
import { MutableRefObject, useCallback } from 'react';
import { DisplayParams } from '../types';
import { toast } from 'sonner';

export const useEnhancedManualCheck = (
  mountedRef: MutableRefObject<boolean>,
  imageUrl: string | null,
  imagePollerHandleManualCheck: ((imageUrl: string | null, originalHandleManualCheck: () => Promise<boolean>, params: DisplayParams) => Promise<boolean>) | null,
  originalHandleManualCheck: () => Promise<boolean>,
  params: DisplayParams,
  extractMetadataFromImage: (url: string) => Promise<Record<string, string>>
) => {
  const handleManualCheck = useCallback(async () => {
    console.log('[useEnhancedManualCheck] Manual check initiated');
    if (!mountedRef.current) return;
    
    if (!imageUrl) {
      console.log('[useEnhancedManualCheck] No image URL to check');
      toast.error('No image to check');
      return;
    }
    
    try {
      console.log('[useEnhancedManualCheck] Checking image:', imageUrl);
      
      let result = false;
      
      if (imagePollerHandleManualCheck) {
        console.log('[useEnhancedManualCheck] Using image poller for check');
        result = await imagePollerHandleManualCheck(imageUrl, originalHandleManualCheck, params);
      } else {
        console.log('[useEnhancedManualCheck] Using original handle for check');
        result = await originalHandleManualCheck();
      }
      
      console.log('[useEnhancedManualCheck] Check result:', result);
      
      if (result) {
        toast.success('Image updated');
        
        // Extract metadata when image changes
        if (mountedRef.current) {
          try {
            console.log('[useEnhancedManualCheck] Extracting metadata after update');
            await extractMetadataFromImage(imageUrl);
          } catch (err) {
            console.error('[useEnhancedManualCheck] Error extracting metadata:', err);
          }
        }
      } else {
        toast.info('No changes detected');
      }
    } catch (error) {
      console.error('[useEnhancedManualCheck] Error during manual check:', error);
      if (mountedRef.current) {
        toast.error('Error checking for updates');
      }
    }
  }, [imageUrl, imagePollerHandleManualCheck, originalHandleManualCheck, params, extractMetadataFromImage, mountedRef]);
  
  return { handleManualCheck };
};
