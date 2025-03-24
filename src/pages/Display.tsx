
import React from 'react';
import { useDisplayPage } from '@/components/display/hooks/useDisplayPage';
import { DisplayContainer } from '@/components/display/DisplayContainer';
import { DisplayMode } from '@/components/display/DisplayMode';
import { ErrorMessage } from '@/components/display/ErrorMessage';

const Display = () => {
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
    handleManualCheck,
    getImagePositionStyle,
    handleImageError
  } = useDisplayPage();

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
      />
    </DisplayContainer>
  );
};

export default Display;
