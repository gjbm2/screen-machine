
import React, { useEffect } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import { DebugPanel } from '@/components/display/DebugPanel';
import { DebugImageContainer } from '@/components/display/DebugImageContainer';
import { ErrorMessage } from '@/components/display/ErrorMessage';
import { processOutputParam, fetchOutputFiles, extractImageMetadata, processCaptionWithMetadata } from '@/components/display/utils';
import { DisplayParams } from '@/components/display/types';
import { ImageDisplay } from '@/components/display/ImageDisplay';
import { useDisplayState } from '@/components/display/hooks/useDisplayState';

const Display = () => {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();

  // Parse URL parameters
  const params: DisplayParams = {
    output: searchParams.get('output'),
    showMode: (searchParams.get('show') || 'fit') as DisplayParams['showMode'],
    position: (searchParams.get('position') || 'center') as DisplayParams['position'],
    refreshInterval: Number(searchParams.get('refresh') || '5'),
    backgroundColor: searchParams.get('background') || '000000',
    debugMode: searchParams.get('debug') === 'true',
    data: searchParams.has('data') ? searchParams.get('data') : undefined,
    caption: searchParams.get('caption'),
    captionPosition: searchParams.get('caption-position') as DisplayParams['captionPosition'] || 'bottom-center',
    captionSize: searchParams.get('caption-size') || '16px',
    captionColor: searchParams.get('caption-color') || 'ffffff',
    captionFont: searchParams.get('caption-font') || 'Arial, sans-serif',
    transition: searchParams.get('transition') as DisplayParams['transition'] || 'cut',
  };

  // Use our custom hook for state management
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
  } = useDisplayState(params);

  // Redirect to debug mode if no output parameter is provided
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

  // Handle image loading and metadata extraction
  useEffect(() => {
    if (!params.output && !params.debugMode) {
      return;
    }

    const processedUrl = processOutputParam(params.output);
    if (processedUrl) {
      if (!isTransitioning) {
        loadNewImage(processedUrl);
      }
      
      // Set up periodic checking for image changes
      const intervalId = window.setInterval(() => {
        if (processedUrl && !isLoading && !isTransitioning) {
          checkImageModified(processedUrl);
        }
      }, params.refreshInterval * 1000);

      // Extract metadata if data parameter is provided
      if (params.data !== undefined) {
        extractImageMetadata(processedUrl, params.data || undefined)
          .then(data => {
            // Update processed caption with metadata
            if (params.caption) {
              const newCaption = processCaptionWithMetadata(params.caption, data);
              useDisplayState.setState({ processedCaption: newCaption });
            }
          })
          .catch(err => console.error('Error extracting metadata:', err));
      } else if (params.caption) {
        // If there's a caption but no metadata, just use the caption as is
        useDisplayState.setState({ processedCaption: params.caption });
      }

      return () => {
        window.clearInterval(intervalId);
      };
    }
  }, [params.output, params.refreshInterval, params.debugMode, params.data, params.caption]);

  // Fetch available output files for debug mode
  useEffect(() => {
    if (params.debugMode) {
      fetchOutputFiles().then(files => setOutputFiles(files));
    }
  }, [params.debugMode, setOutputFiles]);

  const handleImageError = () => {
    console.error('Failed to load image:', imageUrl);
  };

  const imageStyle = getImagePositionStyle(params.position, params.showMode);

  const containerStyle: React.CSSProperties = {
    backgroundColor: `#${params.backgroundColor}`,
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

  return (
    <div style={containerStyle}>
      {params.debugMode ? (
        <>
          <DebugPanel 
            params={params}
            imageUrl={imageUrl}
            lastModified={lastModified}
            lastChecked={lastChecked}
            nextCheckTime={nextCheckTime}
            imageKey={imageKey}
            outputFiles={outputFiles}
            imageChanged={imageChanged}
            onCheckNow={handleManualCheck}
          />
          <DebugImageContainer 
            imageUrl={imageUrl}
            imageKey={imageKey}
            showMode={params.showMode}
            position={params.position}
            backgroundColor={params.backgroundColor}
            onImageError={handleImageError}
            imageRef={imageRef}
            imageChanged={imageChanged}
            caption={params.caption}
            captionPosition={params.captionPosition}
            captionSize={params.captionSize}
            captionColor={params.captionColor}
            captionFont={params.captionFont}
            metadata={params.data !== undefined ? metadata : undefined}
          />
        </>
      ) : (
        <ImageDisplay
          params={params}
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
