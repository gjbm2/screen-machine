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
  const mountedRef = useRef(true);
  const initialRenderRef = useRef(true);
  const hasProcessedOutputRef = useRef(false);
  const hasCheckedExplicitExitRef = useRef(false);
  const forceViewModeRef = useRef(false);

  const redirectToDebugMode = () => {
    if (forceViewModeRef.current) {
      console.log('[useDisplayPage] Skipping debug mode redirect because forceViewMode is true');
      return;
    }
    updateParam('debug', 'true');
  };

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

  useEffect(() => {
    console.log("[useDisplayPage] Debug mode active:", displayParams.debugMode);
    console.log("[useDisplayPage] ForceViewMode flag:", forceViewModeRef.current);
    console.log("[useDisplayPage] Params:", displayParams);
    console.log("[useDisplayPage] Output param:", displayParams.output);
    
    if (displayParams.output) {
      console.log("[useDisplayPage] ⚠️ Output parameter detected:", displayParams.output);
    }
  }, [displayParams]);

  useOutputProcessor(displayParams);

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

  const { checkDebugRedirection, userExplicitlyExitedDebugRef } = useDebugRedirection(displayParams, redirectToDebugMode);

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

  useEffect(() => {
    if (!mountedRef.current) return;
    
    if (displayParams.output && !hasProcessedOutputRef.current) {
      console.log('[useDisplayPage] Processing output parameter:', displayParams.output);
      hasProcessedOutputRef.current = true;
      
      if (displayParams.output.startsWith('http://') || displayParams.output.startsWith('https://')) {
        console.log('[useDisplayPage] Setting direct URL:', displayParams.output);
        setImageUrl(displayParams.output);
      } else {
        const normalizedPath = normalizePathForDisplay(displayParams.output);
        console.log('[useDisplayPage] Setting normalized path:', normalizedPath);
        setImageUrl(normalizedPath);
      }
      
      setImageKey(prev => prev + 1);
      
      const filename = displayParams.output.split('/').pop() || displayParams.output;
      const displayName = filename.length > 30 ? filename.substring(0, 27) + '...' : filename;
      toast.success(`Displaying image: ${displayName}`);
    }
  }, [displayParams.output, setImageUrl, setImageKey]);

  useEffect(() => {
    hasProcessedOutputRef.current = false;
  }, [location.pathname, location.search]);

  useEffect(() => {
    mountedRef.current = true;
    console.log('[useDisplayPage] Component mounted');
    
    return () => {
      console.log('[useDisplayPage] Component unmounting');
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    if (!mountedRef.current) return;
    if (forceViewModeRef.current || userExplicitlyExitedDebugRef.current) {
      console.log('[useDisplayPage] Skipping debug redirection check - user explicitly exited debug mode or force view mode is set');
    } else if (displayParams.output) {
      checkDebugRedirection();
    } else {
      checkDebugRedirection();
    }
  }, [displayParams, displayParams.output, displayParams.debugMode, userExplicitlyExitedDebugRef]);

  const { 
    attemptMetadataExtraction, 
    resetMetadataExtractionFlag 
  } = useMetadataManager(displayParams, imageUrl, extractMetadataFromImage);

  useEffect(() => {
    if (!mountedRef.current) return;
    
    if (!isLoading && !isTransitioning) {
      attemptMetadataExtraction(imageUrl, metadata, isLoading, isTransitioning);
    }
  }, [displayParams, imageUrl, metadata, isLoading, isTransitioning]);

  useEffect(() => {
    if (!mountedRef.current) return;
    resetMetadataExtractionFlag();
  }, [imageUrl]);

  useCaptionProcessor(previewParams, metadata, imageUrl, setProcessedCaption);

  useDebugFiles(displayParams.debugMode, setOutputFiles);

  const { handleManualCheck: imagePollerHandleManualCheck, isChecking } = displayParams.output 
    ? useImagePoller(
        displayParams,
        imageUrl,
        isLoading,
        isTransitioning,
        loadNewImage,
        checkImageModified,
        extractMetadataFromImage
      )
    : { handleManualCheck: null, isChecking: false };

  const { handleImageError } = useImageErrorHandler(imageUrl, mountedRef);

  const { handleManualCheck } = useEnhancedManualCheck(
    mountedRef,
    imageUrl,
    imagePollerHandleManualCheck,
    originalHandleManualCheck,
    displayParams,
    extractMetadataFromImage
  );

  useEffect(() => {
    if (!mountedRef.current) return;
    setPreviewParams(displayParams);
  }, [displayParams]);

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
    redirectToDebugMode,
    isChecking
  };
};
