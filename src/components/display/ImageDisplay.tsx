import React, { useState, useEffect, useRef } from 'react';
import { DisplayParams } from './types';
import { createUrlWithParams, decodeComplexOutputParam } from './utils';
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
  const [containerSize, setContainerSize] = useState({ width: window.innerWidth, height: window.innerHeight });
  const [doubleClickAttempted, setDoubleClickAttempted] = useState(false);
  const doubleClickTimeoutRef = useRef<number | null>(null);
  const [hasLoadError, setHasLoadError] = useState(false);
  // State to hold the final processed image URL (important for complex URLs)
  const [processedImageUrl, setProcessedImageUrl] = useState<string | null>(null);

  // Process complex URLs to ensure they're fully decoded
  useEffect(() => {
    if (imageUrl) {
      if (imageUrl.includes('?') && (imageUrl.startsWith('http://') || imageUrl.startsWith('https://'))) {
        // For complex URLs, ensure they're fully decoded
        const fullyDecodedUrl = decodeComplexOutputParam(imageUrl);
        console.log('[ImageDisplay] Processed complex URL:', fullyDecodedUrl);
        setProcessedImageUrl(fullyDecodedUrl);
      } else {
        // For simple URLs, use as-is
        setProcessedImageUrl(imageUrl);
      }
      
      // Reset error state when image URL changes
      setHasLoadError(false);
    } else {
      setProcessedImageUrl(null);
    }
  }, [imageUrl]);

  // Debug when image URL changes
  useEffect(() => {
    console.log('[ImageDisplay] Image URL changed:', imageUrl);
    console.log('[ImageDisplay] Processed URL:', processedImageUrl);
    console.log('[ImageDisplay] Image Key:', imageKey);
  }, [imageUrl, processedImageUrl, imageKey]);

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
    
    console.log('[ImageDisplay] Double-click detected, navigating to debug mode');
    
    // Get current URL and add debug=true parameter
    const currentUrl = window.location.href;
    
    let newUrl;
    if (currentUrl.includes('debug=')) {
      // Already has debug parameter, don't add it again
      console.log('[ImageDisplay] URL already has debug parameter, not modifying');
      newUrl = currentUrl;
    } else {
      // Add debug=true parameter
      const hasParams = currentUrl.includes('?');
      newUrl = currentUrl + (hasParams ? '&' : '?') + 'debug=true';
      console.log('[ImageDisplay] Adding debug=true to URL:', newUrl);
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

  return (
    <div 
      style={{ position: 'relative', width: '100%', height: '100%' }}
      onDoubleClick={handleDoubleClick}
    >
      {processedImageUrl ? (
        <>
          <img
            key={imageKey}
            ref={imageRef}
            src={processedImageUrl}
            alt=""
            style={isTransitioning ? newImageStyle : imageStyle}
            onError={handleImageLoadError}
            onLoad={() => console.log('[ImageDisplay] Image loaded successfully:', processedImageUrl)}
            crossOrigin="anonymous" // Add crossOrigin attribute to help with CORS issues
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
