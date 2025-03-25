
import { useCallback } from 'react';
import { DisplayParams } from '../types';

export const useManualImageUpdater = (
  mountedRef: React.MutableRefObject<boolean>
) => {
  const handleManualUpdate = useCallback(async (
    currentImageUrl: string | null,
    originalHandleManualCheck: () => Promise<boolean>,
    currentParams: DisplayParams
  ): Promise<boolean> => {
    console.log('[useManualImageUpdater] Manual update started');
    console.log('[useManualImageUpdater] Current transition type:', currentParams.transition);
    
    if (!mountedRef.current) {
      console.log('[useManualImageUpdater] Component unmounted, skipping');
      return false;
    }
    
    if (!currentImageUrl) {
      console.log('[useManualImageUpdater] No image URL, skipping');
      return false;
    }
    
    try {
      // Call the original handler
      console.log('[useManualImageUpdater] Calling original handler with transition type:', currentParams.transition);
      return await originalHandleManualCheck();
    } catch (err) {
      console.error('[useManualImageUpdater] Error in manual update:', err);
      return false;
    }
  }, [mountedRef]);
  
  return {
    handleManualUpdate
  };
};
