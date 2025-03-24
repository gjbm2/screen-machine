
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
  const [metadataExtractionAttempted, setMetadataExtractionAttempted] = useState(false);

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
    getImagePositionStyle,
    extractMetadataFromImage
  } = useDisplayState(previewParams);

  // Enhanced debug logging for metadata
  useEffect(() => {
    console.log('[useDisplayPage] Params:', params);
    console.log('[useDisplayPage] Image URL:', imageUrl);
    console.log('[useDisplayPage] Metadata:', metadata);
    
    // Only extract metadata once per imageUrl and only if needed
    if (imageUrl && 
        Object.keys(metadata).length === 0 && 
        !metadataExtractionAttempted && 
        !isLoading) {
      console.log('[useDisplayPage] No metadata found, triggering extraction once');
      
      setMetadataExtractionAttempted(true);
      extractMetadataFromImage(imageUrl).catch(err => 
        console.error('[useDisplayPage] Error extracting metadata:', err)
      );
    }
  }, [params, imageUrl, metadata, extractMetadataFromImage, metadataExtractionAttempted, isLoading]);

  // Reset metadata extraction flag when image URL changes
  useEffect(() => {
    if (imageUrl) {
      setMetadataExtractionAttempted(false);
    }
  }, [imageUrl]);

  // Redirect to debug mode if needed
  useEffect(() => {
    redirectToDebugMode();
  }, [params.output, params.debugMode, redirectToDebugMode]);

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
