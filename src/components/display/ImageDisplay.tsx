
import React, { useState, useEffect, useRef } from 'react';
import { DisplayParams } from './types';
import { toast } from 'sonner';
import { CaptionRenderer } from './debug/CaptionRenderer';

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
  isLoadingMetadata?: boolean;
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
  onImageError,
  isLoadingMetadata = false
}) => {
  const [containerSize, setContainerSize] = useState({ width: window.innerWidth, height: window.innerHeight });
  const [doubleClickAttempted, setDoubleClickAttempted] = useState(false);
  const doubleClickTimeoutRef = useRef<number | null>(null);
  const [hasLoadError, setHasLoadError] = useState(false);

  // Log the image URL for debugging
  useEffect(() => {
    console.log('[ImageDisplay] Current image URL:', imageUrl);
    console.log('[ImageDisplay] Is loading metadata:', isLoadingMetadata);
    console.log('[ImageDisplay] Processed caption:', processedCaption);
    console.log('[ImageDisplay] Metadata available:', Object.keys(metadata).length > 0);
  }, [imageUrl, isLoadingMetadata, processedCaption, metadata]);

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
      if (doubleClickTimeoutRef.current) {
        window.clearTimeout(doubleClickTimeoutRef.current);
      }
    };
  }, []);

  const handleDoubleClick = (e: React.MouseEvent) => {
    // Prevent event bubbling and default behavior
    e.stopPropagation();
    e.preventDefault();
    
    console.log('[ImageDisplay] Double-click detected, navigating to debug mode');
    
    // Get current URL and add debug=true parameter
    const currentUrl = window.location.href;
    
    let newUrl;
    if (currentUrl.includes('debug=')) {
      // Already has debug parameter, don't add it again
      newUrl = currentUrl;
    } else {
      // Add debug=true parameter
      const hasParams = currentUrl.includes('?');
      newUrl = currentUrl + (hasParams ? '&' : '?') + 'debug=true';
    }
    
    // Use window.location.href to ensure a full page reload
    window.location.href = newUrl;
    
    // Show a toast message
    toast.success("Debug Mode Activated");
  };

  // Handle image error with more details
  const handleImageLoadError = (e: React.SyntheticEvent<HTMLImageElement>) => {
    console.error('[ImageDisplay] Image failed to load:', imageUrl);
    setHasLoadError(true);
    
    // Show toast with more info
    toast.error(`Failed to load image: ${imageUrl?.split('/').pop() || 'unknown'}`);
    
    // Try to get more details about the error
    if (imageUrl) {
      const isCrossOrigin = imageUrl.startsWith('http') && !imageUrl.startsWith(window.location.origin);
      if (isCrossOrigin) {
        console.warn('[ImageDisplay] Cross-origin image may have CORS restrictions:', imageUrl);
        toast.error("Cross-origin image failed to load. CORS may be restricted.");
      } else {
        fetch(imageUrl, { method: 'HEAD' })
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

  // Get dimensions for scaling captions appropriately
  const imageDimensions = {
    width: imageRef.current?.naturalWidth || 0,
    height: imageRef.current?.naturalHeight || 0
  };
  
  // Fix background color handling - ensure it has proper format
  const captionBgColor = params.captionBgColor || '#000000';
  const formattedBgColor = captionBgColor.startsWith('#') ? captionBgColor : `#${captionBgColor}`;

  // This is the key change - we show the caption if it exists, regardless of isLoadingMetadata
  const shouldShowCaption = processedCaption !== null;
  
  // Debug the caption rendering decision
  useEffect(() => {
    console.log('[ImageDisplay] Should show caption decision:', { 
      processedCaption, 
      shouldShowCaption
    });
  }, [processedCaption, shouldShowCaption]);

  return (
    <div 
      style={{ position: 'relative', width: '100%', height: '100%' }}
      onDoubleClick={handleDoubleClick}
    >
      {imageUrl ? (
        <>
          <img
            key={imageKey}
            ref={imageRef}
            src={imageUrl}
            alt=""
            style={isTransitioning ? newImageStyle : imageStyle}
            onError={handleImageLoadError}
            onLoad={() => console.log('[ImageDisplay] Image loaded successfully:', imageUrl)}
            crossOrigin="anonymous"
          />
          
          {/* Transitioning old image (for fades) */}
          {isTransitioning && oldImageUrl && (
            <img
              src={oldImageUrl}
              alt=""
              style={oldImageStyle}
              crossOrigin="anonymous"
            />
          )}
          
          {shouldShowCaption && (
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
