
import React, { useCallback, useEffect, useRef } from 'react';
import { useDisplayPage } from '@/components/display/hooks/useDisplayPage';
import { DisplayContainer } from '@/components/display/DisplayContainer';
import { DisplayMode } from '@/components/display/DisplayMode';
import { ErrorMessage } from '@/components/display/ErrorMessage';
import { useIsMobile } from '@/hooks/use-mobile';

const Display = () => {
  const isMobile = useIsMobile();
  const logLimiterRef = useRef({ count: 0, lastTime: 0 });
  
  // Custom logging function to limit console output
  const limitedLog = useCallback((message: string, data?: any) => {
    const now = Date.now();
    const timeDiff = now - logLimiterRef.current.lastTime;
    
    // Only log once per second and reset counter
    if (timeDiff > 1000) {
      if (logLimiterRef.current.count > 1) {
        console.log(`[Display] Suppressed ${logLimiterRef.current.count - 1} similar log messages in the last second`);
      }
      console.log(message, data);
      logLimiterRef.current = { count: 0, lastTime: now };
    } else {
      // Count suppressed logs
      logLimiterRef.current.count++;
    }
  }, []);
  
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

  // Add controlled debug logging
  useEffect(() => {
    if (__DEV__) {
      limitedLog('[Display] Component rendered with imageUrl:', imageUrl);
      limitedLog('[Display] Is mobile device:', isMobile);
    }
  }, [imageUrl, isMobile, limitedLog]);

  if (error) {
    return <ErrorMessage message={error} backgroundColor={previewParams.backgroundColor} />;
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
