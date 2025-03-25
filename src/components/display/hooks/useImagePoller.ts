
import { useRef, useEffect } from 'react';
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
  
  // Track component mounted state for cleanup - always declare all refs first
  const mountedRef = useRef<boolean>(true);
  
  // Set mountedRef cleanup
  useEffect(() => {
    mountedRef.current = true;
    console.log('[useImagePoller] Component mounted');
    
    return () => {
      console.log('[useImagePoller] Component unmounting');
      mountedRef.current = false;
    };
  }, []);
  
  // Call our hooks - always call them in the same order
  const { lastCheckedUrl, isLoadingMetadata } = useImageChangeDetector(
    imageUrl,
    isLoading,
    isTransitioning,
    extractMetadataFromImage
  );
  
  // Set up polling for image changes
  const effectiveRefreshInterval = params.refreshInterval ?? 5;
  const isPollingEnabled = !!processedUrl && effectiveRefreshInterval > 0;
  
  // Always call this hook
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
  
  // Handle initial image loading - always call this hook
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
  
  // Handle manual updates - always call this hook
  const { handleManualUpdate } = useManualImageUpdater(mountedRef);
  
  // Enhanced manual check that handles metadata refresh
  const handleManualCheck = async (
    currentImageUrl: string | null = null,
    originalHandleManualCheck: (() => Promise<boolean>) | null = null,
    currentParams: DisplayParams = params
  ): Promise<boolean> => {
    return handleManualUpdate(currentImageUrl || imageUrl, 
      originalHandleManualCheck || (async () => await Promise.resolve(false)), 
      currentParams);
  };

  return {
    handleManualCheck,
    isChecking,
    lastCheckTime,
    isLoadingMetadata
  };
};
