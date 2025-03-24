
import { useState, useEffect } from 'react';
import { toast } from 'sonner';
import { useDisplayState } from '@/components/display/hooks/useDisplayState';
import { useDisplayParams } from '@/components/display/hooks/useDisplayParams';
import { useCaptionProcessor } from '@/components/display/hooks/useCaptionProcessor';
import { useImagePoller } from '@/components/display/hooks/useImagePoller';
import { useDebugFiles } from '@/components/display/hooks/useDebugFiles';

export const useDisplayPage = () => {
  const { params, redirectToDebugMode } = useDisplayParams();
  const [previewParams, setPreviewParams] = useState(params);

  // Get display state from the core hook
  const {
    imageUrl,
    error,
    imageKey,
    lastModified,
    lastChecked,
    outputFiles,
    setOutputFiles,
    imageChanged,
    metadata,
    isLoading,
    processedCaption,
    setProcessedCaption,
    isTransitioning,
    oldImageUrl,
    oldImageStyle,
    newImageStyle,
    imageRef,
    nextCheckTime,
    loadNewImage,
    checkImageModified,
    handleManualCheck: originalHandleManualCheck,
    getImagePositionStyle
  } = useDisplayState(previewParams);

  // Debug logging
  useEffect(() => {
    console.log('[useDisplayPage] Params:', params);
    console.log('[useDisplayPage] Image URL:', imageUrl);
    console.log('[useDisplayPage] Metadata:', metadata);
  }, [params, imageUrl, metadata]);

  // Redirect to debug mode if needed
  useEffect(() => {
    redirectToDebugMode();
  }, [params.output, params.debugMode]);

  // Process captions with metadata
  useCaptionProcessor(previewParams, metadata, imageUrl, setProcessedCaption);

  // Fetch debug output files
  useDebugFiles(params.debugMode, setOutputFiles);

  // Poll for image changes
  const { handleManualCheck: imagePollerHandleManualCheck } = useImagePoller(
    params,
    imageUrl,
    isLoading,
    isTransitioning,
    loadNewImage,
    checkImageModified
  );

  // Update preview params when URL params change
  useEffect(() => {
    setPreviewParams(params);
  }, [params]);

  // Wrap the manual check to use the enhanced version
  const handleManualCheck = async () => {
    return imagePollerHandleManualCheck(imageUrl, originalHandleManualCheck, params);
  };

  const handleImageError = () => {
    console.error('Failed to load image:', imageUrl);
    toast.error("Failed to load image");
  };

  return {
    params,
    previewParams,
    imageUrl,
    error,
    imageKey,
    lastModified,
    lastChecked,
    outputFiles,
    imageChanged,
    metadata,
    isLoading,
    processedCaption,
    isTransitioning,
    oldImageUrl,
    oldImageStyle,
    newImageStyle,
    imageRef,
    nextCheckTime,
    handleManualCheck,
    getImagePositionStyle,
    handleImageError
  };
};
