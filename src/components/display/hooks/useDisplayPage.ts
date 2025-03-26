
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
import { normalizePathForDisplay, decodeComplexOutputParam, fullyDecodeUrl } from '../utils/paramUtils';

export const useDisplayPage = () => {
  // Get URL parameters and state management
  const { displayParams, updateParam, location } = useDisplayParams();
  const [previewParams, setPreviewParams] = useState(displayParams);
  
  // Refs for managing component lifecycle and state
  const mountedRef = useRef(true);
  const initialRenderRef = useRef(true);
  const hasProcessedOutputRef = useRef(false);
  const hasCheckedExplicitExitRef = useRef(false);
  const forceViewModeRef = useRef(false);

  // Function to redirect to debug mode
  const redirectToDebugMode = () => {
    if (forceViewModeRef.current) {
      console.log('[useDisplayPage] Skipping debug mode redirect because forceViewMode is true');
      return;
    }
    updateParam('debug', 'true');
  };

  // Check if user explicitly exited debug mode before
  useEffect(() => {
    if (!hasCheckedExplicitExitRef.current && mountedRef.current) {
      hasCheckedExplicitExitRef.current = true;
      
      try {
        const userExplicitlyExited = localStorage.getItem('userExplicitlyExitedDebug');
        console.log('[useDisplayPage] Checking localStorage for explicit exit flag:', userExplicitlyExited);
        
        if (userExplicitlyExited === 'true') {
          console.log('[useDisplayPage] Found explicit debug exit flag in localStorage');
          forceViewModeRef.current = true;
          
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

  // Log debug information when params change
  useEffect(() => {
    console.log("[useDisplayPage] Debug mode active:", displayParams.debugMode);
    console.log("[useDisplayPage] ForceViewMode flag:", forceViewModeRef.current);
    console.log("[useDisplayPage] Params:", displayParams);
    console.log("[useDisplayPage] Output param:", displayParams.output);
    
    if (displayParams.output) {
      console.log("[useDisplayPage] ⚠️ Output parameter detected:", displayParams.output);
    }
  }, [displayParams]);

  // Process output parameter if present
  useOutputProcessor(displayParams);

  // Initialize display state with core functionality
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

  // Debug mode redirection handling
  const { checkDebugRedirection, userExplicitlyExitedDebugRef } = useDebugRedirection(displayParams, redirectToDebugMode);

  // Sync user explicit exit flag with localStorage
  useEffect(() => {
    if (mountedRef.current) {
      try {
        const userExplicitlyExited = localStorage.getItem('userExplicitlyExitedDebug');
        if (userExplicitlyExited === 'true') {
          console.log('[useDisplayPage] Setting explicit exit flag from localStorage');
          userExplicitlyExitedDebugRef.current = true;
          forceViewModeRef.current = true;
          
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

  // Process output parameter on first detection - Fix #2: Ensure external URLs are handled properly
  useEffect(() => {
    if (!mountedRef.current) return;
    
    if (displayParams.output && !hasProcessedOutputRef.current) {
      console.log('[useDisplayPage] Processing output parameter:', displayParams.output);
      hasProcessedOutputRef.current = true;
      
      // Fix #2: Different handling for external URLs vs local paths
      const decodedOutput = decodeComplexOutputParam(displayParams.output);
      if (decodedOutput && (decodedOutput.startsWith('http://') || decodedOutput.startsWith('https://'))) {
        console.log('[useDisplayPage] Setting direct URL:', decodedOutput);
        setImageUrl(decodedOutput);
      } else if (decodedOutput) {
        const normalizedPath = normalizePathForDisplay(decodedOutput);
        console.log('[useDisplayPage] Setting normalized path:', normalizedPath);
        setImageUrl(normalizedPath);
      }
      
      setImageKey(prev => prev + 1);
      
      const filename = displayParams.output.split('/').pop() || displayParams.output;
      const displayName = filename.length > 30 ? filename.substring(0, 27) + '...' : filename;
      toast.success(`Displaying image: ${displayName}`);
    }
  }, [displayParams.output, setImageUrl, setImageKey]);

  // Reset output processing flag when URL changes
  useEffect(() => {
    hasProcessedOutputRef.current = false;
  }, [location.pathname, location.search]);

  // Component lifecycle management
  useEffect(() => {
    mountedRef.current = true;
    console.log('[useDisplayPage] Component mounted');
    
    return () => {
      console.log('[useDisplayPage] Component unmounting');
      mountedRef.current = false;
    };
  }, []);

  // Fix #1: Determine if debug redirection should occur - make sure it redirects when no params
  useEffect(() => {
    if (!mountedRef.current) return;
    if (forceViewModeRef.current || userExplicitlyExitedDebugRef.current) {
      console.log('[useDisplayPage] Skipping debug redirection check - user explicitly exited debug mode or force view mode is set');
    } else {
      // Modified condition - always check for redirection
      checkDebugRedirection();
    }
  }, [displayParams, displayParams.output, displayParams.debugMode, userExplicitlyExitedDebugRef, checkDebugRedirection]);

  // Metadata extraction management
  const { 
    attemptMetadataExtraction, 
    resetMetadataExtractionFlag 
  } = useMetadataManager(displayParams, imageUrl, extractMetadataFromImage);

  // Extract metadata when image loads
  useEffect(() => {
    if (!mountedRef.current) return;
    
    if (!isLoading && !isTransitioning) {
      attemptMetadataExtraction(imageUrl, metadata, isLoading, isTransitioning);
    }
  }, [displayParams, imageUrl, metadata, isLoading, isTransitioning, attemptMetadataExtraction]);

  // Reset metadata extraction flag when image URL changes
  useEffect(() => {
    if (!mountedRef.current) return;
    resetMetadataExtractionFlag();
  }, [imageUrl, resetMetadataExtractionFlag]);

  // Process caption based on metadata
  useCaptionProcessor(previewParams, metadata, imageUrl, setProcessedCaption);

  // Load debug files if in debug mode
  useDebugFiles(displayParams.debugMode, setOutputFiles);

  // Set up image polling if output parameter is present
  const { handleManualCheck: imagePollerHandleManualCheck, isChecking, isLoadingMetadata } = displayParams.output 
    ? useImagePoller(
        displayParams,
        imageUrl,
        isLoading,
        isTransitioning,
        loadNewImage,
        checkImageModified,
        extractMetadataFromImage
      )
    : { handleManualCheck: null, isChecking: false, isLoadingMetadata: false };

  // Handle image loading errors
  const { handleImageError } = useImageErrorHandler(imageUrl, mountedRef);

  // Enhanced manual check handling
  const { handleManualCheck } = useEnhancedManualCheck(
    mountedRef,
    imageUrl,
    imagePollerHandleManualCheck,
    originalHandleManualCheck,
    displayParams,
    extractMetadataFromImage
  );

  // Sync preview params with display params
  useEffect(() => {
    if (!mountedRef.current) return;
    setPreviewParams(displayParams);
  }, [displayParams]);

  // Handle initial render with output parameter
  useEffect(() => {
    if (initialRenderRef.current && displayParams.output) {
      console.log('[useDisplayPage] Initial render with output param:', displayParams.output);
      initialRenderRef.current = false;
      
      setTimeout(() => {
        if (mountedRef.current) {
          if (displayParams.output && displayParams.output.startsWith('http')) {
            console.log('[useDisplayPage] Initial render - setting direct URL:', displayParams.output);
            setImageUrl(displayParams.output);
          } else if (displayParams.output) {
            const normalizedPath = normalizePathForDisplay(displayParams.output);
            console.log('[useDisplayPage] Initial render - setting normalized path:', normalizedPath);
            setImageUrl(normalizedPath);
          }
          setImageKey(prev => prev + 1);
        }
      }, 100);
    }
  }, [displayParams.output, setImageUrl, setImageKey]);

  // Return all necessary state and functions
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
    handleManualCheck,
    getImagePositionStyle,
    handleImageError,
    redirectToDebugMode,
    isChecking,
    isLoadingMetadata
  };
};
