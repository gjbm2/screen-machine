
import { useRef } from 'react';
import { DisplayParams } from '@/components/display/types';
import { processOutputParam } from '@/components/display/utils';
import { useInitialImageLoad } from './useInitialImageLoad';
import { useImageChangeDetector } from './useImageChangeDetector';
import { useManualImageUpdater } from './useManualImageUpdater';

export const useImagePoller = (
  params: DisplayParams,
  imageUrl: string | null,
  isLoading: boolean,
  isTransitioning: boolean,
  loadNewImage: (url: string) => void,
  checkImageModified: (url: string) => Promise<boolean>,
  extractMetadataFromImage: (url: string) => Promise<Record<string, string>>
) => {
  // Process the URL once
  const processedUrl = params.output ? processOutputParam(params.output) : null;
  
  // Track component mounted state for cleanup
  const mountedRef = useRef<boolean>(true);
  
  // Handle initial image loading
  const { initialLoadCompleted } = useInitialImageLoad(
    processedUrl,
    isTransitioning,
    isLoading,
    loadNewImage,
    async (url: string) => {
      if (mountedRef.current) {
        await extractMetadataFromImage(url);
      }
    },
    false
  );
  
  // Detect image changes
  const { lastCheckedUrl } = useImageChangeDetector(
    imageUrl,
    isLoading,
    isTransitioning,
    extractMetadataFromImage
  );
  
  // Handle manual updates
  const { handleManualUpdate } = useManualImageUpdater(mountedRef);
  
  // Create a manual check function that will be exposed to the UI
  const handleManualCheck = async (
    imageUrl: string | null,
    originalHandleManualCheck: () => Promise<boolean>,
    params: DisplayParams
  ): Promise<boolean> => {
    return handleManualUpdate(imageUrl, originalHandleManualCheck, params);
  };

  return {
    handleManualCheck,
    isChecking: false,
    lastCheckTime: null
  };
};
