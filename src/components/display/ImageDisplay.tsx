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
  const [isImageVisible, setIsImageVisible] = useState(false);

  // Log the image URL and transition state for debugging
  useEffect(() => {
    console.log('[DEBUG Main ImageDisplay] Current image URL:', imageUrl);
    console.log('[DEBUG Main ImageDisplay] Image key:', imageKey);
    console.log('[DEBUG Main ImageDisplay] Is transitioning:', isTransitioning);
    console.log('[DEBUG Main ImageDisplay] Is loading metadata:', isLoadingMetadata);
    console.log('[DEBUG Main ImageDisplay] Processed caption:', processedCaption);
    console.log('[DEBUG Main ImageDisplay] Old image URL:', oldImageUrl);
    console.log('[DEBUG Main ImageDisplay] Image styles:', { 
      oldImageStyle: { ...oldImageStyle, opacity: oldImageStyle.opacity }, 
      newImageStyle: { ...newImageStyle, opacity: newImageStyle.opacity },
      regularImageStyle: { ...imageStyle }
    });
    
    // Additional analysis of the image URL
    if (imageUrl) {
      console.log('[DEBUG Main ImageDisplay] Image URL analysis:', {
        length: imageUrl.length,
        startsWithHttp: imageUrl.startsWith('http'),
        startsWithSlash: imageUrl.startsWith('/'),
        endsWithJpgOrPng: imageUrl.endsWith('.jpg') || imageUrl.endsWith('.png') || imageUrl.endsWith('.jpeg')
      });
      
      // Test if the URL is valid
      const img = new Image();
      img.onload = () => console.log('[DEBUG Main ImageDisplay] Test image loaded successfully');
      img.onerror = (e) => console.error('[DEBUG Main ImageDisplay] Test image failed to load:', e);
      img.src = imageUrl;
    }
    
    if (isTransitioning) {
      console.log('[DEBUG Main ImageDisplay] Transition in progress - showing both images with styles');
    } else {
      console.log('[DEBUG Main ImageDisplay] No transition - showing single image with normal style');
    }
    
    console.log('[DEBUG Main ImageDisplay] Metadata available:', Object.keys(metadata).length > 0);
  }, [imageUrl, isTransitioning, isLoadingMetadata, processedCaption, metadata, oldImageUrl, oldImageStyle, newImageStyle, imageStyle, imageKey]);

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

  // Reset image visibility when URL changes
  useEffect(() => {
    if (imageUrl) {
      console.log('[DEBUG Main ImageDisplay] URL changed, resetting image visibility state');
      setIsImageVisible(false);
      setHasLoadError(false);
    }
  }, [imageUrl]);

  // Additional checks for image ref
  useEffect(() => {
    console.log('[DEBUG Main ImageDisplay] Image ref current:', imageRef.current);
    if (imageRef.current) {
      console.log('[DEBUG Main ImageDisplay] Image ref properties:', {
        complete: imageRef.current.complete,
        naturalWidth: imageRef.current.naturalWidth,
        naturalHeight: imageRef.current.naturalHeight
      });
    }
  }, [imageRef.current]);

  const handleDoubleClick = (e: React.MouseEvent) => {
    // Prevent event bubbling and default behavior
    e.stopPropagation();
    e.preventDefault();
    
    console.log('[DEBUG Main ImageDisplay] Double-click detected, navigating to debug mode');
    
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
    console.error('[DEBUG Main ImageDisplay] Image failed to load:', imageUrl);
    console.error('[DEBUG Main ImageDisplay] Error event:', e);
    
    setHasLoadError(true);
    setIsImageVisible(false);
    
    // Additional error information
    if (imageUrl) {
      if (imageUrl.startsWith('http')) {
        console.error('[DEBUG Main ImageDisplay] External URL load failed');
        
        // Test CORS
        fetch(imageUrl, { mode: 'no-cors' })
          .then(() => console.log('[DEBUG Main ImageDisplay] URL exists but may have CORS issues'))
          .catch(err => console.error('[DEBUG Main ImageDisplay] URL fetch failed completely:', err));
      } else {
        console.error('[DEBUG Main ImageDisplay] Local file load failed');
      }
    }
    
    // Show toast with more info
    toast.error(`Failed to load image: ${imageUrl?.split('/').pop() || 'unknown'}`);
    
    // Try to get more details about the error
    if (imageUrl) {
      const isCrossOrigin = imageUrl.startsWith('http') && !imageUrl.startsWith(window.location.origin);
      if (isCrossOrigin) {
        console.warn('[DEBUG Main ImageDisplay] Cross-origin image may have CORS restrictions:', imageUrl);
        toast.error("Cross-origin image failed to load. CORS may be restricted.");
      } else {
        fetch(imageUrl, { method: 'HEAD' })
          .then(response => {
            console.log('[DEBUG Main ImageDisplay] Image HEAD request status:', response.status);
            if (!response.ok) {
              toast.error(`Image not found (${response.status})`);
            }
          })
          .catch(err => {
            console.error('[DEBUG Main ImageDisplay] Network error checking image:', err);
            toast.error(`Network error: ${err.message}`);
          });
      }
    }
    
    // Call the original error handler
    onImageError();
  };

  // Handle successful image load
  const handleImageLoad = (e: React.SyntheticEvent<HTMLImageElement>) => {
    console.log('[DEBUG Main ImageDisplay] Image loaded successfully:', imageUrl);
    console.log('[DEBUG Main ImageDisplay] Image natural dimensions:', {
      width: (e.target as HTMLImageElement).naturalWidth,
      height: (e.target as HTMLImageElement).naturalHeight
    });
    
    setIsImageVisible(true);
    setHasLoadError(false);
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

  // Only show caption if it exists AND we're not transitioning
  const shouldShowCaption = processedCaption !== null && !isTransitioning;
  
  // Debug the caption rendering decision
  useEffect(() => {
    console.log('[DEBUG Main ImageDisplay] Should show caption decision:', { 
      processedCaption, 
      isTransitioning,
      shouldShowCaption
    });
  }, [processedCaption, isTransitioning, shouldShowCaption]);

  return (
    <div 
      style={{ position: 'relative', width: '100%', height: '100%' }}
      onDoubleClick={handleDoubleClick}
    >
      {imageUrl ? (
        <div className="relative w-full h-full">
          {/* Important: Reversed the order - the old image needs to be rendered first */}
          {/* Transitioning old image (for fades) */}
          {isTransitioning && oldImageUrl && (
            <img
              src={oldImageUrl}
              alt=""
              style={oldImageStyle}
              crossOrigin="anonymous"
              onLoad={() => console.log('[DEBUG Main ImageDisplay] Old transition image loaded')}
              onError={() => console.error('[DEBUG Main ImageDisplay] Old transition image failed to load')}
            />
          )}
          
          {/* Main image (or new image during transition) */}
          <img
            key={imageKey}
            ref={imageRef}
            src={imageUrl}
            alt=""
            style={{
              ...isTransitioning ? newImageStyle : imageStyle,
              opacity: isImageVisible ? 1 : 0,
              transition: 'opacity 0.3s ease-in-out'
            }}
            onError={handleImageLoadError}
            onLoad={handleImageLoad}
            crossOrigin="anonymous"
          />
          
          {/* Only show caption when not transitioning */}
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
        </div>
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
