
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

  // Process output parameter
  useOutputProcessor(params);

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

  // Set mounted ref to false on unmount
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // Debug redirection handling
  const { checkDebugRedirection } = useDebugRedirection(params, redirectToDebugMode);
  
  // Check for debug redirection
  useEffect(() => {
    if (!mountedRef.current) return;
    checkDebugRedirection();
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
    
    attemptMetadataExtraction(imageUrl, metadata, isLoading, isTransitioning);
  }, [params, imageUrl, metadata, isLoading, isTransitioning]);

  // Reset metadata extraction flag when image URL changes
  useEffect(() => {
    if (!mountedRef.current) return;
    resetMetadataExtractionFlag();
  }, [imageUrl]);

  // Add debug output to trace debug mode functionality
  useEffect(() => {
    if (!mountedRef.current) return;
    
    if (params.debugMode) {
      console.log('[useDisplayPage] Debug mode is enabled in params');
    } else {
      console.log('[useDisplayPage] Debug mode is NOT enabled in params');
    }
  }, [params.debugMode]);

  // Add debug logging for image loading
  useEffect(() => {
    if (!mountedRef.current) return;
    
    if (imageUrl) {
      console.log('[useDisplayPage] Current image URL:', imageUrl);
    } else if (params.output) {
      console.log('[useDisplayPage] No image URL yet, but output param is:', params.output);
    }
  }, [imageUrl, params.output]);

  // Process captions with metadata
  useCaptionProcessor(previewParams, metadata, imageUrl, setProcessedCaption);

  // Fetch debug output files only when in debug mode
  useDebugFiles(params.debugMode, setOutputFiles);

  // Poll for image changes - Only setup when component is mounted
  const { handleManualCheck: imagePollerHandleManualCheck } = useImagePoller(
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

  // Enhanced manual check
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
