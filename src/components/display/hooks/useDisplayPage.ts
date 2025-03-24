
import { useState, useEffect, useRef } from 'react';
import { toast } from 'sonner';
import { useDisplayState } from '@/components/display/hooks/useDisplayState';
import { useDisplayParams } from '@/components/display/hooks/useDisplayParams';
import { useCaptionProcessor } from '@/components/display/hooks/useCaptionProcessor';
import { useImagePoller } from '@/components/display/hooks/useImagePoller';
import { useDebugFiles } from '@/components/display/hooks/useDebugFiles';
import { processOutputParam } from '../utils/paramUtils';

export const useDisplayPage = () => {
  const { params, redirectToDebugMode } = useDisplayParams();
  const [previewParams, setPreviewParams] = useState(params);
  const [metadataExtractionAttempted, setMetadataExtractionAttempted] = useState(false);
  const [previousImageUrl, setPreviousImageUrl] = useState<string | null>(null);
  const redirectAttemptedRef = useRef(false);
  const debugHandledRef = useRef(false);
  const mountedRef = useRef(true); // Track if component is mounted

  // Set mounted ref to false on unmount
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // Process the output parameter to ensure correct URL format
  useEffect(() => {
    if (params.output) {
      const processedOutput = processOutputParam(params.output);
      if (processedOutput !== params.output) {
        console.log('[useDisplayPage] Processed output param from:', params.output, 'to:', processedOutput);
        params.output = processedOutput;
      }
    }
  }, [params.output]);

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
    if (!mountedRef.current) return; // Skip if unmounted
    
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
        !metadataExtractionAttempted &&
        mountedRef.current) {
      
      console.log('[useDisplayPage] Image URL changed or no metadata found, retrieving metadata');
      setPreviousImageUrl(imageUrl);
      setMetadataExtractionAttempted(true);
      
      // Always try to extract metadata when image changes or loads
      if (mountedRef.current) {
        extractMetadataFromImage(imageUrl).catch(err => 
          console.error('[useDisplayPage] Error extracting metadata:', err)
        );
      }
    }
  }, [params, imageUrl, metadata, extractMetadataFromImage, isLoading, isTransitioning, previousImageUrl, metadataExtractionAttempted]);

  // Reset metadata extraction flag when image URL changes
  useEffect(() => {
    if (!mountedRef.current) return; // Skip if unmounted
    
    if (imageUrl !== previousImageUrl) {
      setPreviousImageUrl(imageUrl);
      setMetadataExtractionAttempted(false);
    }
  }, [imageUrl, previousImageUrl]);

  // Add debug output to trace debug mode functionality
  useEffect(() => {
    if (!mountedRef.current) return; // Skip if unmounted
    
    if (params.debugMode) {
      console.log('[useDisplayPage] Debug mode is enabled in params');
    } else {
      console.log('[useDisplayPage] Debug mode is NOT enabled in params');
    }
  }, [params.debugMode]);

  // Redirect to debug mode if needed (only once per component mount)
  useEffect(() => {
    if (!mountedRef.current) return; // Skip if unmounted
    
    console.log('[useDisplayPage] Checking if debug redirection is needed:', {
      params,
      alreadyAttempted: redirectAttemptedRef.current,
      hasOutput: !!params.output,
      debugMode: params.debugMode,
      debugHandled: debugHandledRef.current
    });
    
    if (!redirectAttemptedRef.current && params.output && !debugHandledRef.current) {
      redirectAttemptedRef.current = true;
      console.log('[useDisplayPage] Attempting debug redirection check');
      
      // If we're in debug mode, mark it as handled to prevent further checks
      if (params.debugMode) {
        debugHandledRef.current = true;
        console.log('[useDisplayPage] Already in debug mode, marking as handled');
      } else {
        // Only redirect if not already in debug mode
        try {
          redirectToDebugMode();
        } catch (err) {
          console.error('[useDisplayPage] Error during debug redirection:', err);
        }
      }
    }
  }, [params, params.output, params.debugMode, redirectToDebugMode]);

  // Add debug logging for image loading
  useEffect(() => {
    if (!mountedRef.current) return; // Skip if unmounted
    
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

  // Update preview params when URL params change
  useEffect(() => {
    if (!mountedRef.current) return; // Skip if unmounted
    setPreviewParams(params);
  }, [params]);

  // Wrap the manual check to use the enhanced version
  const handleManualCheck = async () => {
    if (!mountedRef.current) return false; // Skip if unmounted
    
    const result = await imagePollerHandleManualCheck(imageUrl, originalHandleManualCheck, params);
    
    // If there's a new image loaded, extract metadata
    if (result && imageUrl && mountedRef.current) {
      console.log('[useDisplayPage] New image loaded after manual check, extracting metadata');
      await extractMetadataFromImage(imageUrl);
    }
    
    return result;
  };

  const handleImageError = () => {
    if (!mountedRef.current) return; // Skip if unmounted
    
    console.error('Failed to load image:', imageUrl);
    toast.error("Failed to load image");
    
    // Log more details about the failed image
    if (imageUrl) {
      console.error('[handleImageError] Image URL that failed to load:', imageUrl);
      fetch(imageUrl, { method: 'HEAD' })
        .then(response => {
          if (mountedRef.current) {
            console.log('[handleImageError] HTTP status for image:', response.status, response.statusText);
          }
        })
        .catch(err => {
          if (mountedRef.current) {
            console.error('[handleImageError] Network error when checking image:', err);
          }
        });
    }
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
