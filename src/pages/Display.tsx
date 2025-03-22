import React, { useState, useEffect, useRef } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import { DebugPanel } from '@/components/display/DebugPanel';
import { DebugImageContainer } from '@/components/display/DebugImageContainer';
import { ErrorMessage } from '@/components/display/ErrorMessage';
import { processOutputParam, fetchOutputFiles, getNextCheckTime } from '@/components/display/utils';
import { DisplayParams, ShowMode } from '@/components/display/types';

const Display = () => {
  const [searchParams] = useSearchParams();
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [imageKey, setImageKey] = useState<number>(0);
  const [lastModified, setLastModified] = useState<string | null>(null);
  const [lastChecked, setLastChecked] = useState<Date | null>(null);
  const [outputFiles, setOutputFiles] = useState<string[]>([]);
  const imageRef = useRef<HTMLImageElement | null>(null);
  const lastModifiedRef = useRef<string | null>(null);
  const intervalRef = useRef<number | null>(null);

  // Parse URL parameters
  const params: DisplayParams = {
    output: searchParams.get('output'),
    showMode: (searchParams.get('show') || 'fit') as ShowMode,
    refreshInterval: Number(searchParams.get('refresh') || '5'),
    backgroundColor: searchParams.get('background') || '000000',
    debugMode: searchParams.get('debug') === 'true',
  };

  // Function to check if the image has been modified
  const checkImageModified = async (url: string) => {
    try {
      // Record the check time
      setLastChecked(new Date());
      
      const response = await fetch(url, { method: 'HEAD' });
      const lastModified = response.headers.get('last-modified');
      
      // Update the last modified timestamp for debugging
      setLastModified(lastModified);
      
      // Only update the image if the last-modified header has changed
      if (lastModified && lastModified !== lastModifiedRef.current) {
        console.log('Image modified, updating from:', lastModifiedRef.current, 'to:', lastModified);
        lastModifiedRef.current = lastModified;
        setImageKey(prev => prev + 1);
      }
    } catch (err) {
      // Silent fail - continue showing the current image
      console.error('Error checking image modification:', err);
    }
  };

  // Initialize on first render
  useEffect(() => {
    if (!params.output) {
      setError('Error: "output" parameter is required. Usage: /display?output=image.jpg&show=fit&refresh=5&background=000000&debug=false');
      return;
    }

    const processedUrl = processOutputParam(params.output);
    if (processedUrl) {
      setImageUrl(processedUrl);
      
      // Initial check for last-modified
      checkImageModified(processedUrl);
      
      // Set up periodic checking for image changes
      intervalRef.current = window.setInterval(() => {
        if (processedUrl) {
          checkImageModified(processedUrl);
        }
      }, params.refreshInterval * 1000);
    }

    // Fetch available output files if in debug mode
    if (params.debugMode) {
      fetchOutputFiles().then(files => setOutputFiles(files));
    }

    return () => {
      if (intervalRef.current) {
        window.clearInterval(intervalRef.current);
      }
    };
  }, [params.output, params.refreshInterval, params.debugMode]);

  // Helper to handle image errors
  const handleImageError = () => {
    // Don't set error state, just log - we want to keep showing the last successful image
    console.error('Failed to load image:', imageUrl);
  };

  // Generate styles based on parameters
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
  };

  const imageStyle: React.CSSProperties = (() => {
    switch (params.showMode) {
      case 'fill':
        return {
          width: '100%',
          height: '100%',
          objectFit: 'cover',
          position: 'absolute',
          top: 0,
          left: 0,
        };
      case 'fit':
        return {
          maxWidth: '100%',
          maxHeight: '100%',
          objectFit: 'contain',
        };
      case 'actual':
        return {
          width: 'auto',
          height: 'auto',
          objectFit: 'none',
        };
      default:
        return {
          maxWidth: '100%',
          maxHeight: '100%',
          objectFit: 'contain',
        };
    }
  })();

  // If there's no output parameter, show error message
  if (error) {
    return <ErrorMessage error={error} backgroundColor={params.backgroundColor} />;
  }

  // Calculate next check time for debug display
  const nextCheckTime = getNextCheckTime(lastChecked, params.refreshInterval);

  return (
    <div style={containerStyle}>
      {/* Debug mode */}
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
          />
          <DebugImageContainer 
            imageUrl={imageUrl}
            imageKey={imageKey}
            showMode={params.showMode}
            backgroundColor={params.backgroundColor}
            onImageError={handleImageError}
            imageRef={imageRef}
          />
        </>
      ) : (
        <>
          {imageUrl && (
            <img
              key={imageKey}
              ref={imageRef}
              src={imageUrl}
              alt=""
              style={imageStyle}
              onError={handleImageError}
            />
          )}
        </>
      )}
    </div>
  );
};

export default Display;
