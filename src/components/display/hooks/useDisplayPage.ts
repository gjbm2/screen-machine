
import { useState, useEffect, useRef } from 'react';
import { toast } from 'sonner';
import { useDisplayState } from '@/components/display/hooks/useDisplayState';
import { useDisplayParams } from '@/components/display/hooks/useDisplayParams';
import { useCaptionProcessor } from '@/components/display/hooks/useCaptionProcessor';
import { useImagePoller } from '@/components/display/hooks/useImagePoller';
import { useDebugFiles } from '@/components/display/hooks/useDebugFiles';
import { useMetadataManager } from '@/components/display/hooks/useMetadataManager';
import { useDebugRedirection } from '@/components/display/hooks/useDebugRedirection';
import { useOutputProcessor } from '@/components/display/hooks/useOutputProcessor';
import { useImageErrorHandler } from '@/components/display/hooks/useImageErrorHandler';
import { useEnhancedManualCheck } from '@/components/display/hooks/useEnhancedManualCheck';

export const useDisplayPage = () => {
  const { params, redirectToDebugMode } = useDisplayParams();
  const [previewParams, setPreviewParams] = useState(params);
  const mountedRef = useRef(true); // Track if component is mounted
  const outputProcessedRef = useRef(false); // Track if output has been processed

  // Debug logging for params
  useEffect(() => {
    console.log("[useDisplayPage] Debug mode active:", params.debugMode);
    console.log("[useDisplayPage] Params:", params);
    console.log("[useDisplayPage] Output param:", params.output);
  }, [params]);

  // Process output parameter
  useOutputProcessor(params);

  // Get display state from the core hook
  const {
    imageUrl,
    setImageUrl,
    error,
    imageKey,
    setImageKey, // Make sure this is destructured to use it
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

  // Handle output parameter directly to ensure image displays
  useEffect(() => {
    if (!mountedRef.current) return;
    
    // Only process if we have an output parameter and it hasn't been processed already for this value
    if (params.output && (!outputProcessedRef.current || imageUrl !== params.output)) {
      console.log('[useDisplayPage] Processing output parameter directly:', params.output);
      
      // Set the image URL directly
      setImageUrl(params.output);
      
      // Increment the image key to force a reload
      setImageKey(prev => prev + 1);
      
      // Mark as processed
      outputProcessedRef.current = true;
      
      // Toast to notify user
      toast.success(`Displaying image: ${params.output.split('/').pop()}`);
    }
  }, [params.output, setImageUrl, setImageKey, imageUrl]);

  // Reset output processed flag when output param changes
  useEffect(() => {
    if (params.output === null) {
      outputProcessedRef.current = false;
    }
  }, [params.output]);

  // Set mounted ref to false on unmount
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // Debug redirection handling
  const { checkDebugRedirection } = useDebugRedirection(params, redirectToDebugMode);
  
  // Check for debug redirection - only if not already in debug mode
  useEffect(() => {
    if (!mountedRef.current) return;
    if (!params.debugMode) {
      checkDebugRedirection();
    }
  }, [params, params.output, params.debugMode]);

  // Metadata management
  const { 
    attemptMetadataExtraction, 
    resetMetadataExtractionFlag 
  } = useMetadataManager(params, imageUrl, extractMetadataFromImage);

  // Enhanced debug logging for metadata
  useEffect(() => {
    if (!mountedRef.current) return;
    
    console.log('[useDisplayPage] Params:', params);
    console.log('[useDisplayPage] Image URL:', imageUrl);
    console.log('[useDisplayPage] Metadata:', metadata);
    
    // Only attempt metadata extraction if not loading and not transitioning
    if (!isLoading && !isTransitioning) {
      attemptMetadataExtraction(imageUrl, metadata, isLoading, isTransitioning);
    }
  }, [params, imageUrl, metadata, isLoading, isTransitioning]);

  // Reset metadata extraction flag when image URL changes
  useEffect(() => {
    if (!mountedRef.current) return;
    resetMetadataExtractionFlag();
  }, [imageUrl]);

  // Process captions with metadata
  useCaptionProcessor(previewParams, metadata, imageUrl, setProcessedCaption);

  // Fetch debug output files only when in debug mode
  useDebugFiles(params.debugMode, setOutputFiles);

  // Only setup image polling when NOT in debug mode to prevent infinite loops
  const { handleManualCheck: imagePollerHandleManualCheck } = params.debugMode 
    ? { handleManualCheck: null }
    : useImagePoller(
        params,
        imageUrl,
        isLoading,
        isTransitioning,
        loadNewImage,
        checkImageModified,
        extractMetadataFromImage
      );

  // Handle image errors
  const { handleImageError } = useImageErrorHandler(imageUrl, mountedRef);

  // Enhanced manual check - handle the case when imagePollerHandleManualCheck is null
  const { handleManualCheck } = useEnhancedManualCheck(
    mountedRef,
    imageUrl,
    imagePollerHandleManualCheck,
    originalHandleManualCheck,
    params,
    extractMetadataFromImage
  );

  // Update preview params when URL params change
  useEffect(() => {
    if (!mountedRef.current) return;
    setPreviewParams(params);
  }, [params]);

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
