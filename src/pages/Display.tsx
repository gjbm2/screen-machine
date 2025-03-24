
import React, { useEffect, useCallback, useState } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import { DebugPanel } from '@/components/display/DebugPanel';
import { DebugImageContainer } from '@/components/display/DebugImageContainer';
import { ErrorMessage } from '@/components/display/ErrorMessage';
import { processOutputParam, fetchOutputFiles, extractImageMetadata, processCaptionWithMetadata } from '@/components/display/utils';
import { DisplayParams } from '@/components/display/types';
import { ImageDisplay } from '@/components/display/ImageDisplay';
import { useDisplayState } from '@/components/display/hooks/useDisplayState';
import { toast } from 'sonner';

const Display = () => {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();

  // Process searchParams, decoding URI components where needed
  const params: DisplayParams = {
    output: searchParams.get('output') ? decodeURIComponent(searchParams.get('output') || '') : null,
    showMode: (searchParams.get('show') || 'fit') as DisplayParams['showMode'],
    position: (searchParams.get('position') || 'center') as DisplayParams['position'],
    refreshInterval: Number(searchParams.get('refresh') || '5'),
    backgroundColor: searchParams.get('background') || '000000',
    debugMode: searchParams.get('debug') === 'true',
    data: searchParams.has('data') ? searchParams.get('data') : undefined,
    caption: searchParams.get('caption') ? decodeURIComponent(searchParams.get('caption') || '') : null,
    captionPosition: searchParams.get('caption-position') as DisplayParams['captionPosition'] || 'bottom-center',
    captionSize: searchParams.get('caption-size') || '16px',
    captionColor: searchParams.get('caption-color') || 'ffffff',
    captionFont: searchParams.get('caption-font') ? decodeURIComponent(searchParams.get('caption-font') || '') : 'Arial, sans-serif',
    transition: searchParams.get('transition') as DisplayParams['transition'] || 'cut',
  };

  // Track preview state
  const [previewParams, setPreviewParams] = useState<DisplayParams>(params);
  
  const {
    imageUrl,
    error,
    imageKey,
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
    handleManualCheck,
    getImagePositionStyle
  } = useDisplayState(previewParams);

  // Redirect to debug mode if no output is specified
  useEffect(() => {
    if (!params.output && !params.debugMode) {
      const queryParams = new URLSearchParams();
      queryParams.set('debug', 'true');
      if (params.showMode) queryParams.set('show', params.showMode);
      if (params.position) queryParams.set('position', params.position);
      if (params.refreshInterval) queryParams.set('refresh', params.refreshInterval.toString());
      if (params.backgroundColor) queryParams.set('background', params.backgroundColor);
      navigate(`/display?${queryParams.toString()}`);
    }
  }, [params.output, params.debugMode, navigate, params.showMode, params.position, params.refreshInterval, params.backgroundColor]);

  // Handle image loading and refresh checking
  useEffect(() => {
    if (!params.output && !params.debugMode) {
      return;
    }

    const processedUrl = processOutputParam(params.output);
    if (processedUrl) {
      // Only load the image if it's not already loading or transitioning
      if (!isTransitioning) {
        loadNewImage(processedUrl);
      }
      
      // Set up periodic checks for image modifications
      const intervalId = window.setInterval(() => {
        if (processedUrl && !isLoading && !isTransitioning) {
          checkImageModified(processedUrl);
        }
      }, params.refreshInterval * 1000);

      // Setup cleanup to clear the interval
      return () => {
        window.clearInterval(intervalId);
      };
    }
  }, [params.output, params.refreshInterval, params.debugMode, isLoading, isTransitioning, loadNewImage, checkImageModified]);

  // Process captions separately to avoid unnecessary metadata extraction
  useEffect(() => {
    if (!imageUrl) return;

    if (previewParams.caption) {
      if (previewParams.data !== undefined) {
        // Ensure this is called only when metadata is already available
        if (Object.keys(metadata).length > 0) {
          const newCaption = processCaptionWithMetadata(previewParams.caption, metadata);
          setProcessedCaption(newCaption);
        }
      } else {
        setProcessedCaption(previewParams.caption);
      }
    } else {
      setProcessedCaption(null);
    }
  }, [previewParams.caption, previewParams.data, metadata, imageUrl, setProcessedCaption]);

  // Fetch output files for debug mode
  useEffect(() => {
    if (params.debugMode) {
      fetchOutputFiles().then(files => setOutputFiles(files));
    }
  }, [params.debugMode, setOutputFiles]);

  // Update preview params from debug panel changes
  useEffect(() => {
    setPreviewParams(params);
  }, [params]);

  const handleImageError = () => {
    console.error('Failed to load image:', imageUrl);
    toast.error("Failed to load image");
  };

  // Calculate image styles with dimensions if available
  const calculateImageStyle = useCallback(() => {
    const containerWidth = window.innerWidth;
    const containerHeight = window.innerHeight;
    let imageWidth = 0;
    let imageHeight = 0;
    
    if (imageRef.current) {
      imageWidth = imageRef.current.naturalWidth;
      imageHeight = imageRef.current.naturalHeight;
    }
    
    return getImagePositionStyle(
      previewParams.position, 
      previewParams.showMode,
      containerWidth,
      containerHeight,
      imageWidth,
      imageHeight
    );
  }, [previewParams.position, previewParams.showMode, getImagePositionStyle, imageRef]);

  const imageStyle = calculateImageStyle();

  const containerStyle: React.CSSProperties = {
    backgroundColor: `#${previewParams.backgroundColor}`,
    width: '100vw',
    height: '100vh',
    margin: 0,
    padding: 0,
    overflow: 'hidden',
    display: 'flex',
    justifyContent: 'center',
    alignItems: 'center',
    position: 'relative',
  };

  if (error) {
    return <ErrorMessage message={error} backgroundColor={previewParams.backgroundColor} />;
  }

  return (
    <div style={containerStyle}>
      {params.debugMode ? (
        <>
          <DebugPanel 
            params={params}
            imageUrl={imageUrl}
            lastModified={lastModified ? lastModified : null}
            lastChecked={lastChecked}
            nextCheckTime={nextCheckTime}
            imageKey={imageKey}
            outputFiles={outputFiles}
            imageChanged={imageChanged}
            onCheckNow={handleManualCheck}
            metadata={metadata}
            onApplyCaption={(caption) => setProcessedCaption(caption)}
          />
          <DebugImageContainer 
            imageUrl={imageUrl}
            imageKey={imageKey}
            showMode={previewParams.showMode}
            position={previewParams.position}
            backgroundColor={previewParams.backgroundColor}
            onImageError={handleImageError}
            imageRef={imageRef}
            imageChanged={imageChanged}
            caption={processedCaption}
            captionPosition={previewParams.captionPosition}
            captionSize={previewParams.captionSize}
            captionColor={previewParams.captionColor}
            captionFont={previewParams.captionFont}
            metadata={metadata}
            onSettingsChange={() => {}} // This is a placeholder for future setting change handlers
          />
        </>
      ) : (
        <ImageDisplay
          params={previewParams}
          imageUrl={imageUrl}
          imageKey={imageKey}
          imageStyle={imageStyle}
          processedCaption={processedCaption}
          metadata={metadata}
          isTransitioning={isTransitioning}
          oldImageUrl={oldImageUrl}
          oldImageStyle={oldImageStyle}
          newImageStyle={newImageStyle}
          imageRef={imageRef}
          onImageError={handleImageError}
        />
      )}
    </div>
  );
};

export default Display;
