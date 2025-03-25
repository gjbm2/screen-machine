
import { useRef } from 'react';
import { DisplayParams } from '@/components/display/types';
import { processOutputParam } from '@/components/display/utils';
import { useInitialImageLoad } from './useInitialImageLoad';
import { useImageChangeDetector } from './useImageChangeDetector';
import { useImageCheckPoller } from './useImageCheckPoller';
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
    false // Remove dependency on debug mode for initial load
  );
  
  // Detect image changes
  const { lastCheckedUrl } = useImageChangeDetector(
    imageUrl,
    isLoading,
    isTransitioning,
    extractMetadataFromImage
  );
  
  // Set up polling for image changes - Enable polling regardless of debug mode
  // Ensure that refreshInterval defaults to 5 if not set
  const effectiveRefreshInterval = params.refreshInterval || 5;
  const isPollingEnabled = !!processedUrl;
  
  // Log the polling state
  console.log('[useImagePoller] Polling enabled:', isPollingEnabled, 'Refresh interval:', effectiveRefreshInterval);
  
  const { pollNow, isChecking, lastCheckTime } = useImageCheckPoller(
    processedUrl,
    effectiveRefreshInterval,
    isLoading,
    isTransitioning,
    checkImageModified,
    loadNewImage,
    extractMetadataFromImage,
    isPollingEnabled
  );
  
  // Handle manual updates
  const { handleManualUpdate } = useManualImageUpdater(mountedRef);
  
  // Enhanced manual check that handles metadata refresh
  const handleManualCheck = async (
    imageUrl: string | null,
    originalHandleManualCheck: () => Promise<boolean>,
    params: DisplayParams
  ): Promise<boolean> => {
    return handleManualUpdate(imageUrl, originalHandleManualCheck, params);
  };

  return {
    handleManualCheck,
    isChecking,
    lastCheckTime
  };
};
