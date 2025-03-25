
import React from 'react';
import { useDisplayPage } from '@/components/display/hooks/useDisplayPage';
import { DisplayContainer } from '@/components/display/DisplayContainer';
import { DisplayMode } from '@/components/display/DisplayMode';
import { ErrorMessage } from '@/components/display/ErrorMessage';
import { useIsMobile } from '@/hooks/use-mobile';

const Display = () => {
  const isMobile = useIsMobile();
  
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
    handleImageError
  } = useDisplayPage();

  // Add debug logging to help diagnose metadata issues
  React.useEffect(() => {
    console.log('[Display] Component rendered with imageUrl:', imageUrl);
    console.log('[Display] Metadata:', metadata);
    console.log('[Display] Is mobile device:', isMobile);
    console.log('[Display] Is checking for updates:', isChecking);
    
    // Log metadata object keys and structure
    if (metadata && Object.keys(metadata).length > 0) {
      console.log('[Display] Metadata keys:', Object.keys(metadata));
      console.log('[Display] First few entries:', Object.entries(metadata).slice(0, 5));
    } else {
      console.log('[Display] No metadata available');
    }
  }, [metadata, imageUrl, isMobile, isChecking]);

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
      />
    </DisplayContainer>
  );
};

export default Display;
