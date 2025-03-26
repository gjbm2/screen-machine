
import React, { useCallback, useEffect, useRef } from 'react';
import { DisplayContainer } from '@/components/display/DisplayContainer';
import { DisplayMode } from '@/components/display/DisplayMode';
import { ErrorMessage } from '@/components/display/ErrorMessage';
import { useIsMobile } from '@/hooks/use-mobile';
import { useDisplayParams } from '@/components/display/hooks/useDisplayParams';
import { DisplayStateProvider, useDisplayStateContext } from '@/components/display/context/DisplayStateContext';

// Inner component that uses the context
const DisplayContent = () => {
  const isMobile = useIsMobile();
  const logLimiterRef = useRef({ count: 0, lastTime: 0 });
  const rendersRef = useRef(0);
  
  const {
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
    isChecking,
    isLoadingMetadata,
    params
  } = useDisplayStateContext();
  
  // Only log in development mode and limit frequency
  const limitedLog = useCallback((message: string, data?: any) => {
    if (!import.meta.env.DEV) return;
    
    const now = Date.now();
    const timeDiff = now - logLimiterRef.current.lastTime;
    
    // Only log once per 10 seconds and reset counter
    if (timeDiff > 10000) {
      if (logLimiterRef.current.count > 1) {
        console.log(`[Display] Suppressed ${logLimiterRef.current.count - 1} similar log messages in the last 10 seconds`);
      }
      console.log(message, data);
      logLimiterRef.current = { count: 0, lastTime: now };
    } else {
      // Count suppressed logs
      logLimiterRef.current.count++;
    }
  }, []);
  
  // Track renders for debugging
  useEffect(() => {
    if (import.meta.env.DEV) {
      rendersRef.current++;
      
      // Only log every 100 renders to avoid console spam
      if (rendersRef.current % 100 === 0) {
        console.log(`[Display] Component rendered ${rendersRef.current} times`);
      }
    }
  });
  
  // Add controlled debug logging - with proper dependency array
  useEffect(() => {
    limitedLog('[Display] Component rendered with imageUrl:', imageUrl);
    limitedLog('[Display] Is mobile device:', isMobile);
    limitedLog('[Display] Current params:', params);
    // Only re-run when these values change
  }, [imageUrl, isMobile, limitedLog, params]);
  
  if (error) {
    return <ErrorMessage message={error} backgroundColor="#000000" />;
  }
  
  // Pass the actual params from context instead of hardcoded values
  return (
    <DisplayContainer params={params}>
      <DisplayMode 
        params={params}
        previewParams={params}
        imageUrl={imageUrl}
        imageKey={imageKey}
        imageRef={imageRef}
        lastModified={lastModified}
        lastChecked={lastChecked}
        nextCheckTime={nextCheckTime}
        imageChanged={imageChanged}
        outputFiles={outputFiles}
        metadata={metadata}
        processedCaption={processedCaption}
        isTransitioning={isTransitioning}
        oldImageUrl={oldImageUrl}
        oldImageStyle={oldImageStyle}
        newImageStyle={newImageStyle}
        onHandleManualCheck={handleManualCheck}
        onImageError={handleImageError}
        getImagePositionStyle={getImagePositionStyle}
        isChecking={isChecking}
        isLoadingMetadata={isLoadingMetadata}
      />
    </DisplayContainer>
  );
};

// Outer component that provides the context
const Display = () => {
  const { displayParams } = useDisplayParams();
  
  console.log('[Display] Main component rendered with displayParams:', displayParams);
  
  return (
    <DisplayStateProvider params={displayParams}>
      <DisplayContent />
    </DisplayStateProvider>
  );
};

export default Display;
