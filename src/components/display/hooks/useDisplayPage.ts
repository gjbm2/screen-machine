
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
  const hasCheckedExplicitExitRef = useRef(false); // Track if we've checked localStorage
  const forceViewModeRef = useRef(false); // Track if we should force view mode

  // Function to redirect to debug mode
  const redirectToDebugMode = () => {
    if (forceViewModeRef.current) {
      console.log('[useDisplayPage] Skipping debug mode redirect because forceViewMode is true');
      return;
    }
    updateParam('debug', 'true');
  };

  // Check if user explicitly exited debug mode
  useEffect(() => {
    if (!hasCheckedExplicitExitRef.current && mountedRef.current) {
      hasCheckedExplicitExitRef.current = true;
      
      try {
        const userExplicitlyExited = localStorage.getItem('userExplicitlyExitedDebug');
        console.log('[useDisplayPage] Checking localStorage for explicit exit flag:', userExplicitlyExited);
        
        if (userExplicitlyExited === 'true') {
          console.log('[useDisplayPage] Found explicit debug exit flag in localStorage');
          // Set the forced view mode flag
          forceViewModeRef.current = true;
          
          // Clear the flag immediately to prevent it from affecting future navigation
          // unless this is an initial load of the page after committing settings
          if (displayParams.debugMode === false) {
            console.log('[useDisplayPage] Current page is in view mode, clearing localStorage flag');
            localStorage.removeItem('userExplicitlyExitedDebug');
          } else {
            console.log('[useDisplayPage] Not clearing localStorage flag yet since page is still in debug mode');
          }
        }
      } catch (e) {
        console.error('[useDisplayPage] Error checking localStorage flag:', e);
      }
    }
  }, [displayParams.debugMode]);

  // Debug logging for params
  useEffect(() => {
    console.log("[useDisplayPage] Debug mode active:", displayParams.debugMode);
    console.log("[useDisplayPage] ForceViewMode flag:", forceViewModeRef.current);
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

  // Debug redirection handling
  const { checkDebugRedirection, userExplicitlyExitedDebugRef } = useDebugRedirection(displayParams, redirectToDebugMode);
  
  // Check for explicit exit flag from localStorage and sync to our ref
  useEffect(() => {
    if (mountedRef.current) {
      try {
        const userExplicitlyExited = localStorage.getItem('userExplicitlyExitedDebug');
        if (userExplicitlyExited === 'true') {
          console.log('[useDisplayPage] Setting explicit exit flag from localStorage');
          userExplicitlyExitedDebugRef.current = true;
          forceViewModeRef.current = true;
          
          // Only remove the flag if we're actually in view mode
          if (!displayParams.debugMode) {
            localStorage.removeItem('userExplicitlyExitedDebug');
            console.log('[useDisplayPage] Removed localStorage flag - now in view mode');
          }
        }
      } catch (e) {
        console.error('[useDisplayPage] Error checking localStorage:', e);
      }
    }
  }, [userExplicitlyExitedDebugRef, displayParams.debugMode]);

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

  // Check for debug redirection - only if user has not explicitly exited and we're not forcing view mode
  useEffect(() => {
    if (!mountedRef.current) return;
    if (forceViewModeRef.current || userExplicitlyExitedDebugRef.current) {
      console.log('[useDisplayPage] Skipping debug redirection check - user explicitly exited debug mode or force view mode is set');
    } else if (displayParams.output) {
      // If we have an output parameter but we're not in debug mode,
      // only redirect if this isn't a result of a commit action
      checkDebugRedirection();
    } else {
      // No output parameter, always check for redirection
      checkDebugRedirection();
    }
  }, [displayParams, displayParams.output, displayParams.debugMode, userExplicitlyExitedDebugRef]);

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

  // Setup image polling in both view mode and debug mode when URL is present
  const { handleManualCheck: imagePollerHandleManualCheck } = displayParams.output 
    ? useImagePoller(
        displayParams,
        imageUrl,
        isLoading,
        isTransitioning,
        loadNewImage,
        checkImageModified,
        extractMetadataFromImage
      )
    : { handleManualCheck: null };

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
