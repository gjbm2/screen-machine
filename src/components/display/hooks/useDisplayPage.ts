
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
  const { displayParams, updateParam, location } = useDisplayParams();
  const [previewParams, setPreviewParams] = useState(displayParams);
  const mountedRef = useRef(true); // Track if component is mounted
  const initialRenderRef = useRef(true); // Track initial render
  const hasProcessedOutputRef = useRef(false); // Track if we've processed the output param

  // Function to redirect to debug mode
  const redirectToDebugMode = () => {
    updateParam('debug', 'true');
  };

  // Debug logging for params
  useEffect(() => {
    console.log("[useDisplayPage] Debug mode active:", displayParams.debugMode);
    console.log("[useDisplayPage] Params:", displayParams);
    console.log("[useDisplayPage] Output param:", displayParams.output);
    
    // Log a clear message if output parameter exists
    if (displayParams.output) {
      console.log("[useDisplayPage] ⚠️ Output parameter detected:", displayParams.output);
    }
  }, [displayParams]);

  // Process output parameter
  useOutputProcessor(displayParams);

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
    
    if (displayParams.output && !hasProcessedOutputRef.current) {
      console.log('[useDisplayPage] Processing output parameter:', displayParams.output);
      hasProcessedOutputRef.current = true;
      
      // For fully formed URLs, use directly
      if (displayParams.output.startsWith('http://') || displayParams.output.startsWith('https://')) {
        console.log('[useDisplayPage] Setting direct URL:', displayParams.output);
        setImageUrl(displayParams.output);
      } else {
        // For local paths, normalize 
        const normalizedPath = normalizePathForDisplay(displayParams.output);
        console.log('[useDisplayPage] Setting normalized path:', normalizedPath);
        setImageUrl(normalizedPath);
      }
      
      // Increment the image key to force a reload
      setImageKey(prev => prev + 1);
      
      // Toast to notify user
      const filename = displayParams.output.split('/').pop() || displayParams.output;
      const displayName = filename.length > 30 ? filename.substring(0, 27) + '...' : filename;
      toast.success(`Displaying image: ${displayName}`);
    }
  }, [displayParams.output, setImageUrl, setImageKey]);

  // Reset output processing flag on route change
  useEffect(() => {
    hasProcessedOutputRef.current = false;
  }, [location.pathname, location.search]);

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
  const { checkDebugRedirection } = useDebugRedirection(displayParams, redirectToDebugMode);
  
  // Check for debug redirection - only if not already in debug mode
  useEffect(() => {
    if (!mountedRef.current) return;
    if (!displayParams.debugMode) {
      checkDebugRedirection();
    }
  }, [displayParams, displayParams.output, displayParams.debugMode]);

  // Metadata management
  const { 
    attemptMetadataExtraction, 
    resetMetadataExtractionFlag 
  } = useMetadataManager(displayParams, imageUrl, extractMetadataFromImage);

  // Enhanced debug logging for metadata
  useEffect(() => {
    if (!mountedRef.current) return;
    
    // Only attempt metadata extraction if not loading and not transitioning
    if (!isLoading && !isTransitioning) {
      attemptMetadataExtraction(imageUrl, metadata, isLoading, isTransitioning);
    }
  }, [displayParams, imageUrl, metadata, isLoading, isTransitioning]);

  // Reset metadata extraction flag when image URL changes
  useEffect(() => {
    if (!mountedRef.current) return;
    resetMetadataExtractionFlag();
  }, [imageUrl]);

  // Process captions with metadata
  useCaptionProcessor(previewParams, metadata, imageUrl, setProcessedCaption);

  // Fetch debug output files only when in debug mode
  useDebugFiles(displayParams.debugMode, setOutputFiles);

  // Only setup image polling when NOT in debug mode to prevent infinite loops
  const { handleManualCheck: imagePollerHandleManualCheck } = displayParams.debugMode 
    ? { handleManualCheck: null }
    : useImagePoller(
        displayParams,
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
    displayParams,
    extractMetadataFromImage
  );

  // Update preview params when URL params change
  useEffect(() => {
    if (!mountedRef.current) return;
    setPreviewParams(displayParams);
  }, [displayParams]);

  // Special handling for initial render with output parameter
  useEffect(() => {
    if (initialRenderRef.current && displayParams.output) {
      console.log('[useDisplayPage] Initial render with output param:', displayParams.output);
      initialRenderRef.current = false;
      
      // Small delay to ensure DOM is ready
      setTimeout(() => {
        if (mountedRef.current) {
          // For fully formed URLs, use directly
          if (displayParams.output && displayParams.output.startsWith('http')) {
            console.log('[useDisplayPage] Initial render - setting direct URL:', displayParams.output);
            setImageUrl(displayParams.output);
          } else if (displayParams.output) {
            // For local paths, normalize 
            const normalizedPath = normalizePathForDisplay(displayParams.output);
            console.log('[useDisplayPage] Initial render - setting normalized path:', normalizedPath);
            setImageUrl(normalizedPath);
          }
          setImageKey(prev => prev + 1);
        }
      }, 100);
    }
  }, [displayParams.output, setImageUrl, setImageKey]);

  return {
    params: displayParams,
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
    handleImageError: useImageErrorHandler(imageUrl, mountedRef).handleImageError,
    redirectToDebugMode
  };
};
