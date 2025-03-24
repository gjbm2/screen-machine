
import { useState, useEffect, useRef } from 'react';
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
  const [previousImageUrl, setPreviousImageUrl] = useState<string | null>(null);
  const redirectAttemptedRef = useRef(false);

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
    
    // Check if the image URL has changed
    const imageUrlChanged = imageUrl !== previousImageUrl;
    
    // Only extract metadata if the image URL has changed or if we haven't attempted it yet for this URL
    if (imageUrl && 
        (imageUrlChanged || Object.keys(metadata).length === 0) && 
        !isLoading && 
        !isTransitioning && 
        !metadataExtractionAttempted) {
      
      console.log('[useDisplayPage] Image URL changed or no metadata found, retrieving metadata');
      setPreviousImageUrl(imageUrl);
      setMetadataExtractionAttempted(true);
      
      // Always try to extract metadata when image changes or loads
      extractMetadataFromImage(imageUrl).catch(err => 
        console.error('[useDisplayPage] Error extracting metadata:', err)
      );
    }
  }, [params, imageUrl, metadata, extractMetadataFromImage, isLoading, isTransitioning, previousImageUrl, metadataExtractionAttempted]);

  // Reset metadata extraction flag when image URL changes
  useEffect(() => {
    if (imageUrl !== previousImageUrl) {
      setPreviousImageUrl(imageUrl);
      setMetadataExtractionAttempted(false);
    }
  }, [imageUrl, previousImageUrl]);

  // Redirect to debug mode if needed (only once per component mount)
  useEffect(() => {
    if (!redirectAttemptedRef.current && params.output) {
      redirectAttemptedRef.current = true;
      redirectToDebugMode();
    }
  }, [params.output, redirectToDebugMode]);

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
    checkImageModified,
    extractMetadataFromImage
  );

  // Update preview params when URL params change
  useEffect(() => {
    setPreviewParams(params);
  }, [params]);

  // Wrap the manual check to use the enhanced version
  const handleManualCheck = async () => {
    const result = await imagePollerHandleManualCheck(imageUrl, originalHandleManualCheck, params);
    
    // If there's a new image loaded, extract metadata
    if (result && imageUrl) {
      console.log('[useDisplayPage] New image loaded after manual check, extracting metadata');
      await extractMetadataFromImage(imageUrl);
    }
    
    return result;
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
