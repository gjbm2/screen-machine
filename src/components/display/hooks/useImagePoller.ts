
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
  
  // Important fix: Always call these hooks in the same order, regardless of conditions
  
  // Handle initial image loading - always call these hooks regardless of conditions
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
    params.debugMode || false
  );
  
  // Detect image changes - always call this hook
  const { lastCheckedUrl, isLoadingMetadata } = useImageChangeDetector(
    imageUrl,
    isLoading,
    isTransitioning,
    extractMetadataFromImage
  );
  
  // Set up polling for image changes - call this hook with stable params
  const effectiveRefreshInterval = params.refreshInterval ?? 5;
  const isPollingEnabled = !!processedUrl && effectiveRefreshInterval > 0;
  
  // Always call the hook, conditional behavior happens inside the hook
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
  
  // Handle manual updates - always call this hook
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
    lastCheckTime,
    isLoadingMetadata
  };
};
