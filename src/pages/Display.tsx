
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
    nextCheckTime,  // This is now Date | null
    handleManualCheck,
    getImagePositionStyle,
    handleImageError
  } = useDisplayPage();

  // Add debug logging to help diagnose metadata issues
  React.useEffect(() => {
    console.log('Display rendered with metadata:', metadata);
    console.log('Image URL is:', imageUrl);
    console.log('Is mobile device:', isMobile);
  }, [metadata, imageUrl, isMobile]);

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
        nextCheckTime={nextCheckTime}  // This is now Date | null
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
      />
    </DisplayContainer>
  );
};

export default Display;
