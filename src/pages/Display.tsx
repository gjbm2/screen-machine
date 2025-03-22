
import React, { useState, useEffect, useRef } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import { DebugPanel } from '@/components/display/DebugPanel';
import { DebugImageContainer } from '@/components/display/DebugImageContainer';
import { ErrorMessage } from '@/components/display/ErrorMessage';
import { processOutputParam, fetchOutputFiles, getNextCheckTime, extractImageMetadata } from '@/components/display/utils';
import { DisplayParams, ShowMode, PositionMode, CaptionPosition } from '@/components/display/types';
import { toast } from 'sonner';

const Display = () => {
  const [searchParams] = useSearchParams();
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [imageKey, setImageKey] = useState<number>(0);
  const [lastModified, setLastModified] = useState<string | null>(null);
  const [lastChecked, setLastChecked] = useState<Date | null>(null);
  const [outputFiles, setOutputFiles] = useState<string[]>([]);
  const [imageChanged, setImageChanged] = useState<boolean>(false);
  const [metadata, setMetadata] = useState<Record<string, string>>({});
  const imageRef = useRef<HTMLImageElement | null>(null);
  const lastModifiedRef = useRef<string | null>(null);
  const intervalRef = useRef<number | null>(null);
  const navigate = useNavigate();

  const params: DisplayParams = {
    output: searchParams.get('output'),
    showMode: (searchParams.get('show') || 'fit') as ShowMode,
    position: (searchParams.get('position') || 'center') as PositionMode,
    refreshInterval: Number(searchParams.get('refresh') || '5'),
    backgroundColor: searchParams.get('background') || '000000',
    debugMode: searchParams.get('debug') === 'true',
    data: searchParams.has('data') ? searchParams.get('data') : undefined,
    caption: searchParams.get('caption'),
    captionPosition: searchParams.get('caption-position') as CaptionPosition || 'bottom-center',
    captionSize: searchParams.get('caption-size') || '16px',
    captionColor: searchParams.get('caption-color') || 'ffffff',
    captionFont: searchParams.get('caption-font') || 'Arial, sans-serif',
  };

  const checkImageModified = async (url: string) => {
    try {
      setLastChecked(new Date());
      
      const response = await fetch(url, { method: 'HEAD' });
      const lastModified = response.headers.get('last-modified');
      
      setLastModified(lastModified);
      
      if (lastModified && lastModified !== lastModifiedRef.current) {
        console.log('Image modified, updating from:', lastModifiedRef.current, 'to:', lastModified);
        
        if (lastModifiedRef.current !== null) {
          setImageChanged(true);
          if (params.debugMode) {
            toast.info("Image has been updated on the server");
          }
        }
        
        lastModifiedRef.current = lastModified;
        setImageKey(prev => prev + 1);
      }
    } catch (err) {
      console.error('Error checking image modification:', err);
    }
  };

  const handleManualCheck = async () => {
    if (imageUrl) {
      setImageChanged(false);
      await checkImageModified(imageUrl);
      if (!imageChanged) {
        toast.info("Image has not changed since last check");
      }
    } else {
      toast.error("No image URL to check");
    }
  };

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
      setImageUrl(processedUrl);
      
      checkImageModified(processedUrl);
      
      // Set up periodic checking for image changes
      intervalRef.current = window.setInterval(() => {
        if (processedUrl) {
          checkImageModified(processedUrl);
        }
      }, params.refreshInterval * 1000);

      // Extract metadata if data parameter is provided
      if (params.data !== undefined) {
        extractImageMetadata(processedUrl, params.data || undefined)
          .then(data => setMetadata(data))
          .catch(err => console.error('Error extracting metadata:', err));
      }
    }

    if (params.debugMode) {
      fetchOutputFiles().then(files => setOutputFiles(files));
    }

    return () => {
      if (intervalRef.current) {
        window.clearInterval(intervalRef.current);
      }
    };
  }, [params.output, params.refreshInterval, params.debugMode, params.data]);

  const handleImageError = () => {
    console.error('Failed to load image:', imageUrl);
  };

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

  const imageStyle: React.CSSProperties = (() => {
    // Base styles
    const styles: React.CSSProperties = {
      position: 'absolute', 
    };

    // Position styles
    switch (params.position) {
      case 'top-left':
        styles.top = 0;
        styles.left = 0;
        break;
      case 'top-center':
        styles.top = 0;
        styles.left = '50%';
        styles.transform = 'translateX(-50%)';
        break;
      case 'top-right':
        styles.top = 0;
        styles.right = 0;
        break;
      case 'center-left':
        styles.top = '50%';
        styles.left = 0;
        styles.transform = 'translateY(-50%)';
        break;
      case 'center':
        styles.top = '50%';
        styles.left = '50%';
        styles.transform = 'translate(-50%, -50%)';
        break;
      case 'center-right':
        styles.top = '50%';
        styles.right = 0;
        styles.transform = 'translateY(-50%)';
        break;
      case 'bottom-left':
        styles.bottom = 0;
        styles.left = 0;
        break;
      case 'bottom-center':
        styles.bottom = 0;
        styles.left = '50%';
        styles.transform = 'translateX(-50%)';
        break;
      case 'bottom-right':
        styles.bottom = 0;
        styles.right = 0;
        break;
      default:
        styles.top = '50%';
        styles.left = '50%';
        styles.transform = 'translate(-50%, -50%)';
    }

    // Show mode styles
    switch (params.showMode) {
      case 'fill':
        styles.width = '100%';
        styles.height = '100%';
        styles.objectFit = 'cover';
        break;
      case 'fit':
        styles.maxWidth = '100%';
        styles.maxHeight = '100%';
        styles.objectFit = 'contain';
        break;
      case 'stretch':
        styles.width = '100%';
        styles.height = '100%';
        styles.objectFit = 'fill';
        break;
      case 'actual':
        styles.width = 'auto';
        styles.height = 'auto';
        styles.objectFit = 'none';
        break;
      default:
        styles.maxWidth = '100%';
        styles.maxHeight = '100%';
        styles.objectFit = 'contain';
    }

    return styles;
  })();

  // Caption styles
  const captionStyle: React.CSSProperties = (() => {
    if (!params.caption) return {};

    const styles: React.CSSProperties = {
      position: 'absolute',
      padding: '8px 16px',
      backgroundColor: 'rgba(0, 0, 0, 0.7)',
      color: `#${params.captionColor}`,
      fontSize: params.captionSize,
      fontFamily: params.captionFont,
      maxWidth: '80%',
      textAlign: 'center',
      borderRadius: '4px',
      zIndex: 10,
    };

    switch (params.captionPosition) {
      case 'top-left':
        styles.top = '20px';
        styles.left = '20px';
        break;
      case 'top-center':
        styles.top = '20px';
        styles.left = '50%';
        styles.transform = 'translateX(-50%)';
        break;
      case 'top-right':
        styles.top = '20px';
        styles.right = '20px';
        break;
      case 'bottom-left':
        styles.bottom = '20px';
        styles.left = '20px';
        break;
      case 'bottom-center':
        styles.bottom = '20px';
        styles.left = '50%';
        styles.transform = 'translateX(-50%)';
        break;
      case 'bottom-right':
        styles.bottom = '20px';
        styles.right = '20px';
        break;
      default:
        styles.bottom = '20px';
        styles.left = '50%';
        styles.transform = 'translateX(-50%)';
    }

    return styles;
  })();

  // Metadata display styles
  const metadataStyle: React.CSSProperties = {
    position: 'absolute',
    padding: '12px',
    backgroundColor: 'rgba(0, 0, 0, 0.7)',
    color: '#ffffff',
    fontFamily: 'monospace',
    fontSize: '14px',
    maxWidth: '350px',
    borderRadius: '4px',
    top: '20px',
    left: '20px',
    zIndex: 10,
    overflowY: 'auto',
    maxHeight: '80vh',
  };

  const nextCheckTime = getNextCheckTime(lastChecked, params.refreshInterval);

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
        <div style={{ position: 'relative', width: '100%', height: '100%' }}>
          {imageUrl && (
            <>
              <img
                key={imageKey}
                ref={imageRef}
                src={imageUrl}
                alt=""
                style={imageStyle}
                onError={handleImageError}
              />
              
              {params.caption && (
                <div style={captionStyle}>
                  {params.caption}
                </div>
              )}
              
              {params.data !== undefined && Object.keys(metadata).length > 0 && (
                <div style={metadataStyle}>
                  {Object.entries(metadata).map(([key, value]) => (
                    <div key={key} style={{ margin: '4px 0' }}>
                      <strong>{key}:</strong> {value}
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
};

export default Display;
