
import React, { useState, useEffect, useRef } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import { DebugPanel } from '@/components/display/DebugPanel';
import { DebugImageContainer } from '@/components/display/DebugImageContainer';
import { ErrorMessage } from '@/components/display/ErrorMessage';
import { processOutputParam, fetchOutputFiles, getNextCheckTime, extractImageMetadata, processCaptionWithMetadata } from '@/components/display/utils';
import { DisplayParams, ShowMode, PositionMode, CaptionPosition, TransitionType } from '@/components/display/types';
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
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [processedCaption, setProcessedCaption] = useState<string | null>(null);
  const [isTransitioning, setIsTransitioning] = useState<boolean>(false);
  const [oldImageUrl, setOldImageUrl] = useState<string | null>(null);
  const [oldImageStyle, setOldImageStyle] = useState<React.CSSProperties>({});
  const [newImageStyle, setNewImageStyle] = useState<React.CSSProperties>({});
  
  const imageRef = useRef<HTMLImageElement | null>(null);
  const lastModifiedRef = useRef<string | null>(null);
  const intervalRef = useRef<number | null>(null);
  const preloadImageRef = useRef<HTMLImageElement | null>(null);
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
    transition: searchParams.get('transition') as TransitionType || 'cut',
  };

  const loadNewImage = (url: string) => {
    if (params.transition === 'cut' || !imageUrl) {
      // For immediate transitions or first load
      setImageUrl(url);
      setImageKey(prev => prev + 1);
      setImageChanged(false);
    } else {
      // For fade transitions, preload the image first
      setIsLoading(true);
      setOldImageUrl(imageUrl);
      
      // Create a new image element to preload
      const preloadImg = new Image();
      preloadImg.onload = () => {
        // Once loaded, start transition
        setImageUrl(url);
        setImageKey(prev => prev + 1);
        
        // Set transition styles
        const duration = params.transition === 'fade-fast' ? 1 : 10;
        setOldImageStyle({
          position: 'absolute',
          transition: `opacity ${duration}s ease`,
          opacity: 1,
          zIndex: 2,
          ...getImagePositionStyle(params.position, params.showMode)
        });
        
        setNewImageStyle({
          ...getImagePositionStyle(params.position, params.showMode),
          opacity: 0,
          zIndex: 1
        });
        
        // Start transition
        setIsTransitioning(true);
        
        // After a small delay to ensure new image is rendered
        setTimeout(() => {
          setOldImageStyle(prev => ({
            ...prev,
            opacity: 0
          }));
          
          setNewImageStyle(prev => ({
            ...prev,
            opacity: 1,
            transition: `opacity ${duration}s ease`
          }));
          
          // End transition after duration
          setTimeout(() => {
            setIsTransitioning(false);
            setOldImageUrl(null);
            setImageChanged(false);
          }, duration * 1000);
        }, 50);
        
        setIsLoading(false);
      };
      
      preloadImg.onerror = () => {
        // If preload fails, fall back to immediate transition
        setImageUrl(url);
        setImageKey(prev => prev + 1);
        setIsLoading(false);
        setImageChanged(false);
      };
      
      preloadImg.src = url;
      preloadImageRef.current = preloadImg;
    }
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
          
          // If image has changed, load new metadata first and then load the new image
          if (params.data !== undefined && url) {
            const newMetadata = await extractImageMetadata(url, params.data || undefined);
            setMetadata(newMetadata);
            
            // Update processed caption with new metadata
            if (params.caption) {
              const newCaption = processCaptionWithMetadata(params.caption, newMetadata);
              setProcessedCaption(newCaption);
            }
          }
          
          // Load the new image
          loadNewImage(url);
        }
        
        lastModifiedRef.current = lastModified;
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
      if (!isTransitioning) {
        loadNewImage(processedUrl);
      }
      
      // Set up periodic checking for image changes
      intervalRef.current = window.setInterval(() => {
        if (processedUrl && !isLoading && !isTransitioning) {
          checkImageModified(processedUrl);
        }
      }, params.refreshInterval * 1000);

      // Extract metadata if data parameter is provided
      if (params.data !== undefined) {
        extractImageMetadata(processedUrl, params.data || undefined)
          .then(data => {
            setMetadata(data);
            
            // Update processed caption with metadata
            if (params.caption) {
              const newCaption = processCaptionWithMetadata(params.caption, data);
              setProcessedCaption(newCaption);
            }
          })
          .catch(err => console.error('Error extracting metadata:', err));
      } else if (params.caption) {
        // If there's a caption but no metadata, just use the caption as is
        setProcessedCaption(params.caption);
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
  }, [params.output, params.refreshInterval, params.debugMode, params.data, params.caption]);

  const handleImageError = () => {
    console.error('Failed to load image:', imageUrl);
  };

  const getImagePositionStyle = (position: PositionMode, showMode: ShowMode): React.CSSProperties => {
    // Base styles based on show mode
    const baseStyle: React.CSSProperties = (() => {
      switch (showMode) {
        case 'fill':
          return {
            width: '100%',
            height: '100%',
            objectFit: 'cover',
          };
        case 'fit':
          return {
            maxWidth: '100%',
            maxHeight: '100%',
            objectFit: 'contain',
          };
        case 'stretch':
          return {
            width: '100%',
            height: '100%',
            objectFit: 'fill',
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
    
    // Add position styles
    const positionStyle: React.CSSProperties = { position: 'absolute' };
    
    switch (position) {
      case 'top-left':
        return { ...baseStyle, ...positionStyle, top: 0, left: 0 };
      case 'top-center':
        return { ...baseStyle, ...positionStyle, top: 0, left: '50%', transform: 'translateX(-50%)' };
      case 'top-right':
        return { ...baseStyle, ...positionStyle, top: 0, right: 0 };
      case 'center-left':
        return { ...baseStyle, ...positionStyle, top: '50%', left: 0, transform: 'translateY(-50%)' };
      case 'center':
        return { ...baseStyle, ...positionStyle, top: '50%', left: '50%', transform: 'translate(-50%, -50%)' };
      case 'center-right':
        return { ...baseStyle, ...positionStyle, top: '50%', right: 0, transform: 'translateY(-50%)' };
      case 'bottom-left':
        return { ...baseStyle, ...positionStyle, bottom: 0, left: 0 };
      case 'bottom-center':
        return { ...baseStyle, ...positionStyle, bottom: 0, left: '50%', transform: 'translateX(-50%)' };
      case 'bottom-right':
        return { ...baseStyle, ...positionStyle, bottom: 0, right: 0 };
      default:
        return { ...baseStyle, ...positionStyle, top: '50%', left: '50%', transform: 'translate(-50%, -50%)' };
    }
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

  const imageStyle = getImagePositionStyle(params.position, params.showMode);

  // Caption styles
  const captionStyle: React.CSSProperties = (() => {
    if (!processedCaption) return {};

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
      whiteSpace: processedCaption.includes('\n') ? 'pre-line' : 'normal',
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
          {/* Current image */}
          {imageUrl && (
            <>
              <img
                key={imageKey}
                ref={imageRef}
                src={imageUrl}
                alt=""
                style={isTransitioning ? newImageStyle : imageStyle}
                onError={handleImageError}
              />
              
              {/* Transitioning old image (for fades) */}
              {isTransitioning && oldImageUrl && (
                <img
                  src={oldImageUrl}
                  alt=""
                  style={oldImageStyle}
                />
              )}
              
              {processedCaption && (
                <div style={captionStyle}>
                  {processedCaption}
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
