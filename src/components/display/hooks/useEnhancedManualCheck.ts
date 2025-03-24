
import { useCallback } from 'react';
import { toast } from 'sonner';
import { DisplayParams } from '../types';

export const useEnhancedManualCheck = (
  mountedRef: React.MutableRefObject<boolean>,
  imageUrl: string | null,
  imagePollerHandleManualCheck: ((imageUrl: string | null, originalHandleManualCheck: () => Promise<boolean>, params: DisplayParams) => Promise<boolean>) | null,
  originalHandleManualCheck: () => Promise<boolean>,
  params: DisplayParams,
  extractMetadataFromImage: (url: string) => Promise<Record<string, string>>
) => {
  const handleManualCheck = useCallback(async () => {
    if (!mountedRef.current) return false;
    
    console.log('[useEnhancedManualCheck] Manual check initiated');
    
    if (!imageUrl) {
      toast.warning("No image to check");
      return false;
    }
    
    try {
      // If in debug mode or no imagePollerHandleManualCheck provided, use the original handler
      if (params.debugMode || !imagePollerHandleManualCheck) {
        console.log('[useEnhancedManualCheck] Using original manual check in debug mode');
        const result = await originalHandleManualCheck();
        
        // Also extract metadata directly in debug mode
        if (params.debugMode && imageUrl && mountedRef.current) {
          console.log('[useEnhancedManualCheck] Debug mode: forcing metadata extraction');
          try {
            await extractMetadataFromImage(imageUrl);
          } catch (err) {
            console.error('[useEnhancedManualCheck] Error extracting metadata in debug mode:', err);
          }
        }
        
        return result;
      }
      
      // Use the enhanced imagePoller version for normal mode
      console.log('[useEnhancedManualCheck] Using enhanced manual check in normal mode');
      return await imagePollerHandleManualCheck(imageUrl, originalHandleManualCheck, params);
    } catch (err) {
      console.error('[useEnhancedManualCheck] Error during manual check:', err);
      if (mountedRef.current) {
        toast.error('Error checking for image updates');
      }
      return false;
    }
  }, [
    mountedRef, 
    imageUrl, 
    params, 
    originalHandleManualCheck, 
    imagePollerHandleManualCheck,
    extractMetadataFromImage
  ]);
  
  return {
    handleManualCheck
  };
};
