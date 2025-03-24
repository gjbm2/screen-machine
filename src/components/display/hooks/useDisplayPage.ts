
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
import { normalizePathForDisplay } from '../utils/paramUtils';

export const useDisplayPage = () => {
  const { params, redirectToDebugMode } = useDisplayParams();
  const [previewParams, setPreviewParams] = useState(params);
  const mountedRef = useRef(true); // Track if component is mounted
  const initialRenderRef = useRef(true); // Track initial render

  // Debug logging for params
  useEffect(() => {
    console.log("[useDisplayPage] Debug mode active:", params.debugMode);
    console.log("[useDisplayPage] Params:", params);
    console.log("[useDisplayPage] Output param:", params.output);
    
    // Log a clear message if output parameter exists
    if (params.output) {
      console.log("[useDisplayPage] ⚠️ Output parameter detected:", params.output);
    }
  }, [params]);

  // Process output parameter
  useOutputProcessor(params);

  // Get display state from the core hook
  const {
    imageUrl,
    setImageUrl,
    error,
    imageKey,
    setImageKey,
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
    
    if (params.output) {
      console.log('[useDisplayPage] Processing output parameter:', params.output);
      
      // For fully formed URLs, use directly
      if (params.output.startsWith('http://') || params.output.startsWith('https://')) {
        setImageUrl(params.output);
      } else {
        // For local paths, normalize 
        const normalizedPath = normalizePathForDisplay(params.output);
        setImageUrl(normalizedPath);
      }
      
      // Increment the image key to force a reload
      setImageKey(prev => prev + 1);
      
      // Toast to notify user
      const filename = params.output.split('/').pop() || params.output;
      const displayName = filename.length > 30 ? filename.substring(0, 27) + '...' : filename;
      toast.success(`Displaying image: ${displayName}`);
    }
  }, [params.output, setImageUrl, setImageKey]);

  // Set mounted ref to false on unmount
  useEffect(() => {
    mountedRef.current = true;
    console.log('[useDisplayPage] Component mounted');
    
    return () => {
      console.log('[useDisplayPage] Component unmounting');
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

  // Special handling for initial render with output parameter
  useEffect(() => {
    if (initialRenderRef.current && params.output) {
      console.log('[useDisplayPage] Initial render with output param:', params.output);
      initialRenderRef.current = false;
      
      // Small delay to ensure DOM is ready
      setTimeout(() => {
        if (mountedRef.current) {
          // For fully formed URLs, use directly
          if (params.output && params.output.startsWith('http')) {
            setImageUrl(params.output);
          } else if (params.output) {
            // For local paths, normalize 
            const normalizedPath = normalizePathForDisplay(params.output);
            setImageUrl(normalizedPath);
          }
          setImageKey(prev => prev + 1);
        }
      }, 100);
    }
  }, [params.output, setImageUrl, setImageKey]);

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
    handleManualCheck: originalHandleManualCheck,
    getImagePositionStyle,
    handleImageError: useImageErrorHandler(imageUrl, mountedRef).handleImageError
  };
};
