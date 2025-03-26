
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
  
  console.log('[useDisplayPage] Initializing with displayParams:', displayParams);
  console.log('[useDisplayPage] Has processed output flag:', hasProcessedOutputRef.current);

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
  const { processedUrl } = useOutputProcessor(displayParams);
  console.log("[useDisplayPage] Processed URL from useOutputProcessor:", processedUrl);

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
    setImageChanged,
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
  
  console.log('[useDisplayPage] Current imageUrl from displayState:', imageUrl);
  console.log('[useDisplayPage] Processed URL from useOutputProcessor:', processedUrl);

  // Update imageUrl when processedUrl changes
  useEffect(() => {
    if (!mountedRef.current) return;
    
    if (processedUrl && processedUrl !== imageUrl) {
      console.log('[useDisplayPage] Setting image URL from processed URL:', processedUrl);
      setImageUrl(processedUrl);
      
      // Update imageKey to force re-render
      setImageKey(prev => {
        const newKey = prev + 1;
        console.log('[useDisplayPage] Incrementing image key to:', newKey);
        return newKey;
      });
      
      // Set flag to prevent reprocessing
      hasProcessedOutputRef.current = true;
      
      // Reset imageChanged flag
      setImageChanged(false);
      
      // Show toast notification
      const filename = processedUrl.split('/').pop() || processedUrl;
      const displayName = filename.length > 30 ? filename.substring(0, 27) + '...' : filename;
      toast.success(`Displaying image: ${displayName}`);
    }
  }, [processedUrl, imageUrl, setImageUrl, setImageKey, setImageChanged]);

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

  // Process output parameter on first detection - keeping for backward compatibility
  useEffect(() => {
    if (!mountedRef.current) return;
    
    console.log('[useDisplayPage] Checking if should process output param:', {
      output: displayParams.output,
      hasProcessed: hasProcessedOutputRef.current,
      locationSearch: location.search
    });
    
    if (displayParams.output && !hasProcessedOutputRef.current && !processedUrl) {
      console.log('[useDisplayPage] Processing output parameter in legacy handler:', displayParams.output);
      
      // Keep this logic for backwards compatibility
      const decodedOutput = decodeComplexOutputParam(displayParams.output);
      console.log('[useDisplayPage] Legacy decoded output:', decodedOutput);
      
      if (decodedOutput) {
        if (decodedOutput.startsWith('http://') || decodedOutput.startsWith('https://')) {
          console.log('[useDisplayPage] Legacy setting direct URL:', decodedOutput);
          setImageUrl(decodedOutput);
        } else {
          const normalizedPath = normalizePathForDisplay(decodedOutput);
          console.log('[useDisplayPage] Legacy setting normalized path:', normalizedPath);
          setImageUrl(normalizedPath);
        }
        
        setImageKey(prev => {
          const newKey = prev + 1;
          console.log('[useDisplayPage] Legacy incrementing image key to:', newKey);
          return newKey;
        });
        
        hasProcessedOutputRef.current = true;
      }
    }
  }, [displayParams.output, processedUrl, setImageUrl, setImageKey, location.search]);

  // Reset output processing flag when URL changes
  useEffect(() => {
    if (location.search !== '') {
      const oldValue = hasProcessedOutputRef.current;
      hasProcessedOutputRef.current = false;
      console.log('[useDisplayPage] Reset hasProcessedOutputRef from', oldValue, 'to false due to URL change');
    }
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

  // Determine if debug redirection should occur
  useEffect(() => {
    if (!mountedRef.current) return;
    if (forceViewModeRef.current || userExplicitlyExitedDebugRef.current) {
      console.log('[useDisplayPage] Skipping debug redirection check - user explicitly exited debug mode or force view mode is set');
    } else {
      // Always check for redirection
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
      
      // Force processing of output parameter on initial render
      if (!hasProcessedOutputRef.current) {
        console.log('[useDisplayPage] Forcing processing of output parameter on initial render');
        setImageUrl(null); // Reset to trigger URL change detection
        setTimeout(() => {
          setImageUrl(processedUrl || displayParams.output);
          setImageKey(prev => prev + 1);
        }, 100);
      }
    }
  }, [displayParams.output, processedUrl, setImageUrl, setImageKey]);

  // Debugging code to force image URL reload when it should be present but isn't
  useEffect(() => {
    if (mountedRef.current && displayParams.output && !imageUrl && !isLoading) {
      console.log('[useDisplayPage] DEBUG: Output param present but imageUrl is null:', {
        output: displayParams.output,
        imageUrl,
        processedUrl,
        hasProcessedOutputRef: hasProcessedOutputRef.current
      });
      
      // If we have output param but no imageUrl, try to force a reload
      if (hasProcessedOutputRef.current) {
        console.log('[useDisplayPage] Forcing hasProcessedOutputRef reset to try loading image again');
        hasProcessedOutputRef.current = false;
      }
    }
  }, [displayParams.output, imageUrl, processedUrl, isLoading]);

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
