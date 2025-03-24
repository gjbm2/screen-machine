
import { useBasicImageState } from './useBasicImageState';
import { useImageModificationCheck } from './useImageModificationCheck';
import { useMetadataExtractor } from './useMetadataExtractor';
import { useManualImageCheck } from './useManualImageCheck';

export const useImageState = () => {
  // Use the smaller, focused hooks
  const {
    imageUrl,
    setImageUrl,
    imageKey,
    setImageKey,
    isLoading,
    setIsLoading,
    imageRef,
    intervalRef,
    preloadImageRef
  } = useBasicImageState();

  const {
    lastModified,
    setLastModified,
    lastChecked,
    setLastChecked,
    imageChanged,
    setImageChanged,
    lastModifiedRef,
    checkImageModified
  } = useImageModificationCheck();

  const {
    metadata,
    setMetadata,
    lastMetadataUrlRef,
    isExtractingMetadataRef,
    extractMetadataFromImage
  } = useMetadataExtractor();

  // Manual check requires access to multiple hook values, so we compose it last
  const { handleManualCheck } = useManualImageCheck(
    imageUrl,
    setImageChanged,
    checkImageModified,
    extractMetadataFromImage,
    lastMetadataUrlRef
  );

  // Debug log when metadata state changes
  if (process.env.NODE_ENV !== 'production') {
    console.log('[useImageState] Current metadata:', metadata);
    console.log('[useImageState] Metadata keys:', Object.keys(metadata));
  }

  return {
    imageUrl,
    setImageUrl,
    imageKey,
    setImageKey,
    lastModified,
    setLastModified,
    lastChecked,
    setLastChecked,
    imageChanged,
    setImageChanged,
    metadata,
    setMetadata,
    isLoading,
    setIsLoading,
    imageRef,
    lastModifiedRef,
    intervalRef,
    preloadImageRef,
    lastMetadataUrlRef,
    isExtractingMetadataRef,
    checkImageModified,
    handleManualCheck,
    extractMetadataFromImage
  };
};
