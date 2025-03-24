
import React, { useState, useEffect, useRef } from 'react';
import { DisplayParams } from './types';
import { useNavigate } from 'react-router-dom';
import { createUrlWithParams } from './utils';
import { CaptionRenderer } from './debug/CaptionRenderer';
import { toast } from 'sonner';

interface ImageDisplayProps {
  params: DisplayParams;
  imageUrl: string | null;
  imageKey: number;
  imageStyle: React.CSSProperties;
  processedCaption: string | null;
  metadata: Record<string, string>;
  isTransitioning: boolean;
  oldImageUrl: string | null;
  oldImageStyle: React.CSSProperties;
  newImageStyle: React.CSSProperties;
  imageRef: React.RefObject<HTMLImageElement>;
  onImageError: () => void;
}

export const ImageDisplay: React.FC<ImageDisplayProps> = ({
  params,
  imageUrl,
  imageKey,
  imageStyle,
  processedCaption,
  metadata,
  isTransitioning,
  oldImageUrl,
  oldImageStyle,
  newImageStyle,
  imageRef,
  onImageError
}) => {
  const navigate = useNavigate();
  const [containerSize, setContainerSize] = useState({ width: window.innerWidth, height: window.innerHeight });
  const [doubleClickAttempted, setDoubleClickAttempted] = useState(false);
  const doubleClickTimeoutRef = useRef<number | null>(null);
  const [hasLoadError, setHasLoadError] = useState(false);
  const [isLoading, setIsLoading] = useState(imageUrl !== null);

  // Debug when image URL changes
  useEffect(() => {
    console.log('[ImageDisplay] Image URL changed:', imageUrl);
    console.log('[ImageDisplay] Image Key:', imageKey);
    
    // Reset error state when image URL changes
    if (imageUrl) {
      setHasLoadError(false);
      setIsLoading(true);
    } else {
      setIsLoading(false);
    }
  }, [imageUrl, imageKey]);

  // Update container size on window resize
  useEffect(() => {
    const handleResize = () => {
      setContainerSize({
        width: window.innerWidth,
        height: window.innerHeight
      });
    };
    
    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
      // Clear any pending timeouts when component unmounts
      if (doubleClickTimeoutRef.current) {
        window.clearTimeout(doubleClickTimeoutRef.current);
      }
    };
  }, []);

  const handleDoubleClick = (e: React.MouseEvent) => {
    // Prevent event bubbling and default behavior
    e.stopPropagation();
    e.preventDefault();
    
    // Prevent duplicate navigation attempts
    if (doubleClickAttempted) return;
    setDoubleClickAttempted(true);
    
    // Log that we're trying to navigate to debug mode
    console.log('[ImageDisplay] Double-click detected, navigating to debug mode');
    
    // Create URL with existing params plus debug mode
    const newParams = { ...params, debugMode: true };
    const debugUrl = createUrlWithParams(newParams);
    
    // Navigate to the debug URL
    console.log('[ImageDisplay] Navigating to:', debugUrl);
    
    // Force a small delay to prevent race conditions
    setTimeout(() => {
      navigate(debugUrl);
      
      // Reset the flag after navigation (in case component doesn't unmount)
      setTimeout(() => {
        setDoubleClickAttempted(false);
      }, 1000);
    }, 10);
  };

  // Handle successful image load
  const handleImageLoad = () => {
    console.log('[ImageDisplay] Image loaded successfully:', imageUrl);
    setIsLoading(false);
    setHasLoadError(false);
  };

  // Handle image error with more details
  const handleImageLoadError = (e: React.SyntheticEvent<HTMLImageElement>) => {
    console.error('[ImageDisplay] Image failed to load:', imageUrl);
    setHasLoadError(true);
    setIsLoading(false);
    
    // Show toast with more info
    toast.error(`Failed to load image: ${imageUrl?.split('/').pop() || 'unknown'}`);
    
    // Try to get more details about the error
    if (imageUrl) {
      // Verify if the URL path is correctly formatted
      console.log('[ImageDisplay] Checking URL format:', imageUrl);
      
      // For relative paths, make sure they start with /
      const formattedPath = imageUrl.startsWith('/') ? imageUrl : `/${imageUrl}`;
      console.log('[ImageDisplay] Formatted path for fetch:', formattedPath);
      
      fetch(formattedPath, { method: 'HEAD' })
        .then(response => {
          console.log('[ImageDisplay] Image HEAD request status:', response.status);
          if (!response.ok) {
            toast.error(`Image not found (${response.status})`);
          }
        })
        .catch(err => {
          console.error('[ImageDisplay] Network error checking image:', err);
          toast.error(`Network error: ${err.message}`);
        });
    }
    
    // Call the original error handler
    onImageError();
  };

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

  // Loading indicator styles
  const loadingStyle: React.CSSProperties = {
    position: 'absolute',
    top: '50%',
    left: '50%',
    transform: 'translate(-50%, -50%)',
    fontSize: '1.2rem',
    color: '#666',
    zIndex: 5,
  };

  // Get dimensions for scaling captions appropriately
  const imageDimensions = {
    width: imageRef.current?.naturalWidth || 0,
    height: imageRef.current?.naturalHeight || 0
  };
  
  // Fix background color handling - ensure it has proper format
  const captionBgColor = params.captionBgColor || '#000000';
  const formattedBgColor = captionBgColor.startsWith('#') ? captionBgColor : `#${captionBgColor}`;

  return (
    <div 
      style={{ position: 'relative', width: '100%', height: '100%' }}
      onDoubleClick={handleDoubleClick}
    >
      {imageUrl ? (
        <>
          {isLoading && !hasLoadError && (
            <div style={loadingStyle}>Loading image...</div>
          )}
          
          <img
            key={imageKey}
            ref={imageRef}
            src={imageUrl}
            alt=""
            style={isTransitioning ? newImageStyle : imageStyle}
            onError={handleImageLoadError}
            onLoad={handleImageLoad}
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
            <CaptionRenderer
              caption={processedCaption}
              position={params.captionPosition || 'bottom-center'}
              fontSize={params.captionSize || '16px'}
              color={params.captionColor || 'ffffff'}
              fontFamily={params.captionFont || 'Arial, sans-serif'}
              backgroundColor={formattedBgColor}
              backgroundOpacity={params.captionBgOpacity !== undefined ? params.captionBgOpacity : 0.7}
              containerWidth={containerSize.width}
              screenWidth={window.innerWidth}
              screenSize={imageDimensions}
            />
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
      ) : (
        <div 
          style={{ 
            display: 'flex', 
            alignItems: 'center', 
            justifyContent: 'center', 
            width: '100%', 
            height: '100%',
            color: '#666',
            flexDirection: 'column',
            textAlign: 'center',
            padding: '20px'
          }}
        >
          <div style={{ marginBottom: '10px', fontSize: '1.5rem' }}>
            {hasLoadError ? 'Failed to load image' : 'No image to display'}
          </div>
          {hasLoadError && imageUrl && (
            <div style={{ fontSize: '0.9rem', maxWidth: '80%', wordBreak: 'break-all' }}>
              Attempted to load: {imageUrl}
            </div>
          )}
        </div>
      )}
    </div>
  );
};
