
import React, { useCallback, useEffect, useRef, useMemo } from 'react';
import { useDisplayPage } from '@/components/display/hooks/useDisplayPage';
import { DisplayContainer } from '@/components/display/DisplayContainer';
import { DisplayMode } from '@/components/display/DisplayMode';
import { ErrorMessage } from '@/components/display/ErrorMessage';
import { useIsMobile } from '@/hooks/use-mobile';

const Display = () => {
  const isMobile = useIsMobile();
  const logLimiterRef = useRef({ count: 0, lastTime: 0 });
  const rendersRef = useRef(0);
  
  // Only log in development mode and limit frequency
  const limitedLog = useCallback((message: string, data?: any) => {
    if (!import.meta.env.DEV) return;
    
    const now = Date.now();
    const timeDiff = now - logLimiterRef.current.lastTime;
    
    // Only log once per 5 seconds and reset counter
    if (timeDiff > 5000) {
      if (logLimiterRef.current.count > 1) {
        console.log(`[Display] Suppressed ${logLimiterRef.current.count - 1} similar log messages in the last 5 seconds`);
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
      console.log(`[Display] Component rendered ${rendersRef.current} times`);
    }
  });
  
  const {
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
    isChecking,
    handleManualCheck,
    getImagePositionStyle,
    handleImageError,
    isLoadingMetadata
  } = useDisplayPage();

  // Memoize the error component to prevent recreating it on every render
  const errorComponent = useMemo(() => {
    if (error) {
      return <ErrorMessage message={error} backgroundColor={previewParams.backgroundColor} />;
    }
    return null;
  }, [error, previewParams.backgroundColor]);

  // Add controlled debug logging - with proper dependency array
  useEffect(() => {
    limitedLog('[Display] Component rendered with imageUrl:', imageUrl);
    limitedLog('[Display] Is mobile device:', isMobile);
    // Only re-run when these values change
  }, [imageUrl, isMobile, limitedLog]);

  if (error) {
    return errorComponent;
  }

  return (
    <DisplayContainer params={params}>
      <DisplayMode 
        params={params}
        previewParams={previewParams}
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

export default Display;
